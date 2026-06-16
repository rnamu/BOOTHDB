# main.py - FastAPI本体

from fastapi import FastAPI, HTTPException, Depends, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from typing import Optional
import uuid

from config import settings
from models import (
    ProductRegisterRequest, ProductResponse, ProductListResponse,
    PriceHistoryResponse, PriceHistoryItem,
    AvatarResponse, AvatarListResponse,
    ReviewCreateRequest, ReviewResponse, ReviewListResponse, AvatarRatingResponse,
    MessageResponse,
)
from database import (
    get_product_by_booth_id, create_product, get_products,
    add_price_history, get_price_history, get_price_stats,
    get_avatars, get_avatar_by_id, get_products_by_avatar, link_product_avatar,
    create_review, get_reviews, get_review_stats, get_avatar_ratings,
    has_user_reviewed,
)
from scraper import extract_booth_item_id, scrape_booth_item, normalize_booth_url
from scheduler import start_scheduler, stop_scheduler
from auth import get_current_user, get_optional_user


# ==========================================
# アプリ起動・終了処理
# ==========================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時
    start_scheduler()
    yield
    # 終了時
    stop_scheduler()


app = FastAPI(
    title="BOOTHDB API",
    description="VRChat向けBOOTH商品データベースのバックエンドAPI",
    version="1.0.0",
    lifespan=lifespan,
)

# ==========================================
# CORS設定（フロントエンドからのアクセスを許可）
# ==========================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:8080",
        "http://127.0.0.1:5500",
        "https://*.github.io",  # GitHub Pages
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# ヘルスチェック（cron-job.org のping用）
# ==========================================

@app.get("/ping", tags=["システム"])
async def ping():
    """サービス死活監視用エンドポイント"""
    return {"status": "ok"}


@app.get("/", tags=["システム"])
async def root():
    return {"message": "BOOTHDB API", "version": "1.0.0"}


# ==========================================
# 商品API
# ==========================================

@app.post("/api/products/register", response_model=ProductResponse, tags=["商品"])
async def register_product(
    body: ProductRegisterRequest,
    user: dict = Depends(get_current_user),
):
    """
    BOOTH商品URLを登録する。
    すでに登録済みの場合は既存の商品情報を返す。
    """
    # URLからアイテムIDを抽出
    item_id = extract_booth_item_id(body.booth_url)
    if not item_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="有効なBOOTH商品URLを入力してください（例: https://booth.pm/ja/items/1234567）",
        )

    # 既存チェック
    existing = await get_product_by_booth_id(item_id)
    if existing:
        return existing

    # スクレイピング
    scraped = await scrape_booth_item(item_id)
    if not scraped:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="商品情報の取得に失敗しました。URLを確認するか、しばらくしてからお試しください。",
        )

    # DB登録
    product_data = {
        "id": str(uuid.uuid4()),
        "booth_item_id": scraped["booth_item_id"],
        "title": scraped["title"],
        "creator_name": scraped["creator_name"],
        "shop_name": scraped["shop_name"],
        "current_price": scraped["current_price"],
        "thumbnail_url": scraped["thumbnail_url"],
        "booth_url": scraped["booth_url"],
        "category": scraped["category"],
        "description": scraped["description"],
    }
    product = await create_product(product_data)

    # 初回価格履歴を記録
    if scraped["current_price"] is not None:
        await add_price_history(product["id"], scraped["current_price"])

    # アバター紐づけ処理
    for avatar_name in scraped.get("extracted_avatar_names", []):
        from database import get_avatar_by_name
        avatar = await get_avatar_by_name(avatar_name)
        if avatar:
            await link_product_avatar(product["id"], avatar["id"])

    return product


@app.get("/api/products", response_model=ProductListResponse, tags=["商品"])
async def list_products(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: Optional[str] = None,
    search: Optional[str] = None,
):
    """商品一覧を取得する"""
    items, total = await get_products(page, per_page, category, search)
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@app.get("/api/products/{product_id}", response_model=ProductResponse, tags=["商品"])
async def get_product(product_id: str):
    """商品詳細を取得する"""
    from database import get_db
    db = get_db()
    res = db.table("products").select("*").eq("id", product_id).maybe_single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="商品が見つかりません")
    return res.data


# ==========================================
# 価格履歴API
# ==========================================

@app.get("/api/products/{product_id}/prices", response_model=PriceHistoryResponse, tags=["価格履歴"])
async def get_product_price_history(
    product_id: str,
    limit: int = Query(90, ge=7, le=365),
):
    """商品の価格履歴を取得する"""
    from database import get_db
    db = get_db()
    product_res = db.table("products").select("current_price").eq("id", product_id).maybe_single().execute()
    if not product_res.data:
        raise HTTPException(status_code=404, detail="商品が見つかりません")

    history = await get_price_history(product_id, limit)
    stats = await get_price_stats(product_id)

    return {
        "product_id": product_id,
        "current_price": product_res.data.get("current_price"),
        "lowest_price": stats.get("lowest_price"),
        "lowest_price_date": stats.get("lowest_price_date"),
        "highest_price": stats.get("highest_price"),
        "history": [
            {"price": h["price"], "recorded_at": h["recorded_at"]}
            for h in history
        ],
    }


# ==========================================
# アバターAPI
# ==========================================

@app.get("/api/avatars", response_model=AvatarListResponse, tags=["アバター"])
async def list_avatars(search: Optional[str] = None):
    """アバター一覧を取得する"""
    items = await get_avatars(search)
    return {"items": items, "total": len(items)}


@app.get("/api/avatars/{avatar_id}", response_model=AvatarResponse, tags=["アバター"])
async def get_avatar(avatar_id: str):
    """アバター詳細を取得する"""
    avatar = await get_avatar_by_id(avatar_id)
    if not avatar:
        raise HTTPException(status_code=404, detail="アバターが見つかりません")
    return avatar


@app.get("/api/avatars/{avatar_id}/products", response_model=ProductListResponse, tags=["アバター"])
async def get_avatar_products(
    avatar_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: Optional[str] = None,
    sort: str = Query("popular", regex="^(popular|newest|price_asc|discount)$"),
):
    """アバター対応商品一覧を取得する"""
    avatar = await get_avatar_by_id(avatar_id)
    if not avatar:
        raise HTTPException(status_code=404, detail="アバターが見つかりません")

    items, total = await get_products_by_avatar(avatar_id, page, per_page, category, sort)
    return {"items": items, "total": total, "page": page, "per_page": per_page}


# ==========================================
# レビューAPI
# ==========================================

@app.post("/api/reviews", response_model=ReviewResponse, tags=["レビュー"])
async def post_review(
    body: ReviewCreateRequest,
    user: dict = Depends(get_current_user),
):
    """レビューを投稿する（ログイン必須・1商品1レビューまで）"""
    # 評価値チェック
    if not 1 <= body.rating <= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="評価は1〜5で入力してください",
        )

    # 重複チェック
    already = await has_user_reviewed(body.product_id, user["id"])
    if already:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="この商品にはすでにレビューを投稿済みです",
        )

    # ユーザー名を取得
    from database import get_db
    db = get_db()
    profile_res = db.table("profiles").select("username").eq("id", user["id"]).maybe_single().execute()
    username = (profile_res.data or {}).get("username", "匿名ユーザー")

    review_data = {
        "id": str(uuid.uuid4()),
        "product_id": body.product_id,
        "avatar_id": body.avatar_id,
        "user_id": user["id"],
        "rating": body.rating,
        "comment": body.comment,
    }
    review = await create_review(review_data)
    review["username"] = username

    # アバター名を付加
    avatar = await get_avatar_by_id(body.avatar_id)
    review["avatar_name"] = (avatar or {}).get("name")

    return review


@app.get("/api/products/{product_id}/reviews", response_model=ReviewListResponse, tags=["レビュー"])
async def get_product_reviews(
    product_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    """商品のレビュー一覧を取得する"""
    items, total = await get_reviews(product_id, page, per_page)
    stats = await get_review_stats(product_id)

    # レビューにアバター名・ユーザー名を付加
    formatted = []
    for r in items:
        formatted.append({
            **r,
            "avatar_name": (r.get("avatars") or {}).get("name"),
            "username": (r.get("profiles") or {}).get("username", "匿名ユーザー"),
        })

    return {
        "items": formatted,
        "total": total,
        "average_rating": stats.get("average_rating"),
        "rating_distribution": stats.get("rating_distribution", {}),
    }


@app.get("/api/products/{product_id}/reviews/avatars", response_model=list[AvatarRatingResponse], tags=["レビュー"])
async def get_product_avatar_ratings(product_id: str):
    """商品のアバター別評価一覧を取得する"""
    return await get_avatar_ratings(product_id)


# ==========================================
# 認証API（Supabase Auth のラッパー）
# ==========================================

@app.post("/api/auth/register", tags=["認証"])
async def register(body: dict):
    """
    ユーザー登録（メール＋パスワード）。
    Supabase Auth に登録 → profilesテーブルにユーザー名を保存。
    """
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_key)

    email = body.get("email", "").strip()
    password = body.get("password", "")
    username = body.get("username", "").strip()

    if not email or not password or not username:
        raise HTTPException(status_code=400, detail="メールアドレス、パスワード、ユーザー名は必須です")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="パスワードは8文字以上で設定してください")
    if len(username) < 2 or len(username) > 20:
        raise HTTPException(status_code=400, detail="ユーザー名は2〜20文字で設定してください")

    try:
        res = client.auth.sign_up({"email": email, "password": password})
        if not res.user:
            raise HTTPException(status_code=400, detail="登録に失敗しました")

        # profilesテーブルにユーザー名を保存
        db = get_db()
        db.table("profiles").insert({
            "id": res.user.id,
            "username": username,
        }).execute()

        return {"message": "登録しました。確認メールをご確認ください。"}
    except Exception as e:
        error_msg = str(e)
        if "already registered" in error_msg:
            raise HTTPException(status_code=409, detail="このメールアドレスはすでに登録されています")
        raise HTTPException(status_code=400, detail="登録に失敗しました")


@app.post("/api/auth/login", tags=["認証"])
async def login(body: dict):
    """ログイン（メール＋パスワード）→ アクセストークンを返す"""
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_key)

    email = body.get("email", "").strip()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="メールアドレスとパスワードを入力してください")

    try:
        res = client.auth.sign_in_with_password({"email": email, "password": password})
        if not res.session:
            raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")

        # ユーザー名を取得
        db = get_db()
        profile_res = db.table("profiles").select("username").eq("id", res.user.id).maybe_single().execute()
        username = (profile_res.data or {}).get("username", "")

        return {
            "access_token": res.session.access_token,
            "token_type": "bearer",
            "user_id": res.user.id,
            "username": username,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="ログインに失敗しました")


@app.get("/api/auth/me", tags=["認証"])
async def me(user: dict = Depends(get_current_user)):
    """ログイン中のユーザー情報を返す"""
    db = get_db()
    profile_res = db.table("profiles").select("username").eq("id", user["id"]).maybe_single().execute()
    username = (profile_res.data or {}).get("username", "")
    return {"user_id": user["id"], "email": user["email"], "username": username}

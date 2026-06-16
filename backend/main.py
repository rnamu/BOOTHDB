# main.py - FastAPI本体

from fastapi import FastAPI, HTTPException, Depends, status, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Optional
import uuid
import os
import secrets

from config import settings
from models import (
    ProductRegisterRequest, ProductResponse, ProductListResponse,
    PriceHistoryResponse,
    AvatarResponse, AvatarListResponse,
    ReviewCreateRequest, ReviewResponse, ReviewListResponse, AvatarRatingResponse,
    MessageResponse,
)
from database import (
    get_product_by_booth_id, create_product, get_products, get_db,
    add_price_history, get_price_history, get_price_stats,
    get_avatars, get_avatar_by_id, get_products_by_avatar, link_product_avatar,
    create_review, get_reviews, get_review_stats, get_avatar_ratings,
    has_user_reviewed,
)
from scraper import extract_booth_item_id, scrape_booth_item
from scheduler import start_scheduler, stop_scheduler
from auth import get_current_user


# ==========================================
# 管理者トークン管理（メモリ内）
# ==========================================
_admin_tokens: set[str] = set()

def verify_admin_password(password: str) -> bool:
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    return bool(admin_password) and secrets.compare_digest(password, admin_password)

def require_admin(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="管理者認証が必要です")
    token = authorization.removeprefix("Bearer ").strip()
    if token not in _admin_tokens:
        raise HTTPException(status_code=401, detail="管理者トークンが無効です")
    return token


# ==========================================
# アプリ起動・終了処理
# ==========================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="BOOTHDB API",
    description="VRChat向けBOOTH商品データベースのバックエンドAPI",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:8080",
        "http://127.0.0.1:5500",
        "https://*.github.io",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================
# ヘルスチェック
# ==========================================

@app.get("/ping", tags=["システム"])
async def ping():
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
    item_id = extract_booth_item_id(body.booth_url)
    if not item_id:
        raise HTTPException(status_code=400, detail="有効なBOOTH商品URLを入力してください")

    existing = await get_product_by_booth_id(item_id)
    if existing:
        return existing

    scraped = await scrape_booth_item(item_id)
    if not scraped:
        raise HTTPException(status_code=422, detail="商品情報の取得に失敗しました")

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

    if scraped["current_price"] is not None:
        await add_price_history(product["id"], scraped["current_price"])

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
    items, total = await get_products(page, per_page, category, search)
    return {"items": items, "total": total, "page": page, "per_page": per_page}


@app.get("/api/products/{product_id}", response_model=ProductResponse, tags=["商品"])
async def get_product(product_id: str):
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
    items = await get_avatars(search)
    return {"items": items, "total": len(items)}


@app.get("/api/avatars/{avatar_id}", response_model=AvatarResponse, tags=["アバター"])
async def get_avatar(avatar_id: str):
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
    sort: str = Query("popular", pattern="^(popular|newest|price_asc|discount)$"),
):
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
    if not 1 <= body.rating <= 5:
        raise HTTPException(status_code=400, detail="評価は1〜5で入力してください")

    already = await has_user_reviewed(body.product_id, user["id"])
    if already:
        raise HTTPException(status_code=409, detail="この商品にはすでにレビューを投稿済みです")

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

    avatar = await get_avatar_by_id(body.avatar_id)
    review["avatar_name"] = (avatar or {}).get("name")

    return review


@app.get("/api/products/{product_id}/reviews", response_model=ReviewListResponse, tags=["レビュー"])
async def get_product_reviews(
    product_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    items, total = await get_reviews(product_id, page, per_page)
    stats = await get_review_stats(product_id)

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
    return await get_avatar_ratings(product_id)


# ==========================================
# 認証API
# ==========================================

@app.post("/api/auth/register", tags=["認証"])
async def register(body: dict):
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_key)

    email    = body.get("email", "").strip()
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

        db = get_db()
        db.table("profiles").insert({"id": res.user.id, "username": username}).execute()

        return {"message": "登録しました。確認メールをご確認ください。"}
    except HTTPException:
        raise
    except Exception as e:
        if "already registered" in str(e):
            raise HTTPException(status_code=409, detail="このメールアドレスはすでに登録されています")
        raise HTTPException(status_code=400, detail="登録に失敗しました")


@app.post("/api/auth/login", tags=["認証"])
async def login(body: dict):
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_key)

    email    = body.get("email", "").strip()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="メールアドレスとパスワードを入力してください")

    try:
        res = client.auth.sign_in_with_password({"email": email, "password": password})
        if not res.session:
            raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")

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
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"ログインに失敗しました: {str(e)}")


@app.get("/api/auth/me", tags=["認証"])
async def me(user: dict = Depends(get_current_user)):
    db = get_db()
    profile_res = db.table("profiles").select("username").eq("id", user["id"]).maybe_single().execute()
    username = (profile_res.data or {}).get("username", "")
    return {"user_id": user["id"], "email": user["email"], "username": username}


# ==========================================
# 管理者API
# ==========================================

@app.post("/api/admin/login", tags=["管理者"])
async def admin_login(body: dict):
    password = body.get("password", "")
    if not verify_admin_password(password):
        raise HTTPException(status_code=401, detail="パスワードが正しくありません")
    token = secrets.token_hex(32)
    _admin_tokens.add(token)
    return {"admin_token": token, "message": "ログイン成功"}


@app.get("/api/admin/users", tags=["管理者"])
async def admin_list_users(token: str = Depends(require_admin)):
    db = get_db()
    res = db.table("profiles").select("*").order("created_at", desc=True).execute()
    return {"items": res.data, "total": len(res.data)}


@app.delete("/api/admin/users/{user_id}", tags=["管理者"])
async def admin_ban_user(user_id: str, token: str = Depends(require_admin)):
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_service_key)
    client.auth.admin.delete_user(user_id)
    db = get_db()
    db.table("profiles").delete().eq("id", user_id).execute()
    return {"message": "ユーザーをBANしました"}


@app.get("/api/admin/products", tags=["管理者"])
async def admin_list_products(token: str = Depends(require_admin)):
    db = get_db()
    res = db.table("products").select("*").order("registered_at", desc=True).execute()
    return {"items": res.data, "total": len(res.data)}


@app.delete("/api/admin/products/{product_id}", tags=["管理者"])
async def admin_delete_product(product_id: str, token: str = Depends(require_admin)):
    db = get_db()
    db.table("products").delete().eq("id", product_id).execute()
    return {"message": "削除しました"}


@app.get("/api/admin/reviews", tags=["管理者"])
async def admin_list_reviews(token: str = Depends(require_admin)):
    db = get_db()
    res = db.table("reviews").select("*, profiles(username), products(title)").order("created_at", desc=True).execute()
    return {"items": res.data, "total": len(res.data)}


@app.delete("/api/admin/reviews/{review_id}", tags=["管理者"])
async def admin_delete_review(review_id: str, token: str = Depends(require_admin)):
    db = get_db()
    db.table("reviews").delete().eq("id", review_id).execute()
    return {"message": "削除しました"}


@app.post("/api/admin/avatars", tags=["管理者"])
async def admin_create_avatar(body: dict, token: str = Depends(require_admin)):
    name    = body.get("name", "").strip()
    name_en = body.get("name_en", "").strip()
    creator = body.get("creator", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="アバター名は必須です")
    db = get_db()
    res = db.table("avatars").insert({
        "id": str(uuid.uuid4()),
        "name": name,
        "name_en": name_en or None,
        "creator": creator or None,
    }).execute()
    return res.data[0]


@app.patch("/api/admin/avatars/{avatar_id}", tags=["管理者"])
async def admin_update_avatar(avatar_id: str, body: dict, token: str = Depends(require_admin)):
    update_data = {k: v for k, v in body.items() if k in ("name", "name_en", "creator")}
    if not update_data:
        raise HTTPException(status_code=400, detail="更新するデータがありません")
    db = get_db()
    res = db.table("avatars").update(update_data).eq("id", avatar_id).execute()
    return res.data[0]


@app.delete("/api/admin/avatars/{avatar_id}", tags=["管理者"])
async def admin_delete_avatar(avatar_id: str, token: str = Depends(require_admin)):
    db = get_db()
    db.table("avatars").delete().eq("id", avatar_id).execute()
    return {"message": "削除しました"}

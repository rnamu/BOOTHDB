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
    ReviewListResponse,
    MessageResponse,
)
from database import (
    get_product_by_booth_id, create_product, get_products, get_db,
    add_price_history, add_price_history_for_variations,
    get_price_history, get_price_history_by_variation, get_price_stats,
    create_review, get_reviews, get_review_stats,
    has_user_reviewed, save_product_variations, get_product_variations,
    get_sale_products, get_new_products,
)
from scraper import extract_booth_item_id, scrape_booth_item, CRAWL_CATEGORIES
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
    version="2.0.0",
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
    return {"message": "BOOTHDB API", "version": "2.0.0"}


# ==========================================
# カテゴリ一覧API（フロントのカテゴリタグ表示用）
# ==========================================

@app.get("/api/categories", tags=["商品"])
async def list_categories():
    """収集対象カテゴリの一覧を返す"""
    return {"items": list(CRAWL_CATEGORIES.keys())}


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

    if scraped.get("variations"):
        await add_price_history_for_variations(product["id"], scraped["variations"])
        await save_product_variations(product["id"], scraped["variations"])
    elif scraped["current_price"] is not None:
        await add_price_history(product["id"], scraped["current_price"])

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


@app.get("/api/products/new", tags=["商品"])
async def list_new_products(limit: int = Query(6, ge=1, le=20)):
    """新着商品を取得する（トップページ用）"""
    items = await get_new_products(limit)
    return {"items": items, "total": len(items)}


@app.get("/api/products/sale", tags=["商品"])
async def list_sale_products(limit: int = Query(6, ge=1, le=20)):
    """セール中（値下がり中）商品を取得する（トップページ・ヘッダー用）"""
    items = await get_sale_products(limit)
    return {"items": items, "total": len(items)}


@app.get("/api/products/{product_id}", response_model=ProductResponse, tags=["商品"])
async def get_product(product_id: str):
    """商品詳細を取得する"""
    db = get_db()
    try:
        res = db.table("products").select("*").eq("id", product_id).limit(1).execute()
        if res and res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        print(f"[get_product] エラー: {e}")
    raise HTTPException(status_code=404, detail="商品が見つかりません")


@app.get("/api/products/{product_id}/variations", tags=["商品"])
async def get_product_variations_endpoint(product_id: str):
    """商品のバリエーション一覧を取得する"""
    variations = await get_product_variations(product_id)
    return {"items": variations, "total": len(variations)}


# ==========================================
# 価格履歴API
# ==========================================

@app.get("/api/products/{product_id}/prices", response_model=PriceHistoryResponse, tags=["価格履歴"])
async def get_product_price_history(
    product_id: str,
    limit: int = Query(90, ge=7, le=365),
):
    """商品の価格履歴を取得する（後方互換用・単一系統）"""
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


@app.get("/api/products/{product_id}/prices/by-variation", tags=["価格履歴"])
async def get_product_price_history_by_variation(
    product_id: str,
    limit_per_variation: int = Query(90, ge=7, le=365),
):
    """商品の価格履歴をバリエーションごとにグループ化して取得する"""
    db = get_db()
    product_res = db.table("products").select("current_price").eq("id", product_id).maybe_single().execute()
    if not product_res.data:
        raise HTTPException(status_code=404, detail="商品が見つかりません")

    grouped = await get_price_history_by_variation(product_id, limit_per_variation)
    stats = await get_price_stats(product_id)

    return {
        "product_id": product_id,
        "lowest_price": stats.get("lowest_price"),
        "lowest_price_date": stats.get("lowest_price_date"),
        "highest_price": stats.get("highest_price"),
        "series": grouped,
    }


# ==========================================
# レビューAPI（使用アバターなし版）
# ==========================================

@app.post("/api/reviews", tags=["レビュー"])
async def post_review(
    body: dict,
    user: dict = Depends(get_current_user),
):
    product_id = body.get("product_id")
    rating = body.get("rating")
    comment = body.get("comment")

    if not product_id:
        raise HTTPException(status_code=400, detail="商品IDは必須です")
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        raise HTTPException(status_code=400, detail="評価は1〜5で入力してください")

    already = await has_user_reviewed(product_id, user["id"])
    if already:
        raise HTTPException(status_code=409, detail="この商品にはすでにレビューを投稿済みです")

    db = get_db()
    profile_res = db.table("profiles").select("username").eq("id", user["id"]).maybe_single().execute()
    username = (profile_res.data or {}).get("username", "匿名ユーザー")

    review_data = {
        "id": str(uuid.uuid4()),
        "product_id": product_id,
        "user_id": user["id"],
        "rating": rating,
        "comment": comment,
    }
    review = await create_review(review_data)
    review["username"] = username

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
            "username": (r.get("profiles") or {}).get("username", "匿名ユーザー"),
        })

    return {
        "items": formatted,
        "total": total,
        "average_rating": stats.get("average_rating"),
        "rating_distribution": stats.get("rating_distribution", {}),
    }


# ==========================================
# 認証API（リフレッシュトークン対応）
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

        session = getattr(res, 'session', None)
        user = getattr(res, 'user', None)

        if not session or not user:
            raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")

        db = get_db()
        try:
            profile_res = db.table("profiles").select("username").eq("id", user.id).maybe_single().execute()
            username = (profile_res.data or {}).get("username", "") if profile_res else ""
        except Exception:
            username = ""

        return {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "token_type": "bearer",
            "user_id": user.id,
            "username": username,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Login Error] {type(e).__name__}: {str(e)}")
        raise HTTPException(status_code=401, detail="メールアドレスまたはパスワードが正しくありません")


@app.post("/api/auth/refresh", tags=["認証"])
async def refresh_token(body: dict):
    """
    リフレッシュトークンを使ってアクセストークンを再発行する。
    アクセストークンは通常1時間で失効するため、フロント側はAPIが401を
    返したタイミングでこのエンドポイントを呼び、新しいトークンに差し替える。
    """
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_key)

    refresh_tok = body.get("refresh_token", "")
    if not refresh_tok:
        raise HTTPException(status_code=400, detail="リフレッシュトークンが必要です")

    try:
        res = client.auth.refresh_session(refresh_tok)
        session = getattr(res, 'session', None)
        user = getattr(res, 'user', None)

        if not session or not user:
            raise HTTPException(status_code=401, detail="再ログインが必要です")

        return {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
            "token_type": "bearer",
            "user_id": user.id,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="セッションの更新に失敗しました。再ログインしてください。")


@app.get("/api/auth/me", tags=["認証"])
async def me(user: dict = Depends(get_current_user)):
    db = get_db()
    try:
        profile_res = db.table("profiles").select("username").eq("id", user["id"]).maybe_single().execute()
        username = (profile_res.data or {}).get("username", "") if profile_res else ""
    except Exception:
        username = ""
    return {"user_id": user["id"], "email": user["email"], "username": username}


# サイト管理者として扱うメールアドレス一覧
ADMIN_EMAIL_WHITELIST = {"admin@boothdb.com"}


@app.post("/api/products/{product_id}/rescrape", tags=["商品"])
async def rescrape_product_as_user(
    product_id: str,
    user: dict = Depends(get_current_user),
):
    """
    商品情報を再取得して上書き更新する。
    ADMIN_EMAIL_WHITELISTに登録されたメールアドレスのユーザーのみ実行できる。
    """
    if (user.get("email") or "").lower() not in ADMIN_EMAIL_WHITELIST:
        raise HTTPException(status_code=403, detail="この操作には管理者権限が必要です")

    db = get_db()
    res = db.table("products").select("booth_item_id").eq("id", product_id).maybe_single().execute()
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="商品が見つかりません")

    booth_item_id = res.data["booth_item_id"]
    scraped = await scrape_booth_item(booth_item_id)
    if not scraped:
        raise HTTPException(status_code=422, detail="再取得に失敗しました。BOOTH側で商品が削除された可能性があります。")

    update_data = {
        "title": scraped["title"],
        "creator_name": scraped["creator_name"],
        "shop_name": scraped["shop_name"],
        "current_price": scraped["current_price"],
        "thumbnail_url": scraped["thumbnail_url"],
        "category": scraped["category"],
        "description": scraped["description"],
        "last_checked_at": __import__("datetime").datetime.utcnow().isoformat(),
    }
    db.table("products").update(update_data).eq("id", product_id).execute()

    if scraped.get("variations"):
        await add_price_history_for_variations(product_id, scraped["variations"])
        await save_product_variations(product_id, scraped["variations"])
    elif scraped["current_price"] is not None:
        await add_price_history(product_id, scraped["current_price"])

    updated = db.table("products").select("*").eq("id", product_id).maybe_single().execute()
    return updated.data


# ==========================================
# 管理者API
# ==========================================

@app.post("/api/admin/login", tags=["管理者"])
async def admin_login(body: dict):
    """管理者ログイン（パスワードのみ）"""
    password = body.get("password", "")
    if not verify_admin_password(password):
        raise HTTPException(status_code=401, detail="パスワードが正しくありません")
    token = secrets.token_hex(32)
    _admin_tokens.add(token)
    return {"admin_token": token, "message": "ログイン成功"}


@app.get("/api/admin/users", tags=["管理者"])
async def admin_list_users(token: str = Depends(require_admin)):
    """ユーザー一覧（管理者専用）"""
    db = get_db()
    res = db.table("profiles").select("*").order("created_at", desc=True).execute()
    return {"items": res.data, "total": len(res.data)}


@app.delete("/api/admin/users/{user_id}", tags=["管理者"])
async def admin_ban_user(user_id: str, token: str = Depends(require_admin)):
    """ユーザーをBAN（管理者専用）"""
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_service_key)
    client.auth.admin.delete_user(user_id)
    db = get_db()
    db.table("profiles").delete().eq("id", user_id).execute()
    return {"message": "ユーザーをBANしました"}


@app.get("/api/admin/products", tags=["管理者"])
async def admin_list_products(token: str = Depends(require_admin)):
    """全商品一覧（管理者専用）"""
    db = get_db()
    res = db.table("products").select("*").order("registered_at", desc=True).execute()
    return {"items": res.data, "total": len(res.data)}


@app.delete("/api/admin/products/{product_id}", tags=["管理者"])
async def admin_delete_product(product_id: str, token: str = Depends(require_admin)):
    """商品を削除（管理者専用）"""
    db = get_db()
    db.table("products").delete().eq("id", product_id).execute()
    return {"message": "削除しました"}


@app.get("/api/admin/reviews", tags=["管理者"])
async def admin_list_reviews(token: str = Depends(require_admin)):
    """全レビュー一覧（管理者専用）"""
    db = get_db()
    res = db.table("reviews").select("*, profiles(username), products(title)").order("created_at", desc=True).execute()
    return {"items": res.data, "total": len(res.data)}


@app.delete("/api/admin/reviews/{review_id}", tags=["管理者"])
async def admin_delete_review(review_id: str, token: str = Depends(require_admin)):
    """レビューを削除（管理者専用）"""
    db = get_db()
    db.table("reviews").delete().eq("id", review_id).execute()
    return {"message": "削除しました"}


@app.post("/api/admin/products/{product_id}/rescrape", tags=["管理者"])
async def admin_rescrape_product(product_id: str, token: str = Depends(require_admin)):
    """商品情報を再取得して上書き更新する（管理者専用）"""
    db = get_db()
    res = db.table("products").select("booth_item_id").eq("id", product_id).maybe_single().execute()
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="商品が見つかりません")

    booth_item_id = res.data["booth_item_id"]
    scraped = await scrape_booth_item(booth_item_id)
    if not scraped:
        raise HTTPException(status_code=422, detail="再取得に失敗しました。BOOTH側で商品が削除された可能性があります。")

    update_data = {
        "title": scraped["title"],
        "creator_name": scraped["creator_name"],
        "shop_name": scraped["shop_name"],
        "current_price": scraped["current_price"],
        "thumbnail_url": scraped["thumbnail_url"],
        "category": scraped["category"],
        "description": scraped["description"],
        "last_checked_at": __import__("datetime").datetime.utcnow().isoformat(),
    }
    db.table("products").update(update_data).eq("id", product_id).execute()

    if scraped.get("variations"):
        await add_price_history_for_variations(product_id, scraped["variations"])
        await save_product_variations(product_id, scraped["variations"])
    elif scraped["current_price"] is not None:
        await add_price_history(product_id, scraped["current_price"])

    updated = db.table("products").select("*").eq("id", product_id).maybe_single().execute()
    return updated.data


# ==========================================
# 管理者API - カテゴリクロール
# ==========================================

_crawl_running = {"active": False}


@app.post("/api/admin/crawl/run", tags=["管理者"])
async def admin_run_crawl(token: str = Depends(require_admin)):
    """カテゴリクロールを今すぐ実行する（管理者専用）"""
    if _crawl_running["active"]:
        raise HTTPException(status_code=409, detail="クロールはすでに実行中です")

    import asyncio
    from scheduler import crawl_categories

    async def _run():
        _crawl_running["active"] = True
        try:
            await crawl_categories(pages_per_category=10)
        finally:
            _crawl_running["active"] = False

    asyncio.create_task(_run())
    return {"message": "クロールを開始しました。完了まで数分かかります。"}


@app.get("/api/admin/crawl/status", tags=["管理者"])
async def admin_crawl_status(token: str = Depends(require_admin)):
    """クロールの進捗状況を取得する（管理者専用）"""
    from database import get_crawl_progress

    progress_list = []
    for category_name in CRAWL_CATEGORIES.keys():
        progress = await get_crawl_progress(category_name)
        progress_list.append(progress)

    return {
        "running": _crawl_running["active"],
        "categories": progress_list,
    }

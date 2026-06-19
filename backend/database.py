# database.py - Supabaseクライアント・データベース操作

from supabase import create_client, Client
from config import settings
from typing import Optional
from datetime import datetime

# サービスロールキーを使ったクライアント（バックエンド専用）
_client: Optional[Client] = None


def get_db() -> Client:
    """Supabaseクライアントをシングルトンで返す"""
    global _client
    if _client is None:
        _client = create_client(
            settings.supabase_url,
            settings.supabase_service_key,
        )
    return _client


# ==========================================
# 商品操作
# ==========================================

async def get_product_by_booth_id(booth_item_id: str) -> Optional[dict]:
    """BOOTHアイテムIDで商品を取得する"""
    db = get_db()
    res = db.table("products") \
        .select("*") \
        .eq("booth_item_id", booth_item_id) \
        .maybe_single() \
        .execute()
    return res.data


async def create_product(data: dict) -> dict:
    """商品を新規登録する"""
    db = get_db()
    res = db.table("products").insert(data).execute()
    return res.data[0]


async def update_product_price(product_id: str, price: int) -> None:
    """商品の現在価格と最終確認日時を更新する"""
    db = get_db()
    db.table("products").update({
        "current_price": price,
        "last_checked_at": datetime.utcnow().isoformat(),
    }).eq("id", product_id).execute()


async def get_products(
    page: int = 1,
    per_page: int = 20,
    category: Optional[str] = None,
    search: Optional[str] = None,
) -> tuple[list[dict], int]:
    """商品一覧を取得する（ページネーション付き）"""
    db = get_db()
    offset = (page - 1) * per_page

    query = db.table("products").select("*", count="exact")

    if category:
        query = query.eq("category", category)
    if search:
        query = query.ilike("title", f"%{search}%")

    res = query.order("registered_at", desc=True) \
        .range(offset, offset + per_page - 1) \
        .execute()

    return res.data, (res.count or 0)


async def get_all_products_for_scrape() -> list[dict]:
    """定期スクレイピング対象の全商品を取得する"""
    db = get_db()
    res = db.table("products") \
        .select("id, booth_item_id") \
        .execute()
    return res.data


# ==========================================
# 価格履歴操作
# ==========================================

async def add_price_history(product_id: str, price: int) -> None:
    """価格履歴を1件追加する"""
    db = get_db()
    db.table("price_history").insert({
        "product_id": product_id,
        "price": price,
        "recorded_at": datetime.utcnow().isoformat(),
    }).execute()


async def get_price_history(product_id: str, limit: int = 90) -> list[dict]:
    """商品の価格履歴を取得する（最新limit件）"""
    db = get_db()
    res = db.table("price_history") \
        .select("price, recorded_at") \
        .eq("product_id", product_id) \
        .order("recorded_at", desc=False) \
        .limit(limit) \
        .execute()
    return res.data


async def get_price_stats(product_id: str) -> dict:
    """最安値・最高値・平均値を取得する"""
    db = get_db()
    res = db.table("price_history") \
        .select("price, recorded_at") \
        .eq("product_id", product_id) \
        .execute()

    if not res.data:
        return {}

    prices = [r["price"] for r in res.data]
    lowest = min(prices)
    lowest_date = next(
        r["recorded_at"] for r in res.data if r["price"] == lowest
    )

    return {
        "lowest_price": lowest,
        "lowest_price_date": lowest_date,
        "highest_price": max(prices),
        "average_price": round(sum(prices) / len(prices)),
    }


# ==========================================
# アバター操作
# ==========================================

async def get_avatars(search: Optional[str] = None) -> list[dict]:
    """アバター一覧を取得する"""
    db = get_db()
    query = db.table("avatars").select("*")
    if search:
        query = query.ilike("name", f"%{search}%")
    res = query.order("name").execute()

    # 各アバターの対応商品数を整数として付加する
    for item in res.data:
        count_res = db.table("product_avatar_map") \
            .select("product_id", count="exact") \
            .eq("avatar_id", item["id"]) \
            .execute()
        item["product_count"] = count_res.count or 0

    return res.data


async def get_avatar_by_id(avatar_id: str) -> Optional[dict]:
    """アバターIDでアバターを取得する"""
    db = get_db()
    res = db.table("avatars") \
        .select("*") \
        .eq("id", avatar_id) \
        .maybe_single() \
        .execute()
    return res.data


async def get_avatar_by_name(name: str) -> Optional[dict]:
    """アバター名（部分一致）でアバターを取得する"""
    db = get_db()
    res = db.table("avatars") \
        .select("*") \
        .ilike("name", f"%{name}%") \
        .maybe_single() \
        .execute()
    return res.data


async def get_products_by_avatar(
    avatar_id: str,
    page: int = 1,
    per_page: int = 20,
    category: Optional[str] = None,
    sort: str = "popular",
) -> tuple[list[dict], int]:
    """アバター対応商品一覧を取得する"""
    db = get_db()
    offset = (page - 1) * per_page

    query = db.table("product_avatar_map") \
        .select("products(*)", count="exact") \
        .eq("avatar_id", avatar_id)

    res = query.range(offset, offset + per_page - 1).execute()
    items = [r["products"] for r in res.data if r.get("products")]
    return items, (res.count or 0)


async def link_product_avatar(product_id: str, avatar_id: str) -> None:
    """商品とアバターを紐づける（中間テーブル）"""
    db = get_db()
    try:
        db.table("product_avatar_map").insert({
            "product_id": product_id,
            "avatar_id": avatar_id,
        }).execute()
    except Exception:
        pass  # 既存レコードの場合は無視


# ==========================================
# レビュー操作
# ==========================================

async def create_review(data: dict) -> dict:
    """レビューを投稿する"""
    db = get_db()
    res = db.table("reviews").insert(data).execute()
    return res.data[0]


async def get_reviews(
    product_id: str,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[dict], int]:
    """商品のレビュー一覧を取得する"""
    db = get_db()
    offset = (page - 1) * per_page

    res = db.table("reviews") \
        .select("*, avatars(name), profiles(username)", count="exact") \
        .eq("product_id", product_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + per_page - 1) \
        .execute()

    return res.data, (res.count or 0)


async def get_review_stats(product_id: str) -> dict:
    """レビューの平均評価・分布を取得する"""
    db = get_db()
    res = db.table("reviews") \
        .select("rating") \
        .eq("product_id", product_id) \
        .execute()

    if not res.data:
        return {"average_rating": None, "rating_distribution": {}}

    ratings = [r["rating"] for r in res.data]
    distribution = {}
    for i in range(1, 6):
        distribution[str(i)] = ratings.count(i)

    return {
        "average_rating": round(sum(ratings) / len(ratings), 1),
        "rating_distribution": distribution,
    }


async def get_avatar_ratings(product_id: str) -> list[dict]:
    """商品のアバター別平均評価を取得する"""
    db = get_db()
    res = db.table("reviews") \
        .select("rating, avatar_id, avatars(name)") \
        .eq("product_id", product_id) \
        .execute()

    if not res.data:
        return []

    # アバターIDごとに集計
    avatar_map: dict[str, dict] = {}
    for r in res.data:
        aid = r["avatar_id"]
        if aid not in avatar_map:
            avatar_map[aid] = {
                "avatar_id": aid,
                "avatar_name": (r.get("avatars") or {}).get("name", "不明"),
                "ratings": [],
            }
        avatar_map[aid]["ratings"].append(r["rating"])

    result = []
    for v in avatar_map.values():
        ratings = v["ratings"]
        result.append({
            "avatar_id": v["avatar_id"],
            "avatar_name": v["avatar_name"],
            "average_rating": round(sum(ratings) / len(ratings), 1),
            "review_count": len(ratings),
        })

    return sorted(result, key=lambda x: x["average_rating"], reverse=True)


async def has_user_reviewed(product_id: str, user_id: str) -> bool:
    """ユーザーがすでにレビューを投稿済みか確認する"""
    db = get_db()
    res = db.table("reviews") \
        .select("id") \
        .eq("product_id", product_id) \
        .eq("user_id", user_id) \
        .maybe_single() \
        .execute()
    return res.data is not None


# ==========================================
# クロール進捗管理
# ==========================================

async def get_crawl_progress(category: str) -> dict:
    """カテゴリごとのクロール進捗を取得する（なければ初期値を返す）"""
    db = get_db()
    try:
        res = db.table("crawl_progress") \
            .select("*") \
            .eq("category", category) \
            .limit(1) \
            .execute()
        if res and res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        print(f"[get_crawl_progress] エラー: {e}")
    return {"category": category, "last_page": 0, "total_collected": 0}


async def update_crawl_progress(category: str, last_page: int, collected_delta: int) -> None:
    """クロール進捗を更新する（UPSERT）"""
    db = get_db()
    current = await get_crawl_progress(category)
    new_total = (current.get("total_collected") or 0) + collected_delta

    db.table("crawl_progress").upsert({
        "category": category,
        "last_page": last_page,
        "total_collected": new_total,
        "updated_at": datetime.utcnow().isoformat(),
    }, on_conflict="category").execute()


async def reset_crawl_progress(category: str) -> None:
    """カテゴリの進捗をリセットする（最初からやり直したい時用）"""
    db = get_db()
    db.table("crawl_progress").delete().eq("category", category).execute()

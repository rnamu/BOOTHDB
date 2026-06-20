# database.py - Supabaseクライアント・データベース操作

from supabase import create_client, Client
from config import settings
from typing import Optional
from datetime import datetime

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


def _first_or_none(res) -> Optional[dict]:
    """.limit(1).execute() の結果から1件目を安全に取り出す"""
    if res and getattr(res, "data", None):
        if len(res.data) > 0:
            return res.data[0]
    return None


# ==========================================
# 商品操作
# ==========================================

async def get_product_by_booth_id(booth_item_id: str) -> Optional[dict]:
    """BOOTHアイテムIDで商品を取得する"""
    db = get_db()
    try:
        res = db.table("products") \
            .select("*") \
            .eq("booth_item_id", booth_item_id) \
            .limit(1) \
            .execute()
        return _first_or_none(res)
    except Exception as e:
        print(f"[get_product_by_booth_id] エラー: {e}")
        return None


async def create_product(data: dict) -> dict:
    """商品を新規登録する"""
    db = get_db()
    res = db.table("products").insert(data).execute()
    return res.data[0]


async def save_product_variations(product_id: str, variations: list[dict]) -> None:
    """商品のバリエーション一覧を保存する（既存削除→入れ直し）"""
    db = get_db()
    try:
        db.table("product_variations").delete().eq("product_id", product_id).execute()

        if not variations:
            return

        rows = [
            {
                "product_id": product_id,
                "name": v["name"],
                "price": v["price"],
                "sort_order": v.get("sort_order", 0),
            }
            for v in variations
        ]
        db.table("product_variations").insert(rows).execute()
    except Exception as e:
        print(f"[save_product_variations] エラー: {e}")


async def get_product_variations(product_id: str) -> list[dict]:
    """商品のバリエーション一覧を取得する（表示順）"""
    db = get_db()
    try:
        res = db.table("product_variations") \
            .select("name, price, sort_order") \
            .eq("product_id", product_id) \
            .order("sort_order") \
            .execute()
        return res.data or []
    except Exception as e:
        print(f"[get_product_variations] エラー: {e}")
        return []


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


async def get_new_products(limit: int = 6) -> list[dict]:
    """新着商品を取得する（登録日時の新しい順）"""
    db = get_db()
    try:
        res = db.table("products") \
            .select("*") \
            .order("registered_at", desc=True) \
            .limit(limit) \
            .execute()
        return res.data or []
    except Exception as e:
        print(f"[get_new_products] エラー: {e}")
        return []


async def get_sale_products(limit: int = 6) -> list[dict]:
    """
    セール中（直近で値下がりした）商品を取得する。

    price_history を商品ごとに新しい順で見て、直近の価格が
    その前の価格より下がっている商品を「セール中」とみなす。
    対象が多い場合に備え、最近チェックされた商品から優先的に確認する。
    """
    db = get_db()
    try:
        # 価格が記録されている商品をある程度の件数だけ取得し、
        # その中から値下がりしているものを抽出する
        candidates = db.table("products") \
            .select("id, title, creator_name, current_price, thumbnail_url, booth_url, category, registered_at") \
            .order("last_checked_at", desc=True) \
            .limit(200) \
            .execute()

        sale_items = []
        for product in (candidates.data or []):
            history = db.table("price_history") \
                .select("price, recorded_at") \
                .eq("product_id", product["id"]) \
                .order("recorded_at", desc=True) \
                .limit(2) \
                .execute()

            rows = history.data or []
            if len(rows) < 2:
                continue

            latest_price = rows[0]["price"]
            previous_price = rows[1]["price"]

            if latest_price < previous_price:
                product["original_price"] = previous_price
                sale_items.append(product)

            if len(sale_items) >= limit:
                break

        return sale_items
    except Exception as e:
        print(f"[get_sale_products] エラー: {e}")
        return []


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

async def add_price_history(product_id: str, price: int, variation_name: Optional[str] = None) -> None:
    """
    価格履歴を追加する。
    「直近の記録」ではなく「今日すでに記録された価格」と比較し、
    同じ価格であれば新しい点を追加しない。
    これにより、同日内に何度再収集しても点が増えるのを防ぐ。
    """
    db = get_db()
    today_str = datetime.utcnow().strftime("%Y-%m-%d")

    try:
        query = db.table("price_history") \
            .select("price, recorded_at") \
            .eq("product_id", product_id) \
            .gte("recorded_at", f"{today_str}T00:00:00") \
            .order("recorded_at", desc=True)

        if variation_name is not None:
            query = query.eq("variation_name", variation_name)
        else:
            query = query.is_("variation_name", "null")

        today_records = query.limit(1).execute()

        if today_records and today_records.data and len(today_records.data) > 0:
            today_price = today_records.data[0]["price"]
            if today_price == price:
                # 今日すでに同じ価格が記録済みならスキップ
                return
    except Exception as e:
        print(f"[add_price_history] 当日価格の確認エラー: {e}")

    db.table("price_history").insert({
        "product_id": product_id,
        "price": price,
        "variation_name": variation_name,
        "recorded_at": datetime.utcnow().isoformat(),
    }).execute()


async def add_price_history_for_variations(product_id: str, variations: list[dict]) -> None:
    """商品の全バリエーション分の価格履歴をまとめて記録する"""
    for v in variations:
        await add_price_history(product_id, v["price"], variation_name=v["name"])


async def get_price_history(product_id: str, limit: int = 90) -> list[dict]:
    """商品の価格履歴を取得する（後方互換用）"""
    db = get_db()
    res = db.table("price_history") \
        .select("price, recorded_at") \
        .eq("product_id", product_id) \
        .order("recorded_at", desc=False) \
        .limit(limit) \
        .execute()
    return res.data


async def get_price_history_by_variation(product_id: str, limit_per_variation: int = 90) -> dict:
    """商品の価格履歴を「バリエーション名ごと」にグループ化して取得する"""
    db = get_db()
    try:
        res = db.table("price_history") \
            .select("price, recorded_at, variation_name") \
            .eq("product_id", product_id) \
            .order("recorded_at", desc=False) \
            .limit(limit_per_variation * 20) \
            .execute()
    except Exception as e:
        print(f"[get_price_history_by_variation] エラー: {e}")
        return {}

    grouped: dict[str, list[dict]] = {}
    for row in (res.data or []):
        key = row.get("variation_name") or "価格"
        grouped.setdefault(key, [])
        if len(grouped[key]) < limit_per_variation:
            grouped[key].append({
                "price": row["price"],
                "recorded_at": row["recorded_at"],
            })

    return grouped


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
        .select("*, profiles(username)", count="exact") \
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


async def has_user_reviewed(product_id: str, user_id: str) -> bool:
    """ユーザーがすでにレビューを投稿済みか確認する"""
    db = get_db()
    try:
        res = db.table("reviews") \
            .select("id") \
            .eq("product_id", product_id) \
            .eq("user_id", user_id) \
            .limit(1) \
            .execute()
        return _first_or_none(res) is not None
    except Exception as e:
        print(f"[has_user_reviewed] エラー: {e}")
        return False


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
        found = _first_or_none(res)
        if found:
            return found
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
    """カテゴリの進捗をリセットする"""
    db = get_db()
    db.table("crawl_progress").delete().eq("category", category).execute()

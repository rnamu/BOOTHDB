# scheduler.py - 定期価格チェック・スクレイピングスケジューラー

import asyncio
import uuid
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from config import settings
from database import (
    get_all_products_for_scrape,
    update_product_price,
    add_price_history,
    add_price_history_for_variations,
    get_db,
    get_product_by_booth_id,
    create_product,
    get_crawl_progress,
    update_crawl_progress,
    save_product_variations,
)
from scraper import (
    scrape_price_only,
    scrape_booth_item,
    fetch_category_page_item_ids,
    CRAWL_CATEGORIES,
)

scheduler = AsyncIOScheduler()

# 1回のクロール実行で巡回する最大ページ数
CRAWL_PAGES_PER_RUN = 5


async def check_all_prices() -> None:
    """
    登録済み全商品の価格をチェックし、変動があれば価格履歴に記録する。
    バリエーションがある商品は全バリエーション分の価格をチェックする。
    BOOTHへの負荷軽減のためリクエスト間にscrape_delay_secondsの間隔を空ける。
    """
    print("[Scheduler] 価格チェック開始")

    products = await get_all_products_for_scrape()
    print(f"[Scheduler] チェック対象: {len(products)} 件")

    success_count = 0
    fail_count = 0

    for product in products:
        product_id = product["id"]
        booth_item_id = product["booth_item_id"]

        try:
            scraped = await scrape_booth_item(booth_item_id)

            if scraped:
                price = scraped["current_price"]

                db = get_db()
                res = db.table("products") \
                    .select("current_price") \
                    .eq("id", product_id) \
                    .maybe_single() \
                    .execute()

                current = (res.data or {}).get("current_price")

                if scraped.get("variations"):
                    await add_price_history_for_variations(product_id, scraped["variations"])
                elif price is not None:
                    await add_price_history(product_id, price)

                if current != price and price is not None:
                    await update_product_price(product_id, price)
                    print(f"[Scheduler] 価格変動: {booth_item_id} {current}円 → {price}円")

                success_count += 1
            else:
                fail_count += 1
                print(f"[Scheduler] 価格取得失敗: {booth_item_id}")

        except Exception as e:
            fail_count += 1
            print(f"[Scheduler] エラー item={booth_item_id}: {e}")

        await asyncio.sleep(settings.scrape_delay_seconds)

    print(f"[Scheduler] 価格チェック完了 成功:{success_count} 失敗:{fail_count}")


async def _register_item_if_new(item_id: str) -> bool:
    """
    商品IDが未登録なら新規登録する。
    Returns: 新規登録した場合True、既存または失敗の場合False
    """
    existing = await get_product_by_booth_id(item_id)
    if existing:
        return False

    scraped = await scrape_booth_item(item_id)
    if not scraped:
        return False

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

    return True


async def crawl_categories(pages_per_category: int = CRAWL_PAGES_PER_RUN) -> dict:
    """
    全カテゴリを巡回して新着商品を収集する。
    各カテゴリの進捗（last_page）を記録し、次回はその続きから再開する。
    """
    print("[Crawler] カテゴリクロール開始")

    total_new = 0
    total_checked = 0

    for category_name, category_slug in CRAWL_CATEGORIES.items():
        progress = await get_crawl_progress(category_name)
        start_page = (progress.get("last_page") or 0) + 1

        category_new = 0

        for page in range(start_page, start_page + pages_per_category):
            item_ids = await fetch_category_page_item_ids(category_slug, page)

            if not item_ids:
                print(f"[Crawler] {category_name}: page={page} で空。1ページ目に戻ります")
                await update_crawl_progress(category_name, 0, category_new)
                break

            for item_id in item_ids:
                total_checked += 1
                try:
                    is_new = await _register_item_if_new(item_id)
                    if is_new:
                        category_new += 1
                        total_new += 1
                except Exception as e:
                    print(f"[Crawler] 商品登録エラー item={item_id}: {e}")

                await asyncio.sleep(settings.scrape_delay_seconds)

            await update_crawl_progress(category_name, page, 0)

        print(f"[Crawler] {category_name}: 新規 {category_new} 件")

    print(f"[Crawler] クロール完了 チェック:{total_checked}件 新規登録:{total_new}件")
    return {"checked": total_checked, "new": total_new}


def start_scheduler() -> None:
    """スケジューラーを起動する（アプリ起動時に呼ぶ）"""
    scheduler.add_job(
        check_all_prices,
        trigger=IntervalTrigger(hours=settings.scrape_interval_hours),
        id="price_check",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        crawl_categories,
        trigger=CronTrigger(hour=18, minute=0),
        id="category_crawl",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    print(f"[Scheduler] 起動完了 - 価格チェック間隔: {settings.scrape_interval_hours}時間 / カテゴリクロール: 毎日JST3:00")


def stop_scheduler() -> None:
    """スケジューラーを停止する（アプリ終了時に呼ぶ）"""
    if scheduler.running:
        scheduler.shutdown()
        print("[Scheduler] 停止")

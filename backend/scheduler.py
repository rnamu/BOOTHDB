# scheduler.py - 定期価格チェック・スクレイピングスケジューラー

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from config import settings
from database import (
    get_all_products_for_scrape,
    update_product_price,
    add_price_history,
    get_db,
)
from scraper import scrape_price_only

scheduler = AsyncIOScheduler()


async def check_all_prices() -> None:
    """
    登録済み全商品の価格をチェックし、変動があれば価格履歴に記録する。
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
            price = await scrape_price_only(booth_item_id)

            if price is not None:
                # DBの現在価格を取得して変動確認
                db = get_db()
                res = db.table("products") \
                    .select("current_price") \
                    .eq("id", product_id) \
                    .maybe_single() \
                    .execute()

                current = (res.data or {}).get("current_price")

                # 価格履歴は常に記録（グラフ描画のため）
                await add_price_history(product_id, price)

                # 価格が変動した場合のみproductsテーブルを更新
                if current != price:
                    await update_product_price(product_id, price)
                    print(f"[Scheduler] 価格変動: {booth_item_id} {current}円 → {price}円")

                success_count += 1
            else:
                fail_count += 1
                print(f"[Scheduler] 価格取得失敗: {booth_item_id}")

        except Exception as e:
            fail_count += 1
            print(f"[Scheduler] エラー item={booth_item_id}: {e}")

        # BOOTH サーバーへの負荷軽減のため待機
        await asyncio.sleep(settings.scrape_delay_seconds)

    print(f"[Scheduler] 価格チェック完了 成功:{success_count} 失敗:{fail_count}")


def start_scheduler() -> None:
    """スケジューラーを起動する（アプリ起動時に呼ぶ）"""
    scheduler.add_job(
        check_all_prices,
        trigger=IntervalTrigger(hours=settings.scrape_interval_hours),
        id="price_check",
        replace_existing=True,
        max_instances=1,  # 同時実行を1つに制限
    )
    scheduler.start()
    print(f"[Scheduler] 起動完了 - 価格チェック間隔: {settings.scrape_interval_hours}時間")


def stop_scheduler() -> None:
    """スケジューラーを停止する（アプリ終了時に呼ぶ）"""
    if scheduler.running:
        scheduler.shutdown()
        print("[Scheduler] 停止")

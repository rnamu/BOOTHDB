# scraper.py - BOOTH商品情報スクレイパー

import re
import asyncio
import httpx
from bs4 import BeautifulSoup
from typing import Optional
from config import settings


# ==========================================
# BOOTH商品URLのバリデーション・ID抽出
# ==========================================

BOOTH_ITEM_URL_PATTERN = re.compile(
    r"https?://booth\.pm/(?:ja|en)/items/(\d+)"
)


def extract_booth_item_id(url: str) -> Optional[str]:
    """BOOTHの商品URLからアイテムIDを取得する"""
    match = BOOTH_ITEM_URL_PATTERN.match(url.strip())
    if match:
        return match.group(1)
    # shop.booth.pm 形式にも対応
    alt_pattern = re.compile(r"https?://[^.]+\.booth\.pm/items/(\d+)")
    alt_match = alt_pattern.match(url.strip())
    if alt_match:
        return alt_match.group(1)
    return None


def normalize_booth_url(item_id: str) -> str:
    """アイテムIDから正規URLを生成する"""
    return f"https://booth.pm/ja/items/{item_id}"


# ==========================================
# スクレイピング本体
# ==========================================

async def scrape_booth_item(item_id: str) -> Optional[dict]:
    """
    BOOTH商品ページをスクレイピングして商品情報を返す

    Returns:
        dict or None: 取得した商品情報。失敗時はNone
    """
    url = normalize_booth_url(item_id)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.scrape_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"[Scraper] HTTP error {e.response.status_code} for item {item_id}")
        return None
    except httpx.RequestError as e:
        print(f"[Scraper] Request error for item {item_id}: {e}")
        return None

    soup = BeautifulSoup(response.text, "lxml")

    # --- 商品名 ---
    title = _get_text(soup, "h2.item-name") \
        or _get_text(soup, '[data-product-name]') \
        or _get_meta(soup, "og:title")

    if not title:
        print(f"[Scraper] タイトルが取得できませんでした: {url}")
        return None

    # --- 価格 ---
    price = _extract_price(soup)

    # --- クリエイター名 ---
    creator_name = _get_text(soup, ".shop-name") \
        or _get_text(soup, '[data-shop-name]') \
        or _get_meta(soup, "og:site_name")

    # --- ショップ名 ---
    shop_name = _get_text(soup, ".shop-name a") or creator_name

    # --- サムネイル ---
    thumbnail_url = _get_meta(soup, "og:image")

    # --- 説明文 ---
    description = _get_meta(soup, "og:description") \
        or _get_text(soup, ".description")

    # --- カテゴリ ---
    category = _extract_category(soup)

    # --- 説明文からアバター名を抽出 ---
    avatar_names = _extract_avatar_names(description or "")

    return {
        "booth_item_id": item_id,
        "title": title.strip(),
        "creator_name": (creator_name or "").strip(),
        "shop_name": (shop_name or "").strip(),
        "current_price": price,
        "thumbnail_url": thumbnail_url,
        "booth_url": url,
        "category": category,
        "description": (description or "")[:2000],  # 最大2000文字
        "extracted_avatar_names": avatar_names,
    }


# ==========================================
# 価格のみ更新スクレイピング（定期実行用・軽量）
# ==========================================

async def scrape_price_only(item_id: str) -> Optional[int]:
    """価格のみを取得する（定期チェック用の軽量版）"""
    url = normalize_booth_url(item_id)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.scrape_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except Exception as e:
        print(f"[Scraper] 価格取得エラー item={item_id}: {e}")
        return None

    soup = BeautifulSoup(response.text, "lxml")
    return _extract_price(soup)


# ==========================================
# 内部ヘルパー関数
# ==========================================

def _get_text(soup: BeautifulSoup, selector: str) -> Optional[str]:
    """CSSセレクタで要素を取得してテキストを返す"""
    el = soup.select_one(selector)
    if el:
        return el.get_text(strip=True) or None
    return None


def _get_meta(soup: BeautifulSoup, property_name: str) -> Optional[str]:
    """OGPメタタグの値を取得する"""
    tag = soup.find("meta", property=property_name) \
        or soup.find("meta", attrs={"name": property_name})
    if tag and tag.get("content"):
        return tag["content"].strip() or None
    return None


def _extract_price(soup: BeautifulSoup) -> Optional[int]:
    """
    価格要素を複数のセレクタで試して数値に変換する
    BOOTH のHTML構造が変わっても対応しやすいよう複数候補を用意
    """
    selectors = [
        ".price",
        ".item-price",
        '[data-price]',
        ".js-buy-box-price",
        ".price-value",
    ]
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            raw = el.get("data-price") or el.get_text(strip=True)
            price = _parse_price_string(raw)
            if price is not None:
                return price

    # JSONLDから価格を探す（構造化データ）
    import json
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("offers"):
                offers = data["offers"]
                if isinstance(offers, dict):
                    raw_price = offers.get("price")
                elif isinstance(offers, list) and offers:
                    raw_price = offers[0].get("price")
                else:
                    raw_price = None
                if raw_price is not None:
                    return int(float(str(raw_price)))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    return None


def _parse_price_string(raw: Optional[str]) -> Optional[int]:
    """「¥1,200」「1200」などの文字列から整数を取り出す"""
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    if digits:
        return int(digits)
    return None


def _extract_category(soup: BeautifulSoup) -> Optional[str]:
    """カテゴリ情報をパンくずリストまたはタグから取得する"""
    breadcrumb = soup.select(".breadcrumb li, .breadcrumbs li")
    if len(breadcrumb) >= 2:
        return breadcrumb[-2].get_text(strip=True) or None

    tag_el = soup.select_one(".tag, .category-tag")
    if tag_el:
        return tag_el.get_text(strip=True) or None

    return None


# 対応アバター抽出用のキーワードリスト
KNOWN_AVATAR_NAMES = [
    "しなの", "シナノ",
    "マヌカ",
    "セレスティア", "Selestia",
    "桔梗", "キキョウ",
    "萌", "もえ",
    "ここあ", "ここア",
    "あのん", "アノン",
    "ライム", "Lime",
    "チセ", "Chise",
    "ミルク", "Milk",
    "フィア", "Fia",
    "心桜", "このは",
    "竜胆", "龍胆",
    "ルーシュカ",
    "狐雪", "きつね",
    "メリノ",
    "ヒナ", "雛",
]


def _extract_avatar_names(text: str) -> list[str]:
    """
    商品説明文から対応アバター名を抽出する
    既知のアバター名リストと照合する
    """
    found = []
    for name in KNOWN_AVATAR_NAMES:
        if name in text and name not in found:
            found.append(name)
    return found

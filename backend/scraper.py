# scraper.py - BOOTH商品情報スクレイパー
# DEBUG_VERSION_MARKER_001

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
# カテゴリ一覧クロール（巡回収集）
# ==========================================

# クロール対象カテゴリ（BOOTHのbrowseページ名）
CRAWL_CATEGORIES = {
    "3Dモデル": "3D%E3%83%A2%E3%83%87%E3%83%AB",
    "3Dキャラクター": "3D%E3%82%AD%E3%83%A3%E3%83%A9%E3%82%AF%E3%82%BF%E3%83%BC",
    "3D衣装": "3D%E8%A1%A3%E8%A3%85",
    "3D小道具": "3D%E5%B0%8F%E9%81%93%E5%85%B7",
    "3Dテクスチャ": "3D%E3%83%86%E3%82%AF%E3%82%B9%E3%83%81%E3%83%A3",
    "3D装飾品": "3D%E8%A3%85%E9%A3%BE%E5%93%81",
    "3D髪型": "3D%E9%AB%AA%E5%9E%8B",
}

ITEM_LINK_PATTERN = re.compile(r"/items/(\d+)")


async def fetch_category_page_item_ids(category_slug: str, page: int) -> list[str]:
    """
    カテゴリ一覧ページから商品IDのリストを取得する
    （新しい順に並んでいるため、page=1が常に最新）
    """
    url = f"https://booth.pm/ja/browse/{category_slug}?page={page}"

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
        print(f"[Crawler] 一覧ページ取得エラー {category_slug} page={page}: {e}")
        return []

    soup = BeautifulSoup(response.text, "lxml")

    item_ids: list[str] = []
    seen = set()
    for a in soup.select("a[href*='/items/']"):
        href = a.get("href", "")
        match = ITEM_LINK_PATTERN.search(href)
        if match:
            item_id = match.group(1)
            if item_id not in seen:
                seen.add(item_id)
                item_ids.append(item_id)

    return item_ids


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
    # og:titleは「商品名 - 店名 - BOOTH」の固定フォーマットなので、
    # 末尾を除去するだけで安定して商品名のみを取得できる
    title = None
    og_title = _get_meta(soup, "og:title")
    if og_title:
        title = _strip_booth_suffix(og_title)

    if not title:
        # フォールバック（og:titleが取れなかった場合のみ見出しを試す）
        title = _get_text(soup, "h2.item-name") or _get_text(soup, '[data-product-name]')

    if not title:
        print(f"[Scraper] タイトルが取得できませんでした: {url}")
        return None

    # --- 価格・バリエーション ---
    variations = _extract_variations(soup)
    if variations:
        # 全バリエーション中の最高額をトップ表示価格として採用
        price = max(v["price"] for v in variations)
    else:
        price = _extract_price(soup)

    # --- クリエイター名 ---
    creator_name = _get_text(soup, ".shop-name") \
        or _get_text(soup, '[data-shop-name]')

    # --- ショップ名 ---
    shop_name = _get_text(soup, ".shop-name a") or creator_name

    if not creator_name:
        # og:site_nameはBOOTH固定文言のため使わず、og:titleの店名部分から拾う
        og_title = _get_meta(soup, "og:title")
        creator_name = _extract_shop_name_from_og_title(og_title)
        shop_name = shop_name or creator_name

    # --- サムネイル ---
    thumbnail_url = _get_meta(soup, "og:image")

    # --- 説明文 ---
    # 商品説明本文のコンテナのみを対象にする（おすすめ商品一覧などは除外）
    description = _get_text(soup, ".js-market-item-detail-description") \
        or _get_text(soup, ".market-item-detail-description") \
        or _get_text(soup, "[data-item-description]")

    if not description:
        # フォールバック: og:descriptionはBOOTH固定の注意書きのことが多いため最終手段
        description = _get_meta(soup, "og:description")

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
        "variations": variations,
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


def _strip_booth_suffix(og_title: str) -> str:
    """
    'オリジナル3Dモデル「しなの」 - ポンデロニウム研究所 - BOOTH'
    のような og:title から、末尾の店名・'BOOTH' を除去して商品名のみ返す。
    """
    # 末尾の " - BOOTH" を除去
    text = re.sub(r"\s*-\s*BOOTH\s*$", "", og_title)
    # さらに末尾の " - 店名" を除去（ハイフン区切りの最後のセグメント）
    parts = text.split(" - ")
    if len(parts) >= 2:
        return parts[0].strip()
    return text.strip()


def _extract_shop_name_from_og_title(og_title: Optional[str]) -> Optional[str]:
    """
    'オリジナル3Dモデル「しなの」 - ポンデロニウム研究所 - BOOTH'
    のような og:title から、店名（末尾から2番目のセグメント）を取り出す。
    """
    if not og_title:
        return None
    text = re.sub(r"\s*-\s*BOOTH\s*$", "", og_title)
    parts = text.split(" - ")
    if len(parts) >= 2:
        return parts[-1].strip()
    return None


def _extract_price(soup: BeautifulSoup) -> Optional[int]:
    """
    価格要素を複数のセレクタ・方法で試して数値に変換する
    BOOTH のHTML構造が変わっても対応しやすいよう複数候補を用意
    """
    # 最優先: 購入ボタンの data-product-price 属性（BOOTHが商品ごとに
    # 正確な価格を埋め込んでいる属性。テキスト解析より確実）
    btn = soup.select_one('button[data-product-price]')
    if btn:
        price = _parse_price_string(btn.get("data-product-price"))
        if price is not None:
            return price

    selectors = [
        ".price",
        ".item-price",
        '[data-price]',
        ".js-buy-box-price",
        ".price-value",
        ".u-tpg-c8",
    ]
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            raw = el.get("data-price") or el.get_text(strip=True)
            price = _parse_price_string(raw)
            if price is not None and price > 0:
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
                    price = int(float(str(raw_price)))
                    if price > 0:
                        return price
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    # フォールバック: 価格表示は通常「¥ 数字」の直後に改行や空白が続く
    # 単独行として表示される（例: "¥ 0" や "¥ 5,000"）。
    # 文章中の金額言及（クレジット欄やお知らせ文）と区別するため、
    # 末尾が桁区切りの数字のみで終わる行を優先的に探す。
    page_text = soup.get_text()
    head_text = page_text[:3000]
    print(f"[DEBUG _extract_price] head_text先頭500文字: {head_text[:500]!r}")
    all_yen = re.findall(r"[¥￥]\s*[\d,]+", head_text)
    print(f"[DEBUG _extract_price] head_text内の全¥金額: {all_yen}")

    # 行単位で "¥" のみで構成される価格表示行を探す（最優先）
    line_pattern = re.compile(r"^[¥￥]\s*([\d,]+)\s*~?\s*$", re.MULTILINE)
    line_match = line_pattern.search(head_text)
    if line_match:
        price = _parse_price_string(line_match.group(1))
        if price is not None:
            return price

    # フォールバック: 通常のパターンマッチ（0円も正しく許容する）
    yen_pattern = re.compile(r"[¥￥]\s*([\d,]+)")
    match = yen_pattern.search(head_text)
    if match:
        price = _parse_price_string(match.group(1))
        if price is not None:
            return price

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


def _extract_variations(soup: BeautifulSoup) -> list[dict]:
    """
    商品ページのバリエーション一覧（名前＋価格）を抽出する。

    BOOTHの「カートに入れる」ボタンには以下の属性が必ず付与されている:
        data-product-name="商品名（一部省略される場合あり）"
        data-product-price="正確な価格（数値文字列）"
        data-product-variant="バリエーションID"

    テキスト解析よりもこの属性を直接読む方が圧倒的に確実なため、
    これを一次情報源とする。

    Returns:
        [{"name": "フルパック", "price": 5000, "sort_order": 0}, ...]
        バリエーションがない単一価格の商品の場合は空リストを返す。
    """
    # 「ギフトとして贈る」ボタン(add-gift)は同じ商品の重複データなので除外し、
    # 「カートに入れる」ボタン(add-cart)のみを対象にする
    buttons = soup.select('button[data-product-price].add-cart, button[data-product-price]:not(.add-gift)')

    # 上記セレクタがヒットしない場合のフォールバック：全件取得して後でadd-giftを除外
    if not buttons:
        buttons = soup.select('button[data-product-price]')

    variations: list[dict] = []
    seen_variant_ids = set()

    for btn in buttons:
        classes = btn.get("class") or []
        if "add-gift" in classes:
            continue  # ギフトボタンは商品名・価格が重複するためスキップ

        variant_id = btn.get("data-product-variant")
        if variant_id and variant_id in seen_variant_ids:
            continue
        if variant_id:
            seen_variant_ids.add(variant_id)

        price = _parse_price_string(btn.get("data-product-price"))
        if price is None:
            continue

        # バリエーション名を取得する。
        # まずボタン自身のdata-product-name属性を使い、それが省略形（末尾が...）
        # の場合は、ボタンの少し手前にあるバリエーション名要素から正式名を探す。
        raw_name = btn.get("data-product-name") or ""

        # ボタンが含まれる購入ブロック全体から、価格表示の直前にある
        # バリエーション名テキストを正式名として優先的に使う
        full_name = _find_variation_label_near_button(btn)
        name = full_name or raw_name

        if not name:
            continue

        variations.append({
            "name": name.strip()[:100],
            "price": price,
            "sort_order": len(variations),
        })

    # 同名・同価格の重複を除去（順序維持）
    seen = set()
    unique_variations = []
    for v in variations:
        key = (v["name"], v["price"])
        if key not in seen:
            seen.add(key)
            unique_variations.append(v)

    for idx, v in enumerate(unique_variations):
        v["sort_order"] = idx

    # バリエーションが1件以下の場合は「単一価格商品」とみなし空リストを返す
    if len(unique_variations) <= 1:
        return []

    return unique_variations


def _find_variation_label_near_button(btn) -> Optional[str]:
    """
    購入ボタンを含む親ブロックを遡り、その中にある「バリエーション名」と
    思われるテキスト（商品見出しや価格表示の直前のラベル要素）を探す。
    見つからない場合はNoneを返す（呼び出し元はdata-product-name属性にフォールバックする）。
    """
    # 購入ボタンから3階層程度親をたどり、その中の最初の見出し的要素を探す
    container = btn
    for _ in range(4):
        if container.parent is None:
            break
        container = container.parent

    if container is None:
        return None

    # コンテナ内の最初の小見出し（h3, h4等）またはラベル的なdivを探す
    for tag_name in ("h3", "h4", "h5"):
        el = container.find(tag_name)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text

    return None

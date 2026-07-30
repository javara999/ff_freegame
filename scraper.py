# -*- coding: utf-8 -*-
"""
Game deal scrapers:
  - Epic Games Store
  - CheapShark
  - GOG
  - IndieGala
  - STOVE
"""
import logging
import re
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
_HTML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}
_TIMEOUT = 20


def _get(url, html=False, **kwargs):
    headers = _HTML_HEADERS if html else _HEADERS
    if "headers" not in kwargs:
        kwargs["headers"] = headers
    resp = requests.get(url, timeout=_TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


_CS_BASE = "https://www.cheapshark.com/api/1.0"
_STORE_MAP = {
    "1": "steam",
    "3": "gmg",
    "7": "gog",
    "11": "humble",
    "15": "fanatical",
    "25": "epic",
    "30": "indiegala",
}
_CS_DEDICATED_STORE_IDS = {"1", "7", "30"}
_MC_CACHE = {}
_TITLE_SUFFIX_RE = re.compile(
    r"\b("
    r"complete|deluxe|ultimate|definitive|gold|premium|collector'?s|anniversary|remastered|remaster|"
    r"goty|game\s+of\s+the\s+year|enhanced|extended|special|standard|starter|pack|bundle|edition"
    r")\b",
    re.IGNORECASE,
)


def _normalize_game_title(title):
    text = str(title or "").lower()
    text = re.sub(r"[™®©]", " ", text)
    text = re.sub(r"[\[\(].*?[\]\)]", " ", text)
    text = re.sub(r"\b\d{4}\b", " ", text)
    text = re.sub(r"[:\-–—|_/]+", " ", text)
    text = _TITLE_SUFFIX_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _title_candidates(title):
    base = str(title or "").strip()
    normalized = _normalize_game_title(base)
    variants = []
    for item in (base, normalized):
        item = str(item or "").strip()
        if item and item not in variants:
            variants.append(item)
    if ":" in base:
        head = _normalize_game_title(base.split(":", 1)[0])
        if head and head not in variants:
            variants.append(head)
    if "-" in base:
        head = _normalize_game_title(base.split("-", 1)[0])
        if head and head not in variants:
            variants.append(head)
    return variants


def _metacritic_from_deal(d):
    try:
        score = int(float(d.get("metacriticScore") or 0))
    except Exception:
        score = 0
    link = d.get("metacriticLink") or ""
    if link and link.startswith("/"):
        link = "https://www.metacritic.com" + link
    return score, link


def _deal_match_score(deal, target):
    deal_title = _normalize_game_title(deal.get("title") or "")
    if not deal_title or not target:
        return 0
    if deal_title == target:
        return 100
    target_words = set(target.split())
    deal_words = set(deal_title.split())
    if len(target_words) <= 1:
        return 0
    if deal_title.startswith(target) or target.startswith(deal_title):
        return 80
    if not target_words or not deal_words:
        return 0
    overlap = len(target_words & deal_words)
    return int((overlap / max(len(target_words), len(deal_words))) * 70)


def _cheapshark_metacritic_lookup(title="", steam_appid=None):
    key = f"{steam_appid or ''}:{title or ''}".lower()
    if key in _MC_CACHE:
        return _MC_CACHE[key]
    if not (steam_appid or title):
        _MC_CACHE[key] = (0, "")
        return _MC_CACHE[key]
    try:
        best = None
        if steam_appid:
            deals = _get(f"{_CS_BASE}/deals", params={"pageSize": 5, "steamAppID": steam_appid}).json()
            best = next((deal for deal in deals or [] if int(float(deal.get("metacriticScore") or 0)) > 0), None)
            if best is None and deals:
                best = deals[0]
        if best is None:
            for candidate in _title_candidates(title):
                deals = _get(f"{_CS_BASE}/deals", params={"pageSize": 12, "title": candidate}).json()
                target = _normalize_game_title(candidate)
                ranked = sorted(
                    (deal for deal in deals or [] if int(float(deal.get("metacriticScore") or 0)) > 0),
                    key=lambda deal: _deal_match_score(deal, target),
                    reverse=True,
                )
                if ranked and _deal_match_score(ranked[0], target) >= 45:
                    best = ranked[0]
                    break
        _MC_CACHE[key] = _metacritic_from_deal(best or {})
    except Exception as e:
        log.debug("CheapShark metacritic lookup failed title=%s appid=%s: %s", title, steam_appid, e)
        _MC_CACHE[key] = (0, "")
    return _MC_CACHE[key]


def _cs_deal(d, override_platform=None):
    store_id = str(d.get("storeID", "1"))
    platform = override_platform or "cheapshark"
    try:
        disc = int(float(d.get("savings", 0)))
    except Exception:
        disc = 0
    try:
        orig = float(d.get("normalPrice", 0))
    except Exception:
        orig = 0.0
    try:
        curr = float(d.get("salePrice", 0))
    except Exception:
        curr = 0.0
    try:
        rating = float(d.get("steamRatingPercent") or 0)
    except Exception:
        rating = 0.0
    try:
        rc = int(d.get("steamRatingCount") or 0)
    except Exception:
        rc = 0
    deal_id = d.get("dealID", "")
    game_id = d.get("gameID", "")
    title = d.get("title", "")
    mc_score, mc_url = _metacritic_from_deal(d)
    is_free_period = curr == 0.0 and orig > 0.0
    return {
        "external_id": f"cs_{game_id}_{store_id}",
        "platform": platform,
        "title": title,
        "image_url": d.get("thumb", ""),
        "store_url": f"https://www.cheapshark.com/redirect?dealID={deal_id}" if deal_id else "https://www.cheapshark.com",
        "original_price": orig,
        "current_price": curr,
        "discount_pct": disc,
        "is_free_period": is_free_period,
        "free_start": None,
        "free_end": None,
        "genres": [],
        "rating": rating,
        "rating_count": rc,
        "metacritic_score": mc_score,
        "metacritic_url": mc_url,
    }


def fetch_cheapshark_deals(min_discount=75, max_pages=3):
    results, seen = [], set()
    for page in range(max_pages):
        try:
            deals = _get(f"{_CS_BASE}/deals", params={
                "lowerPrice": 0,
                "upperPrice": 0,
                "sortBy": "Savings",
                "desc": 1,
                "pageSize": 60,
                "pageNumber": page,
                "onSale": 1,
            }).json()
        except Exception as e:
            log.warning("CheapShark page %d failed: %s", page, e)
            break
        if not deals:
            break
        for d in deals:
            store_id = str(d.get("storeID", ""))
            if store_id in _CS_DEDICATED_STORE_IDS:
                continue
            try:
                disc = int(float(d.get("savings", 0)))
            except Exception:
                disc = 0
            if disc < min_discount:
                break
            try:
                orig = float(d.get("normalPrice", 0))
            except Exception:
                orig = 0.0
            try:
                curr = float(d.get("salePrice", 0))
            except Exception:
                curr = 0.0
            if not (curr == 0.0 and orig > 0.0):
                continue
            key = f"{d.get('gameID', '')}_{store_id}"
            if key in seen:
                continue
            seen.add(key)
            results.append(_cs_deal(d))
    return results


def _fetch_cs_store(store_id: str, min_discount=30, max_pages=3, free_only=False):
    platform = _STORE_MAP.get(store_id)
    if not platform:
        return []
    results, seen = [], set()
    for page in range(max_pages):
        try:
            deals = _get(f"{_CS_BASE}/deals", params={
                "storeID": store_id,
                "sortBy": "Savings",
                "desc": 1,
                "pageSize": 60,
                "pageNumber": page,
                "onSale": 1,
            }).json()
        except Exception as e:
            log.warning("CheapShark store=%s page %d failed: %s", store_id, page, e)
            break
        if not deals:
            break
        for d in deals:
            try:
                disc = int(float(d.get("savings", 0)))
            except Exception:
                disc = 0
            if disc < min_discount:
                break
            try:
                orig = float(d.get("normalPrice", 0))
            except Exception:
                orig = 0.0
            try:
                curr = float(d.get("salePrice", 0))
            except Exception:
                curr = 0.0
            if free_only and not (curr == 0.0 and orig > 0.0):
                continue
            key = f"{d.get('gameID', '')}_{store_id}"
            if key in seen:
                continue
            seen.add(key)
            results.append(_cs_deal(d, platform))
    return results


_STEAM_SEARCH_URL = "https://store.steampowered.com/search/results/"
_STEAM_FEATURED_URL = "https://store.steampowered.com/api/featuredcategories"
_STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"


def _steam_appdetails(appid: int):
    try:
        data = _get(_STEAM_APPDETAILS_URL, params={"appids": appid, "cc": "us", "l": "en"}).json()
        app_data = data.get(str(appid), {})
        if not app_data.get("success"):
            return None
        return app_data.get("data")
    except Exception as e:
        log.debug("Steam appdetails %d failed: %s", appid, e)
        return None


def _steam_game_dict(appid: int, detail: dict):
    price_overview = detail.get("price_overview") or {}
    original = price_overview.get("initial", 0) / 100
    current  = price_overview.get("final", 0) / 100
    discount = price_overview.get("discount_percent", 0)
    rec = detail.get("recommendations") or {}
    genres = [g.get("description", "") for g in (detail.get("genres") or [])]
    categories = [c.get("description", "") for c in (detail.get("categories") or [])]

    free_weekend = any("Free Weekend" in c for c in categories)

    # 상시 F2P 제외: is_free=True이면서 원가가 없는 경우 (행사 중 is_free=True로 바뀌므로 original > 0이면 행사로 간주)
    is_f2p = detail.get("is_free", False)
    permanently_free = is_f2p and original <= 0 and not free_weekend
    if permanently_free:
        return None

    # discount_percent=100 을 우선 신뢰 (final 값이 0이 아닌 경우도 있음 — Steam API 불일치)
    is_free_period = (original > 0.0 and (current == 0.0 or discount == 100)) or free_weekend
    if not is_free_period:
        return None
    mc_score, mc_url = _cheapshark_metacritic_lookup(detail.get("name", ""), steam_appid=appid)

    return {
        "external_id":    f"steam_{appid}",
        "platform":       "steam",
        "title":          detail.get("name", ""),
        "image_url":      detail.get("header_image", ""),
        "store_url":      f"https://store.steampowered.com/app/{appid}/",
        "original_price": original,
        "current_price":  current,
        "discount_pct":   discount,
        "is_free_period": True,
        "free_start": None, "free_end": None,
        "genres":         genres,
        "rating": 0.0, "rating_count": rec.get("total", 0),
        "metacritic_score": mc_score,
        "metacritic_url": mc_url,
    }


def _fetch_steam_search_appids(max_pages: int = 5) -> list:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        log.warning("BeautifulSoup not installed; Steam search skipped")
        return []
    appids, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            resp = _get(_STEAM_SEARCH_URL, params={
                "specials": 1, "maxprice": "free", "cc": "us", "l": "english",
                "infinite": 1, "start": (page - 1) * 25, "count": 25, "json": 1,
            }, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://store.steampowered.com/",
            })
            try:
                j = resp.json()
                html_chunk = j.get("results_html", "")
            except Exception:
                html_chunk = resp.text
            soup = BeautifulSoup(html_chunk, "html.parser")
            items = soup.find_all("a", attrs={"data-ds-appid": True})
            if not items:
                break
            for a in items:
                for part in a["data-ds-appid"].split(","):
                    try:
                        aid = int(part.strip())
                        if aid > 0 and aid not in seen:
                            seen.add(aid)
                            appids.append(aid)
                    except ValueError:
                        pass
            if len(items) < 25:
                break
        except Exception as e:
            log.warning("Steam search page %d failed: %s", page, e)
            break
    log.info("Steam search: %d appid(s)", len(appids))
    return appids


def _fetch_steam_featured_appids() -> list:
    appids, seen = [], set()
    try:
        data = _get(_STEAM_FEATURED_URL, params={"cc": "us", "l": "en"}).json()
    except Exception as e:
        log.warning("Steam featuredcategories failed: %s", e)
        return appids
    target_keys = {"specials", "coming_soon", "top_sellers", "new_releases", "free_to_play", "weekend_deals"}
    for key, val in data.items():
        if not isinstance(val, dict):
            continue
        if key not in target_keys:
            name = (val.get("name") or "").lower()
            if not any(k in name for k in ("free", "weekend", "special")):
                continue
        for item in (val.get("items") or []):
            aid = item.get("id") or item.get("appid")
            try:
                aid = int(aid)
                if aid > 0 and aid not in seen:
                    seen.add(aid)
                    appids.append(aid)
            except (ValueError, TypeError):
                pass
    log.info("Steam featured: %d appid(s)", len(appids))
    return appids


def fetch_steam_free(max_detail_calls: int = 60) -> list:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    seen, candidates = set(), []
    for aid in _fetch_steam_search_appids() + _fetch_steam_featured_appids():
        if aid not in seen:
            seen.add(aid)
            candidates.append(aid)
    candidates = candidates[:max_detail_calls]
    log.info("Steam: %d candidate(s) to validate", len(candidates))

    def _process(appid):
        detail = _steam_appdetails(appid)
        if not detail:
            return None
        if detail.get("type", "").lower() not in ("game", "bundle", ""):
            return None
        title = detail.get("name", "")
        if _DEMO_TITLE_RE.search(title):
            log.debug("Steam: skipping demo/trial: %s", title)
            return None
        game = _steam_game_dict(appid, detail)
        return game if game and game.get("is_free_period") else None

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_process, aid): aid for aid in candidates}
        for future in as_completed(futures):
            game = future.result()
            if game:
                results.append(game)

    log.info("Steam free: %d game(s) found", len(results))
    return results


def fetch_indiegala_deals():
    return _fetch_cs_store("30", min_discount=30)


_EPIC_GQL_URL = "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions"
_EPIC_INVALID_SLUGS = {"home", "", "/home", "[]"}
_DEMO_TITLE_RE = re.compile(r"\b(demo|prologue|trial|playtest|beta|free\s*to\s*play|f2p)\b", re.IGNORECASE)
# 한국 상점 페이지에 뜨는 "출시 절차 확인/준비 중" 문구 — 이 게임은 국내에서 다운로드가 불가능하다.
def _fetch_epic_kr_catalog():
    """KR 국가 기준 무료 프로모션 목록을 조회해 (id 집합, 제목 집합)을 반환한다.

    Epic은 한국 심의(출시 절차) 미확정 게임을 KR 국가 조회 결과에서 아예 제외하므로,
    US 목록에는 있지만 KR 목록에 없는 게임 = "대한민국에서의 출시 절차를 확인 또는
    준비 중입니다" 상태로 판별할 수 있다. 상점 HTML을 긁는 방식(봇 차단 403으로
    사실상 항상 실패)을 대체한다. 조회 실패 시 None을 반환하며, 이 경우 호출부는
    전부 "이용 가능"으로 간주한다(오탐 제외 방지).
    """
    try:
        data = _get(_EPIC_GQL_URL, params={"locale": "en", "country": "KR", "allowCountries": "KR"}).json()
    except Exception as e:
        log.warning("Epic KR catalog fetch failed(전체 '이용가능' 처리): %s", e)
        return None
    elements = data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", [])
    ids = {str(el.get("id") or "") for el in elements if el.get("id")}
    titles = {str(el.get("title") or "").strip().lower() for el in elements if el.get("title")}
    return ids, titles


def _epic_store_url(el):
    for m in el.get("offerMappings") or []:
        slug = (m.get("pageSlug") or "").strip()
        if slug and slug not in _EPIC_INVALID_SLUGS:
            return f"https://store.epicgames.com/ko/p/{slug}"
    for m in ((el.get("catalogNs") or {}).get("mappings") or []):
        slug = (m.get("pageSlug") or "").strip()
        if slug and slug not in _EPIC_INVALID_SLUGS:
            return f"https://store.epicgames.com/ko/p/{slug}"
    slug = (el.get("productSlug") or "").strip().rstrip("/")
    if slug and slug not in _EPIC_INVALID_SLUGS:
        return f"https://store.epicgames.com/ko/p/{slug}"
    slug = (el.get("urlSlug") or "").strip()
    if slug and slug not in _EPIC_INVALID_SLUGS:
        return f"https://store.epicgames.com/ko/p/{slug}"
    return "https://store.epicgames.com/ko/free-games"


def fetch_epic_free():
    try:
        data = _get(_EPIC_GQL_URL, params={"locale": "en", "country": "US", "allowCountries": "US"}).json()
    except Exception as e:
        log.warning("Epic fetch failed: %s", e)
        return []
    elements = data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", [])
    results = []
    seen_titles = set()
    for el in elements:
        title = (el.get("title") or "").strip()
        if not title or _DEMO_TITLE_RE.search(title):
            continue
        promo = el.get("promotions") or {}
        offers = [o for g in (promo.get("promotionalOffers") or []) for o in (g.get("promotionalOffers") or [])]
        upcoming = [o for g in (promo.get("upcomingPromotionalOffers") or []) for o in (g.get("promotionalOffers") or [])]
        price_info = (el.get("price") or {}).get("totalPrice") or {}
        decimals = (price_info.get("currencyInfo") or {}).get("decimals", 2)
        divisor = 10 ** decimals
        original = (price_info.get("originalPrice") or 0) / divisor
        disc_price = (price_info.get("discountPrice") or price_info.get("originalPrice") or 0) / divisor
        free_start = None
        free_end = None
        is_free_now = False
        active_disc_pct = None
        for o in offers:
            ds = o.get("discountSetting", {})
            if ds.get("discountType") == "PERCENTAGE":
                pct = ds.get("discountPercentage", 100)
                if pct == 0:
                    is_free_now = True
                    free_start = _parse_dt(o.get("startDate"))
                    free_end = _parse_dt(o.get("endDate"))
                    break
                active_disc_pct = pct
        is_upcoming_free = False
        if not is_free_now:
            for o in upcoming:
                ds = o.get("discountSetting", {})
                if ds.get("discountType") == "PERCENTAGE" and ds.get("discountPercentage", 100) == 0:
                    is_upcoming_free = True
                    free_start = _parse_dt(o.get("startDate"))
                    free_end = _parse_dt(o.get("endDate"))
                    break
        if not is_free_now and not is_upcoming_free:
            continue
        title_key = title.lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        if is_free_now:
            current_price = 0.0
            discount_pct = 100
        else:
            current_price = disc_price
            discount_pct = active_disc_pct if active_disc_pct is not None else (round((1 - disc_price / original) * 100) if original > 0 else 0)
        image_url = ""
        for img in el.get("keyImages") or []:
            if img.get("type") in ("DieselStoreFrontWide", "OfferImageWide", "Thumbnail"):
                image_url = img.get("url", "")
                break
        genres = [t.get("name", "") for t in (el.get("tags") or []) if t.get("groupName") == "genre"]
        results.append({
            "external_id": el.get("id") or el.get("urlSlug") or title,
            "platform": "epic",
            "title": title,
            "image_url": image_url,
            "store_url": _epic_store_url(el),
            "original_price": original,
            "current_price": current_price,
            "discount_pct": discount_pct,
            "is_free_period": is_free_now,
            "free_start": free_start,
            "free_end": free_end,
            "genres": genres,
            "rating": 0.0,
            "rating_count": 0,
        })
    kr_catalog = _fetch_epic_kr_catalog()
    for game in results:
        if kr_catalog is None:
            game["kr_available"] = True
        else:
            kr_ids, kr_titles = kr_catalog
            game["kr_available"] = (
                str(game.get("external_id") or "") in kr_ids
                or str(game.get("title") or "").strip().lower() in kr_titles
            )
        log.info("Epic KR availability title=%s available=%s", game.get("title"), game["kr_available"])
    return results


_GOG_CATALOG_URL = "https://catalog.gog.com/v1/catalog"


def fetch_gog_free():
    try:
        data = _get(_GOG_CATALOG_URL, params={
            "limit": 48,
            "filters": "priceRange:free,0-0",
            "order": "desc:score",
            "productType": "in:game",
            "countryCode": "US",
            "locale": "en-US",
        }).json()
    except Exception as e:
        log.warning("GOG fetch failed: %s", e)
        return []
    results = []
    for p in data.get("products", []):
        title = (p.get("title") or "").strip()
        if not title or _DEMO_TITLE_RE.search(title):
            continue
        if (p.get("productType") or "").lower() == "demo":
            continue
        price_info = p.get("price") or {}
        final_money = price_info.get("finalMoney") or {}
        base_money = price_info.get("baseMoney") or {}
        try:
            curr = float(final_money.get("amount") or 0)
            orig = float(base_money.get("amount") or 0)
        except Exception:
            curr = orig = 0.0
        if curr > 0.0:
            continue
        slug = p.get("slug", "")
        results.append({
            "external_id": f"gog_{p.get('id', slug)}",
            "platform": "gog",
            "title": title,
            "image_url": p.get("coverHorizontal") or p.get("coverVertical") or "",
            "store_url": p.get("storeLink") or (f"https://www.gog.com/en/game/{slug}" if slug else "https://www.gog.com"),
            "original_price": orig,
            "current_price": 0.0,
            "discount_pct": 100 if orig > 0 else 0,
            "is_free_period": True,
            "free_start": None,
            "free_end": None,
            "genres": [g.get("name", "") for g in (p.get("genres") or [])],
            "rating": float(p.get("reviewsRating") or 0),
            "rating_count": int(p.get("reviewsCount") or 0),
        })
    return results


_INDIEGALA_FREE_URL = "https://freebies.indiegala.com/"


def fetch_indiegala_free():
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(_get(_INDIEGALA_FREE_URL, html=True).text, "html.parser")
    except Exception as e:
        log.warning("IndieGala freebies fetch failed: %s", e)
        return []
    results = []
    for col in soup.find_all("div", class_="products-col-inner"):
        try:
            img = col.find("img")
            if not img:
                continue
            image_url = img.get("data-img-src") or img.get("src") or ""
            title_div = col.find("div", class_="product-title")
            if title_div:
                title = title_div.get_text(strip=True)
            else:
                title = re.sub(r"\s+product image\s*$", "", img.get("alt", ""), flags=re.IGNORECASE).strip()
            if not title:
                continue
            link_tag = col.find("a", class_="fit-click")
            if link_tag and link_tag.get("href"):
                href = link_tag["href"]
                store_url = href if href.startswith("http") else "https://freebies.indiegala.com" + href
            else:
                store_url = _INDIEGALA_FREE_URL
            img_id = re.search(r"/([a-f0-9]{8}-[a-f0-9\\-]{4,})/", image_url)
            external_id = f"ig_free_{img_id.group(1)}" if img_id else f"ig_free_{re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_')}"
            results.append({
                "external_id": external_id,
                "platform": "indiegala",
                "title": title,
                "image_url": image_url,
                "store_url": store_url,
                "original_price": 0.0,
                "current_price": 0.0,
                "discount_pct": 100,
                "is_free_period": True,
                "free_start": None,
                "free_end": None,
                "genres": [],
                "rating": 0.0,
                "rating_count": 0,
            })
        except Exception:
            continue
    return results


def fetch_stove_free():
    try:
        import json
        from bs4 import BeautifulSoup
    except Exception:
        log.warning("BeautifulSoup not installed; STOVE skipped")
        return []

    def _abs_url(url):
        if not url:
            return ""
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("//"):
            return "https:" + url
        return "https://store.onstove.com" + url

    def _to_float(value):
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        text = re.sub(r"[^0-9.]+", "", str(value))
        if not text:
            return 0.0
        try:
            return float(text)
        except Exception:
            return 0.0

    def _price_values(text):
        values = []
        for match in re.finditer(r"(?:USD|KRW|[₩$])\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text):
            try:
                values.append(float(match.group(1).replace(",", "")))
            except Exception:
                pass
        return values

    def _iter_dicts(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                for item in _iter_dicts(value):
                    yield item
        elif isinstance(node, list):
            for value in node:
                for item in _iter_dicts(value):
                    yield item

    def _pick(dct, keys):
        for key in keys:
            value = dct.get(key)
            if value not in (None, "", [], {}):
                return value
        return None

    def _store_url(game_id, data):
        url = _pick(data, ["storeUrl", "store_url", "productUrl", "product_url", "url"])
        if url:
            return _abs_url(str(url).strip())
        return "https://store.onstove.com/ko/games/" + str(game_id)

    def _build_game(data):
        if isinstance(data.get("product"), dict):
            data = data["product"]
        game_id = _pick(data, ["productNo", "productId", "product_id", "product_no", "game_no"])
        if game_id in (None, ""):
            return None
        game_id = str(game_id).strip()
        title = _pick(data, ["productName", "name", "title", "product_name"])
        if not title:
            return None
        title = " ".join(str(title).split())
        if _DEMO_TITLE_RE.search(title):
            return None

        amount = data.get("amount") if isinstance(data.get("amount"), dict) else {}
        current_price = _to_float(_pick(data, ["salePrice", "discountPrice", "currentPrice", "finalPrice", "price", "sellingPrice", "sale_price"]) or amount.get("sales_price"))
        original_price = _to_float(_pick(data, ["originPrice", "originalPrice", "listPrice", "basePrice", "priceBeforeDiscount", "normalPrice", "origin_price"]) or amount.get("original_price"))
        if original_price <= 0.0 or current_price != 0.0:
            return None

        image_url = _pick(data, ["imageUrl", "image_url", "thumbnailUrl", "thumbnail_image_url", "verticalImageUrl", "horizontalImageUrl", "coverImageUrl", "title_image_square", "title_image_rectangle"]) or ""
        return {
            "external_id": "stove_" + game_id,
            "platform": "stove",
            "title": title,
            "image_url": _abs_url(str(image_url).strip()),
            "store_url": _store_url(game_id, data),
            "original_price": original_price,
            "current_price": 0.0,
            "discount_pct": int(_to_float(amount.get("discount_rate")) or 100),
            "is_free_period": True,
            "free_start": None,
            "free_end": None,
            "genres": [],
            "rating": 0.0,
            "rating_count": 0,
        }

    detail_price_re = re.compile(
        r"-100%\s+[^0-9]{0,5}([0-9][0-9,]*(?:\.[0-9]+)?)\s+[^0-9]{0,5}(0(?:\.0+)?)\b"
    )
    detail_cache = {}

    def _verify_detail_free_promo(session, store_url):
        if store_url in detail_cache:
            return detail_cache[store_url]
        try:
            html = _stove_get(session, store_url, html=True, headers=html_headers).text
        except Exception as e:
            log.warning("STOVE detail fetch failed: %s (%s)", store_url, e)
            detail_cache[store_url] = None
            return None

        text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
        match = detail_price_re.search(text)
        if not match:
            detail_cache[store_url] = None
            return None

        original_price = _to_float(match.group(1))
        current_price = _to_float(match.group(2))
        if original_price <= 0.0 or current_price != 0.0:
            detail_cache[store_url] = None
            return None

        verified = {
            "original_price": original_price,
            "current_price": current_price,
            "discount_pct": 100,
        }
        detail_cache[store_url] = verified
        return verified

    def _collect_games(payload, seen, session):
        games = []
        items = None
        if isinstance(payload, dict):
            candidate_lists = [value for value in payload.values() if isinstance(value, list)]
            if candidate_lists:
                items = max(candidate_lists, key=len)
        elif isinstance(payload, list):
            items = payload

        if items is not None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                game = _build_game(item)
                if not game:
                    continue
                verified = _verify_detail_free_promo(session, game["store_url"])
                if not verified:
                    continue
                game.update(verified)
                if game["external_id"] in seen:
                    continue
                seen.add(game["external_id"])
                games.append(game)
            return games

        for item in _iter_dicts(payload):
            game = _build_game(item)
            if not game:
                continue
            verified = _verify_detail_free_promo(session, game["store_url"])
            if not verified:
                continue
            game.update(verified)
            if game["external_id"] in seen:
                continue
            seen.add(game["external_id"])
            games.append(game)
        return games

    def _resolve_nuxt_refs(data, value, stack=None):
        if stack is None:
            stack = set()
        if isinstance(value, int) and not isinstance(value, bool):
            if value < 0 or value >= len(data) or value in stack:
                return None
            target = data[value]
            if isinstance(target, (dict, list)):
                stack.add(value)
                return _resolve_nuxt_refs(data, target, stack)
            return target
        if isinstance(value, dict):
            return {key: _resolve_nuxt_refs(data, item, set(stack)) for key, item in value.items()}
        if isinstance(value, list):
            return [_resolve_nuxt_refs(data, item, set(stack)) for item in value]
        return value

    def _extract_json_from_html(html):
        payloads = []
        for match in re.finditer(r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
            raw = match.group(1).strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
                if 'id="__NUXT_DATA__"' in match.group(0) and isinstance(payload, list):
                    payloads.append([
                        _resolve_nuxt_refs(payload, item)
                        for item in payload
                        if isinstance(item, dict)
                    ])
                else:
                    payloads.append(payload)
            except Exception:
                pass
        for pattern in [r"__NUXT__\s*=\s*(\{.*?\})\s*</script>", r"window\.__STORE__\s*=\s*(\{.*?\})\s*;"]:
            for match in re.finditer(pattern, html, re.I | re.S):
                try:
                    payloads.append(json.loads(match.group(1)))
                except Exception:
                    pass
        return payloads

    def _stove_get(session, url, html=False, **kwargs):
        headers = _HTML_HEADERS if html else _HEADERS
        if "headers" not in kwargs:
            kwargs["headers"] = headers
        resp = session.get(url, timeout=_TIMEOUT, allow_redirects=True, **kwargs)
        resp.raise_for_status()
        return resp

    api_headers = dict(_HEADERS, Referer="https://store.onstove.com/", Accept="application/json")
    html_headers = dict(_HTML_HEADERS, Referer="https://store.onstove.com/")
    api_urls = []
    page_urls = [
        "https://store.onstove.com/ko/store/Discount_Mall",
        "https://store.onstove.com/ko/store/stoveindie",
        "https://store.onstove.com/ko/games?priceFilter=FREE",
    ]
    results = []
    seen = set()
    session = requests.Session()

    try:
        _stove_get(session, "https://store.onstove.com/", html=True, headers=html_headers)
    except Exception as e:
        log.warning("STOVE session bootstrap failed: %s", e)

    for api_url in api_urls:
        try:
            payload = _stove_get(session, api_url, headers=api_headers).json()
        except Exception as e:
            log.warning("STOVE API fetch failed: %s (%s)", api_url, e)
            continue
        results.extend(_collect_games(payload, seen, session))
        if results:
            log.info("STOVE free: %d game(s) found via API", len(results))
            return results

    for page_url in page_urls:
        try:
            html = _stove_get(session, page_url, html=True, headers=html_headers).text
        except Exception as e:
            log.warning("STOVE page fetch failed: %s (%s)", page_url, e)
            continue

        for payload in _extract_json_from_html(html):
            results.extend(_collect_games(payload, seen, session))
        if results:
            log.info("STOVE free: %d game(s) found via embedded JSON", len(results))
            return results

        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            match = re.search(r"/games/(\d+)", href)
            if not match:
                continue
            game_id = match.group(1)
            external_id = "stove_" + game_id
            if external_id in seen:
                continue

            block = anchor
            for _ in range(5):
                text = " ".join(block.stripped_strings)
                prices = _price_values(text)
                if "-100%" in text and prices and prices[-1] == 0.0 and any(value > 0.0 for value in prices[:-1]):
                    break
                if block.parent is None:
                    block = None
                    break
                block = block.parent
            if block is None:
                continue

            title = ""
            for tag in block.find_all(["h1", "h2", "h3", "h4", "strong"]):
                title = " ".join(tag.get_text(" ", strip=True).split())
                if title:
                    break
            if not title:
                title = game_id
            if _DEMO_TITLE_RE.search(title):
                continue

            prices = _price_values(" ".join(block.stripped_strings))
            if len(prices) < 2:
                continue
            original_price = 0.0
            for value in prices[:-1]:
                original_price = _to_float(value)
                if original_price > 0.0:
                    break
            if original_price <= 0.0 or _to_float(prices[-1]) != 0.0:
                continue

            image_url = ""
            image_node = block.find("img") or anchor.find("img")
            if image_node is not None:
                image_url = image_node.get("src") or image_node.get("data-src") or image_node.get("data-lazy-src") or ""

            verified = _verify_detail_free_promo(session, _abs_url(href))
            if not verified:
                continue

            seen.add(external_id)
            results.append({
                "external_id": external_id,
                "platform": "stove",
                "title": title,
                "image_url": _abs_url(str(image_url).strip()),
                "store_url": _abs_url(href),
                "original_price": verified["original_price"],
                "current_price": verified["current_price"],
                "discount_pct": verified["discount_pct"],
                "is_free_period": True,
                "free_start": None,
                "free_end": None,
                "genres": [],
                "rating": 0.0,
                "rating_count": 0,
            })

        if results:
            log.info("STOVE free: %d game(s) found via HTML fallback", len(results))
            return results

    log.info("STOVE free: %d game(s) found", len(results))
    return results


def fetch_stove_deals():
    return fetch_stove_free()


def _enrich_metacritic(items):
    for item in items or []:
        if int(item.get("metacritic_score") or 0) > 0:
            continue
        score, url = _cheapshark_metacritic_lookup(item.get("title") or "")
        item["metacritic_score"] = score
        item["metacritic_url"] = url
    return items


def fetch_all():
    data = {
        "epic": fetch_epic_free(),
        "steam": fetch_steam_free(),
        "cheapshark": fetch_cheapshark_deals(),
        "gog": fetch_gog_free(),
        "indiegala": fetch_indiegala_deals(),
        "indiegala_free": fetch_indiegala_free(),
        "stove": fetch_stove_deals(),
    }
    for items in data.values():
        _enrich_metacritic(items)
    return data


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).rstrip("Z")).replace(tzinfo=timezone.utc)
    except Exception:
        return None

"""Booking.com 含稅總價飯店爬蟲模組。"""

import re
from typing import Any, Optional
from urllib.parse import quote, urlparse

from playwright.async_api import BrowserContext

from src.models import HotelPriceResult
from src.scrapers.base import BaseHotelScraper

_RESOLVED_BOOKING_CACHE: dict[str, str] = {}

PRICE_RE = re.compile(r"(?:TWD|NT\$|TW\$|JPY|USD|HKD|SGD|\$|¥)?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
GENERIC_NAME_SUFFIX_RE = re.compile(
    r"(?:酒店集團|大飯店|大酒店|飯店|酒店|渡假村|度假村|旅館|民宿|hotel|hotels|resort|villa)",
    re.IGNORECASE,
)
BREAKFAST_INCLUDED_KEYWORDS = (
    "含早餐",
    "包括早餐",
    "包括自助早餐",
    "包含早餐",
    "包含自助早餐",
    "breakfast included",
)


def _clean_hotel_tokens(hotel_name: str) -> tuple[str, list[str]]:
    cleaned = GENERIC_NAME_SUFFIX_RE.sub("", hotel_name or "").strip().lower()
    cleaned_compact = re.sub(r"[\s\-_/・]+", "", cleaned)
    words = [
        tok
        for tok in re.split(r"[\s\-_/・,()]+", cleaned)
        if len(tok) >= 2 and tok not in {"by", "the", "and", "in", "of", "at"}
    ]
    return cleaned_compact, words


def _is_hotel_title_match(card_title: str, hotel_name: str) -> bool:
    if not card_title or not hotel_name:
        return False
    card_compact, card_words = _clean_hotel_tokens(card_title)
    query_compact, query_words = _clean_hotel_tokens(hotel_name)
    if not query_compact:
        return False
    if query_compact in card_compact or (len(card_compact) >= 2 and card_compact in query_compact):
        return True
    # 比對較具辨識度的片段（排除過泛的單獨詞）
    significant_query = [w for w in query_words if w not in {"okinawa", "naha", "沖繩", "那霸"}]
    return any(len(w) >= 3 and w in card_compact for w in significant_query)


def _parse_amount(raw_text: str) -> Optional[float]:
    if not raw_text:
        return None
    matches = PRICE_RE.findall(raw_text.replace("\xa0", " "))
    for token in reversed(matches):
        cleaned = token.replace(",", "")
        try:
            val = float(cleaned)
            if val > 0:
                return val
        except ValueError:
            continue
    return None


def _parse_total_price_with_tax(price_text: str, tax_text: str) -> Optional[float]:
    base_price = _parse_amount(price_text)
    if base_price is None:
        return None

    normalized_tax = (tax_text or "").replace("\xa0", " ").strip()
    if normalized_tax.startswith("+") or (
        "+" in normalized_tax and any(k in normalized_tax.lower() for k in ("稅", "fee", "tax", "費用"))
    ):
        tax_val = _parse_amount(normalized_tax)
        if tax_val is not None:
            return float(round(base_price + tax_val))

    return float(round(base_price))


def _row_fits_guests(row: dict[str, Any], adults: int, children: int) -> bool:
    occ_text = str(row.get("occ") or "").replace("\xa0", " ").strip()
    if not occ_text:
        adult_icons = int(row.get("occAdultsIcons") or 0)
        child_icons = int(row.get("occChildrenIcons") or 0)
        if adult_icons > 0:
            if adults > adult_icons:
                return False
            remaining = adult_icons - adults
            return children <= (child_icons + remaining)
        return True

    strict_m = re.search(r"限\s*(\d+)\s*人入住", occ_text)
    if strict_m and (adults + children) > int(strict_m.group(1)):
        return False

    total_m = re.search(r"最多(?:人數|可住)[：:]?\s*(\d+)", occ_text)
    if total_m and (adults + children) > int(total_m.group(1)):
        return False

    adult_m = re.search(r"(?:最多成人人數|Max(?:imum)?\s*adults?)[：:]?\s*(\d+)", occ_text, re.IGNORECASE)
    child_m = re.search(r"(?:最多兒童人數|Max(?:imum)?\s*children)[：:]?\s*(\d+)", occ_text, re.IGNORECASE)
    if adult_m is not None:
        max_adults = int(adult_m.group(1))
        if adults > max_adults:
            return False
        max_children = int(child_m.group(1)) if child_m is not None else 0
        remaining_adult_slots = max_adults - adults
        return children <= (max_children + remaining_adult_slots)

    return True


def _is_row_breakfast_included(cond_text: str) -> bool:
    low = (cond_text or "").lower()
    return any(keyword in low for keyword in BREAKFAST_INCLUDED_KEYWORDS)


def extract_booking_room_table_price(
    rows_data: list[dict[str, Any]],
    adults: int = 2,
    children: int = 0,
    breakfast_only: bool = False,
) -> tuple[Optional[float], str]:
    """從 Booking.com 飯店頁面 `#hprt-table` 列資料擷取符合人數與早餐條件的最低含稅總價。"""
    best_price: Optional[float] = None
    best_room = ""

    for row in rows_data:
        if not _row_fits_guests(row, adults=adults, children=children):
            continue
        cond_text = str(row.get("cond") or "")
        if breakfast_only and not _is_row_breakfast_included(cond_text):
            continue

        total_price = _parse_total_price_with_tax(
            str(row.get("price") or ""),
            str(row.get("tax") or ""),
        )
        if total_price is None or total_price <= 0:
            continue

        if best_price is None or total_price < best_price:
            best_price = total_price
            best_room = str(row.get("room") or "").strip()

    if best_price is None:
        return None, ""
    return best_price, best_room


def extract_booking_price_from_cards(
    cards_data: list[dict[str, Any]],
    hotel_name: str,
    booking_path: str = "",
    breakfast_only: bool = False,
) -> tuple[Optional[float], str, str]:
    """從 Booking.com 搜尋結果卡片清單比對目標飯店，回傳 `(含稅總價, 房型, 飯店路徑)`。"""
    normalized_target_path = booking_path.strip().lower()
    matched_path = ""

    for card in cards_data:
        card_title = str(card.get("title") or "").strip()
        raw_href = str(card.get("href") or card.get("path") or "").strip()
        card_path = urlparse(raw_href).path if raw_href else ""

        if normalized_target_path:
            if normalized_target_path not in card_path.lower():
                continue
        elif not _is_hotel_title_match(card_title, hotel_name):
            continue

        if not matched_path and card_path:
            matched_path = card_path

        units_text = str(card.get("units") or "")
        if breakfast_only and not _is_row_breakfast_included(units_text):
            continue

        prices_list = card.get("prices")
        if isinstance(prices_list, list) and prices_list:
            raw_price = str(prices_list[-1] or "")
        else:
            raw_price = str(card.get("price") or "")

        total_price = _parse_total_price_with_tax(raw_price, str(card.get("tax") or ""))
        if total_price is None or total_price <= 0:
            continue

        room_type = ""
        for line in units_text.splitlines():
            clean_ln = line.strip()
            if not clean_ln or "推薦給您的團體" in clean_ln or "包括早餐" in clean_ln or "免費取消" in clean_ln:
                continue
            if re.fullmatch(r"\d+×", clean_ln):
                continue
            room_type = clean_ln
            break

        return total_price, room_type, card_path or matched_path

    return None, "", matched_path


class BookingScraper(BaseHotelScraper):
    """Booking.com 台灣繁中版含稅總價爬蟲。"""

    def __init__(
        self,
        hotel_path: str = "",
        adults: int = 2,
        children: int = 0,
        child_ages: str = "",
        rooms: int = 1,
        currency: str = "TWD",
        breakfast_only: bool = False,
        ip_info: str = "",
    ) -> None:
        raw_path = hotel_path.strip()
        if raw_path and not raw_path.startswith("/"):
            raw_path = "/" + raw_path
        self.hotel_path = raw_path
        self.adults = adults
        self.children = children
        self.child_ages = child_ages.strip()
        self.rooms = rooms
        self.currency = currency.strip().upper() or "TWD"
        self.breakfast_only = breakfast_only
        self.ip_info = ip_info.strip()
        meal_label = "含早餐" if self.breakfast_only else "不限早餐"
        self.platform_name = f"Booking [{meal_label} | IP:{self.ip_info or 'LOCAL'}]"

    def _guest_query_params(self, check_in: str, check_out: str) -> str:
        params = (
            f"checkin={check_in}&checkout={check_out}"
            f"&group_adults={self.adults}&no_rooms={self.rooms}&group_children={self.children}"
        )
        if self.children > 0:
            ages = [a.strip() for a in self.child_ages.split(",") if a.strip()]
            for idx in range(self.children):
                age_val = ages[idx] if idx < len(ages) else "11"
                params += f"&age={quote(age_val)}"
        params += f"&selected_currency={self.currency}&lang=zh-tw"
        return params

    def build_detail_url(self, hotel_path: str, check_in: str, check_out: str) -> str:
        clean_path = hotel_path.strip()
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path
        return f"https://www.booking.com{clean_path}?{self._guest_query_params(check_in, check_out)}"

    def build_search_url(self, hotel_name: str, check_in: str, check_out: str) -> str:
        if self.hotel_path:
            return self.build_detail_url(self.hotel_path, check_in, check_out)
        url = (
            f"https://www.booking.com/searchresults.zh-tw.html"
            f"?ss={quote(hotel_name)}&{self._guest_query_params(check_in, check_out)}"
        )
        if self.breakfast_only:
            url += "&nflt=mealplan%3D1"
        return url

    async def _scrape_detail_table(
        self,
        page,
        detail_url: str,
        hotel_name: str,
        check_in: str,
        check_out: str,
    ) -> Optional[HotelPriceResult]:
        await page.goto(detail_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)

        body_text = (await page.locator("body").inner_text()).lower()
        if any(flag in body_text for flag in ("verify you are human", "captcha", "access denied", "human verification")):
            raise RuntimeError("觸發 Booking.com 人機驗證，已停止該飯店抓取")

        rows_data: list[dict[str, Any]] = await page.evaluate(
            """() => {
                const trs = Array.from(document.querySelectorAll('#hprt-table tbody tr'));
                let curRoom = '';
                return trs.map((tr) => {
                    const roomEl = tr.querySelector('.hprt-roomtype-icon-link');
                    if (roomEl && roomEl.innerText.trim()) curRoom = roomEl.innerText.trim();
                    const occEl = tr.querySelector('.hprt-occupancy-occupancy-info, td.hprt-table-cell-occupancy');
                    const priceEl = tr.querySelector('.prco-valign-middle-helper, .bui-price-display__value');
                    const taxEl = tr.querySelector('.prd-taxes-and-fees-under-price');
                    const condEl = tr.querySelector('td.hprt-table-cell-conditions');
                    const adultIconsEl = occEl ? occEl.querySelectorAll('.c-occupancy-icons__adults i') : [];
                    const adultIcons = adultIconsEl.length || (occEl ? occEl.querySelectorAll('.bicon-occupancy').length : 0);
                    const childIcons = occEl ? occEl.querySelectorAll('.c-occupancy-icons__children i').length : 0;
                    return {
                        room: curRoom,
                        occ: occEl ? occEl.textContent.replace(/\\s+/g, ' ').trim() : '',
                        occAdultsIcons: adultIcons,
                        occChildrenIcons: childIcons,
                        price: priceEl ? priceEl.innerText.trim() : '',
                        tax: taxEl ? taxEl.innerText.trim() : '',
                        cond: condEl ? condEl.innerText.replace(/\\s+/g, ' ').trim() : '',
                    };
                });
            }"""
        )

        price, room_type = extract_booking_room_table_price(
            rows_data=rows_data,
            adults=self.adults,
            children=self.children,
            breakfast_only=self.breakfast_only,
        )
        if price is None:
            return None

        return HotelPriceResult(
            hotel_name=hotel_name,
            platform=self.platform_name,
            check_in=check_in,
            check_out=check_out,
            price=price,
            currency=self.currency,
            room_type=room_type,
            url=detail_url,
        )

    async def fetch_price(
        self,
        context: BrowserContext,
        hotel_name: str,
        check_in: str,
        check_out: str,
    ) -> HotelPriceResult:
        resolved_path = self.hotel_path or _RESOLVED_BOOKING_CACHE.get(hotel_name, "")
        page = await context.new_page()
        try:
            if resolved_path:
                detail_url = self.build_detail_url(resolved_path, check_in, check_out)
                res = await self._scrape_detail_table(page, detail_url, hotel_name, check_in, check_out)
                if res is not None:
                    return res
                meal_desc = "含早餐" if self.breakfast_only else "符合人數"
                raise RuntimeError(f"Booking.com 目前無 {hotel_name} 的{meal_desc}可訂房型")

            search_url = self.build_search_url(hotel_name, check_in, check_out)
            await page.goto(search_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            body_text = (await page.locator("body").inner_text()).lower()
            if any(flag in body_text for flag in ("verify you are human", "captcha", "access denied", "human verification")):
                raise RuntimeError("觸發 Booking.com 人機驗證，已停止該飯店抓取")

            cards_data: list[dict[str, Any]] = await page.evaluate(
                """() => Array.from(document.querySelectorAll('[data-testid="property-card"]')).slice(0, 10).map((c) => ({
                    title: c.querySelector('[data-testid="title"]')?.innerText?.trim() || '',
                    prices: Array.from(c.querySelectorAll('[data-testid="price-and-discounted-price"]')).map((el) => el.innerText.trim()),
                    tax: c.querySelector('[data-testid="taxes-and-charges"]')?.innerText?.trim() || '',
                    units: c.querySelector('[data-testid="recommended-units"]')?.innerText?.trim() || '',
                    href: c.querySelector('a[data-testid="title-link"]')?.getAttribute('href') || '',
                }))"""
            )

            card_price, card_room, matched_path = extract_booking_price_from_cards(
                cards_data=cards_data,
                hotel_name=hotel_name,
                breakfast_only=self.breakfast_only,
            )
            if matched_path:
                _RESOLVED_BOOKING_CACHE[hotel_name] = matched_path
                detail_url = self.build_detail_url(matched_path, check_in, check_out)
                res = await self._scrape_detail_table(page, detail_url, hotel_name, check_in, check_out)
                if res is not None:
                    return res

            if card_price is not None:
                return HotelPriceResult(
                    hotel_name=hotel_name,
                    platform=self.platform_name,
                    check_in=check_in,
                    check_out=check_out,
                    price=card_price,
                    currency=self.currency,
                    room_type=card_room,
                    url=self.build_detail_url(matched_path, check_in, check_out) if matched_path else search_url,
                )

            raise RuntimeError(f"Booking.com 未找到 {hotel_name} 的可訂房型或含稅房價")
        finally:
            await page.close()

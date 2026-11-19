"""Agoda 含稅總價飯店爬蟲模組。"""

import uuid
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

from playwright.async_api import BrowserContext, Page, Response

from src.models import HotelPriceResult
from src.scrapers.base import BaseHotelScraper

CURRENCY_UI_HINTS: dict[str, tuple[str, ...]] = {
    "TWD": ("NT$", "TWD", "新臺幣", "新台幣"),
    "USD": ("USD", "US$"),
    "JPY": ("JPY", "¥", "日圓"),
    "HKD": ("HKD", "HK$"),
    "SGD": ("SGD", "S$"),
}
CURRENCY_FALLBACK_IDS: dict[str, int] = {
    "TWD": 28,
}
MIN_VALID_TWD_NIGHTLY_PRICE = 400.0


def _compute_los(check_in: str, check_out: str) -> int:
    try:
        dt_in = datetime.strptime(check_in, "%Y-%m-%d")
        dt_out = datetime.strptime(check_out, "%Y-%m-%d")
        days = (dt_out - dt_in).days
        return days if days > 0 else 1
    except ValueError:
        return 1


def _detect_payload_currency(payload: dict[str, Any]) -> str:
    cur_info = payload.get("currencyInfo")
    if isinstance(cur_info, dict):
        code = str(cur_info.get("code") or "").strip().upper()
        if code:
            return code
    grid = payload.get("roomGridData")
    if isinstance(grid, dict):
        for master_room in grid.get("masterRooms", []) or []:
            for room in master_room.get("rooms", []) or []:
                room_cur = str(room.get("currency") or "").strip().upper()
                if room_cur:
                    return room_cur
    return ""


def extract_agoda_room_grid_price(
    room_grid_data: dict[str, Any],
    breakfast_only: bool = False,
    expected_currency: str = "",
    room_keyword: str = "",
) -> tuple[Optional[float], str]:
    """從 Agoda `GetSecondaryData` 的 `roomGridData` 擷取符合人數與可訂狀態的最低含稅總價。"""
    expected_norm = expected_currency.strip().upper()
    kw = room_keyword.strip().lower()
    best_price: Optional[float] = None
    best_room_name = ""

    for master_room in room_grid_data.get("masterRooms", []) or []:
        master_name = str(master_room.get("name") or "").strip()
        for room in master_room.get("rooms", []) or []:
            room_cur = str(room.get("currency") or "").strip().upper()
            if expected_norm and room_cur and room_cur != expected_norm:
                raise ValueError(f"Agoda 房型幣別不符（預期 {expected_norm}，實際 {room_cur}）")

            if room.get("isFit") is False:
                continue
            avail = room.get("availability")
            if avail is not None and avail <= 0:
                continue
            if breakfast_only and not bool(room.get("isBreakfastIncluded")):
                continue

            room_display_name = str(room.get("name") or master_name).strip()
            if kw and kw not in f"{room_display_name} {master_name}".lower():
                continue

            total_val = (room.get("totalPrice") or {}).get("display")
            inclusive_val = (room.get("inclusivePrice") or {}).get("display")
            numeric_price: Optional[float] = None
            for candidate in (total_val, inclusive_val):
                if candidate is None:
                    continue
                try:
                    parsed = float(candidate)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    numeric_price = parsed
                    break
            if numeric_price is None:
                continue

            if best_price is None or numeric_price < best_price:
                best_price = numeric_price
                best_room_name = str(room.get("name") or master_name).strip()

    if best_price is None:
        return None, ""

    rounded = float(round(best_price))
    if expected_norm == "TWD" and rounded < MIN_VALID_TWD_NIGHTLY_PRICE:
        raise ValueError(f"Agoda 回傳疑似外幣金額 ({expected_norm} {rounded:.0f})，已拒絕寫入")
    return rounded, best_room_name


class AgodaScraper(BaseHotelScraper):
    """Agoda 飯店含稅總價爬蟲（透過背景 `GetSecondaryData` JSON 取得含稅價格）。"""

    def __init__(
        self,
        hotel_path: str,
        adults: int = 2,
        children: int = 1,
        child_ages: str = "11",
        rooms: int = 1,
        currency: str = "TWD",
        breakfast_only: bool = False,
        ip_info: str = "",
        room_keyword: str = "",
    ) -> None:
        cleaned_path = hotel_path.strip()
        if not cleaned_path.startswith("/"):
            cleaned_path = "/" + cleaned_path
        self.hotel_path = cleaned_path
        self.adults = adults
        self.children = children
        self.child_ages = child_ages.strip()
        self.rooms = rooms
        self.currency = currency.strip().upper() or "TWD"
        self.breakfast_only = breakfast_only
        self.ip_info = ip_info.strip()
        self.room_keyword = room_keyword.strip()
        meal_label = "含早餐" if self.breakfast_only else "不限早餐"
        self.platform_name = f"Agoda [{meal_label} | IP:{self.ip_info or 'LOCAL'}]"

    def build_search_url(self, check_in: str, check_out: str, rooms: Optional[int] = None) -> str:
        los = _compute_los(check_in, check_out)
        target_rooms = rooms if rooms is not None else self.rooms
        url = (
            f"https://www.agoda.com{self.hotel_path}"
            f"?checkIn={check_in}&los={los}&rooms={target_rooms}"
            f"&adults={self.adults}&children={self.children}"
        )
        if self.children > 0 and self.child_ages:
            url += f"&childages={quote(self.child_ages)}"
        url += f"&currencyCode={self.currency}"
        return url

    async def _seed_currency_cookies(self, context: BrowserContext) -> None:
        existing = [c for c in await context.cookies("https://www.agoda.com") if c.get("name") == "agoda.version.03"]
        if existing and f"CurLabel={self.currency}" in (existing[0].get("value") or ""):
            return
        await context.add_cookies(
            [
                {
                    "name": "agoda.version.03",
                    "value": f"CookieId={uuid.uuid4()}&DLang=zh-tw&CurLabel={self.currency}",
                    "domain": ".agoda.com",
                    "path": "/",
                },
                {
                    "name": "agoda.consent",
                    "value": "TW||2026-11-18 00:00:00Z",
                    "domain": ".agoda.com",
                    "path": "/",
                },
            ]
        )

    async def _ui_currency_matches(self, page: Page) -> Optional[bool]:
        curr_btn = page.locator('[data-selenium="currency-container-selected-currency"]')
        if await curr_btn.count() == 0:
            return None
        ui_text = (await curr_btn.first.inner_text()).strip().upper()
        if not ui_text:
            return None
        hints = CURRENCY_UI_HINTS.get(self.currency, (self.currency,))
        return any(h.upper() in ui_text for h in hints)

    async def _switch_currency_on_page(self, page: Page) -> bool:
        curr_btn = page.locator('[data-selenium="currency-container-selected-currency"]')
        if await curr_btn.count() > 0:
            try:
                await curr_btn.first.click()
                await page.wait_for_timeout(1200)
                popup_item = page.locator(
                    f'[data-selenium="currency-popup-menu-item"][data-value="{self.currency}"]'
                ).first
                if await popup_item.count() > 0:
                    try:
                        async with page.expect_response(
                            lambda r: "/api/cronos/layout/currency/set" in r.url,
                            timeout=8000,
                        ):
                            await popup_item.click()
                    except Exception:  # noqa: BLE001
                        await popup_item.click()
                    await page.wait_for_timeout(2500)
                    return True
            except Exception:  # noqa: BLE001
                pass

        currency_id = CURRENCY_FALLBACK_IDS.get(self.currency)
        if currency_id is not None:
            ok = await page.evaluate(
                """async (cid) => {
                    try {
                        const r = await fetch(`/api/cronos/layout/currency/set?currencyId=${cid}`, { credentials: 'include' });
                        return r.ok;
                    } catch (_e) {
                        return false;
                    }
                }""",
                currency_id,
            )
            if ok:
                await page.wait_for_timeout(1000)
                return True
        return False

    async def fetch_price(
        self,
        context: BrowserContext,
        hotel_name: str,
        check_in: str,
        check_out: str,
    ) -> HotelPriceResult:
        room_candidates = [self.rooms]
        if self.adults >= 4 and self.rooms == 1:
            r2 = max(1, (self.adults + 2) // 3)
            r3 = max(1, (self.adults + 1) // 2)
            room_candidates = [r2] if r3 == r2 else [r2, r3]

        await self._seed_currency_cookies(context)
        page = await context.new_page()
        captured_payload: dict[str, Any] = {}

        async def _on_response(resp: Response) -> None:
            if "GetSecondaryData" not in resp.url or resp.status != 200:
                return
            try:
                payload = await resp.json()
                if isinstance(payload, dict):
                    grid = payload.get("roomGridData")
                    if isinstance(grid, dict) and grid.get("masterRooms"):
                        captured_payload["data"] = payload
            except Exception:  # noqa: BLE001 - 非 JSON 或頁面關閉時略過
                pass

        page.on("response", _on_response)
        try:
            last_error = f"Agoda 目前無 {hotel_name} 的可訂房型"
            for target_rooms in room_candidates:
                target_url = self.build_search_url(check_in=check_in, check_out=check_out, rooms=target_rooms)
                captured_payload.clear()
                await page.goto(target_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3500)

                if await self._ui_currency_matches(page) is False:
                    if await self._switch_currency_on_page(page):
                        captured_payload.clear()
                        await page.goto(target_url, wait_until="domcontentloaded")
                        await page.wait_for_timeout(3500)

                await page.evaluate("window.scrollBy(0, 1800)")
                await page.wait_for_timeout(5000)

                payload = captured_payload.get("data")
                if payload and _detect_payload_currency(payload) not in ("", self.currency):
                    if await self._switch_currency_on_page(page):
                        captured_payload.clear()
                        await page.goto(target_url, wait_until="domcontentloaded")
                        await page.wait_for_timeout(3500)
                        await page.evaluate("window.scrollBy(0, 1800)")
                        await page.wait_for_timeout(5000)
                        payload = captured_payload.get("data")

                body_text = await page.locator("body").inner_text()
                low_body = body_text.lower()
                if any(flag in low_body for flag in ("verify you are human", "captcha", "access denied")):
                    raise RuntimeError("觸發網站人機驗證 (CAPTCHA)，已停止該飯店抓取")

                if not payload:
                    last_error = f"未擷取到 Agoda 的 {hotel_name} 房型含稅資料 (GetSecondaryData)"
                    continue

                actual_currency = _detect_payload_currency(payload)
                if actual_currency and actual_currency != self.currency:
                    raise RuntimeError(f"Agoda 回傳幣別不符（預期 {self.currency}，實際 {actual_currency}）")

                price, room_type = extract_agoda_room_grid_price(
                    room_grid_data=payload["roomGridData"],
                    breakfast_only=self.breakfast_only,
                    expected_currency=self.currency,
                    room_keyword=self.room_keyword,
                )
                if price is not None:
                    return HotelPriceResult(
                        hotel_name=hotel_name,
                        platform=self.platform_name,
                        check_in=check_in,
                        check_out=check_out,
                        price=price,
                        currency=self.currency,
                        room_type=room_type,
                        url=target_url,
                    )

                meal_desc = "含早餐" if self.breakfast_only else "符合人數"
                last_error = f"Agoda 目前無 {hotel_name} 的{meal_desc}可訂房型"

            raise RuntimeError(last_error)
        finally:
            await page.close()

"""Agoda 含稅總價飯店爬蟲模組。"""

from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

from playwright.async_api import BrowserContext, Response

from src.models import HotelPriceResult
from src.scrapers.base import BaseHotelScraper


def _compute_los(check_in: str, check_out: str) -> int:
    try:
        dt_in = datetime.strptime(check_in, "%Y-%m-%d")
        dt_out = datetime.strptime(check_out, "%Y-%m-%d")
        days = (dt_out - dt_in).days
        return days if days > 0 else 1
    except ValueError:
        return 1


def extract_agoda_room_grid_price(
    room_grid_data: dict[str, Any],
    breakfast_only: bool = False,
) -> tuple[Optional[float], str]:
    """從 Agoda `GetSecondaryData` 的 `roomGridData` 擷取符合人數與可訂狀態的最低含稅總價。"""
    best_price: Optional[float] = None
    best_room_name = ""

    for master_room in room_grid_data.get("masterRooms", []) or []:
        master_name = str(master_room.get("name") or "").strip()
        for room in master_room.get("rooms", []) or []:
            if room.get("isFit") is False:
                continue
            avail = room.get("availability")
            if avail is not None and avail <= 0:
                continue
            if breakfast_only and not bool(room.get("isBreakfastIncluded")):
                continue

            inclusive_val = (room.get("inclusivePrice") or {}).get("display")
            total_val = (room.get("totalPrice") or {}).get("display")
            raw_price = inclusive_val if inclusive_val is not None else total_val
            if raw_price is None:
                continue

            try:
                numeric_price = float(raw_price)
            except (TypeError, ValueError):
                continue

            if numeric_price <= 0:
                continue

            if best_price is None or numeric_price < best_price:
                best_price = numeric_price
                best_room_name = str(room.get("name") or master_name).strip()

    if best_price is None:
        return None, ""
    return float(round(best_price)), best_room_name


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
        meal_label = "含早餐" if self.breakfast_only else "不限早餐"
        self.platform_name = f"Agoda [{meal_label} | IP:{self.ip_info or 'LOCAL'}]"

    def build_search_url(self, check_in: str, check_out: str) -> str:
        los = _compute_los(check_in, check_out)
        url = (
            f"https://www.agoda.com{self.hotel_path}"
            f"?checkIn={check_in}&los={los}&rooms={self.rooms}"
            f"&adults={self.adults}&children={self.children}"
        )
        if self.children > 0 and self.child_ages:
            url += f"&childages={quote(self.child_ages)}"
        url += f"&currencyCode={self.currency}"
        return url

    async def fetch_price(
        self,
        context: BrowserContext,
        hotel_name: str,
        check_in: str,
        check_out: str,
    ) -> HotelPriceResult:
        target_url = self.build_search_url(check_in=check_in, check_out=check_out)
        page = await context.new_page()
        captured_grid: dict[str, Any] = {}

        async def _on_response(resp: Response) -> None:
            if "GetSecondaryData" not in resp.url or resp.status != 200:
                return
            try:
                payload = await resp.json()
                if isinstance(payload, dict):
                    grid = payload.get("roomGridData")
                    if isinstance(grid, dict) and grid.get("masterRooms"):
                        captured_grid["data"] = grid
            except Exception:  # noqa: BLE001 - 非 JSON 或頁面關閉時略過
                pass

        page.on("response", _on_response)
        try:
            await page.goto(target_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            await page.evaluate("window.scrollBy(0, 1800)")
            await page.wait_for_timeout(5000)

            body_text = await page.locator("body").inner_text()
            low_body = body_text.lower()
            if any(flag in low_body for flag in ("verify you are human", "captcha", "access denied")):
                raise RuntimeError("觸發網站人機驗證 (CAPTCHA)，已停止該飯店抓取")

            grid_data = captured_grid.get("data")
            if not grid_data:
                raise RuntimeError(f"未擷取到 Agoda 的 {hotel_name} 房型含稅資料 (GetSecondaryData)")

            price, room_type = extract_agoda_room_grid_price(
                room_grid_data=grid_data,
                breakfast_only=self.breakfast_only,
            )
            if price is None:
                meal_desc = "含早餐" if self.breakfast_only else "符合人數"
                raise RuntimeError(f"Agoda 目前無 {hotel_name} 的{meal_desc}可訂房型")

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
        finally:
            await page.close()

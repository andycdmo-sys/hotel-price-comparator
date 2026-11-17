"""示範用飯店比價爬蟲（使用 Playwright 無痕分頁與本機測試 DOM，不依賴外部網路）。"""

import html
import re

from playwright.async_api import BrowserContext

from src.models import HotelPriceResult
from src.scrapers.base import BaseHotelScraper


def _parse_price(raw_text: str) -> float:
    digits = re.sub(r"[^\d.]", "", raw_text)
    if not digits:
        raise ValueError(f"無法從價格文字解析金額: {raw_text!r}")
    return float(digits)


class DemoHotelScraper(BaseHotelScraper):
    """示範平台爬蟲：實際建立 Playwright Page 並透過 CSS Selector 擷取房價。

    實作真實訂房網站爬蟲時，可繼承 `BaseHotelScraper`，將 `page.set_content(...)`
    替換為 `await page.goto(target_url, wait_until="domcontentloaded")` 並調整選擇器。
    """

    def __init__(
        self,
        platform_name: str,
        sample_price: float,
        room_type: str = "標準雙人房",
        currency: str = "TWD",
    ) -> None:
        self.platform_name = platform_name
        self.sample_price = sample_price
        self.room_type = room_type
        self.currency = currency

    async def fetch_price(
        self,
        context: BrowserContext,
        hotel_name: str,
        check_in: str,
        check_out: str,
    ) -> HotelPriceResult:
        page = await context.new_page()
        try:
            safe_hotel = html.escape(hotel_name)
            safe_room = html.escape(self.room_type)
            safe_platform = html.escape(self.platform_name)
            demo_html = f"""
            <!DOCTYPE html>
            <html lang="zh-Hant">
              <body>
                <main data-platform="{safe_platform}">
                  <h1 class="hotel-title">{safe_hotel}</h1>
                  <div class="room-name">{safe_room}</div>
                  <div class="price-tag">{self.currency} {self.sample_price:,.0f}</div>
                </main>
              </body>
            </html>
            """
            await page.set_content(demo_html, wait_until="domcontentloaded")

            room_text = (await page.locator(".room-name").inner_text()).strip()
            price_text = (await page.locator(".price-tag").inner_text()).strip()
            parsed_price = _parse_price(price_text)

            return HotelPriceResult(
                hotel_name=hotel_name,
                platform=self.platform_name,
                check_in=check_in,
                check_out=check_out,
                price=parsed_price,
                currency=self.currency,
                room_type=room_text,
                url=f"https://example.com/{self.platform_name.lower()}?hotel={hotel_name}",
            )
        finally:
            await page.close()

"""訂房平台爬蟲抽象基底類別與執行輔助函式。"""

from abc import ABC, abstractmethod
from typing import Sequence

from playwright.async_api import BrowserContext

from src.models import HotelPriceResult


class BaseHotelScraper(ABC):
    """所有訂房平台爬蟲的抽象基底類別。"""

    platform_name: str = "Unknown"

    @abstractmethod
    async def fetch_price(
        self,
        context: BrowserContext,
        hotel_name: str,
        check_in: str,
        check_out: str,
    ) -> HotelPriceResult:
        """使用無痕 BrowserContext 抓取指定飯店與日期的房價。"""


async def run_scrapers(
    scrapers: Sequence[BaseHotelScraper],
    context: BrowserContext,
    hotel_name: str,
    check_in: str,
    check_out: str,
    currency: str = "TWD",
) -> list[HotelPriceResult]:
    """依序執行所有平台爬蟲；單一平台失敗時記錄錯誤並繼續執行其餘平台。"""
    results: list[HotelPriceResult] = []
    for scraper in scrapers:
        try:
            result = await scraper.fetch_price(
                context=context,
                hotel_name=hotel_name,
                check_in=check_in,
                check_out=check_out,
            )
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - 隔離單一平台錯誤，避免中斷整體比價
            results.append(
                HotelPriceResult(
                    hotel_name=hotel_name,
                    platform=scraper.platform_name,
                    check_in=check_in,
                    check_out=check_out,
                    price=None,
                    currency=currency,
                    error=str(exc),
                )
            )
    return results

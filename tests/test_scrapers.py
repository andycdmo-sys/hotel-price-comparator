"""爬蟲錯誤隔離單元測試。"""

from typing import Any, Coroutine, TypeVar

from src.models import HotelPriceResult
from src.scrapers.agoda import AgodaScraper, extract_agoda_room_grid_price
from src.scrapers.base import BaseHotelScraper, run_scrapers
from src.scrapers.trip_com import TripComScraper

_T = TypeVar("_T")


def _run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """直接同步驅動無須事件迴圈 I/O 的協程，避免在沙盒中建立 socketpair。"""
    try:
        while True:
            coro.send(None)
    except StopIteration as stop:
        return stop.value
    finally:
        coro.close()


class _FailingScraper(BaseHotelScraper):
    platform_name = "BrokenSite"

    async def fetch_price(self, context, hotel_name, check_in, check_out):
        raise RuntimeError("連線逾時")


class _SuccessScraper(BaseHotelScraper):
    platform_name = "WorkingSite"

    async def fetch_price(self, context, hotel_name, check_in, check_out):
        return HotelPriceResult(
            hotel_name=hotel_name,
            platform=self.platform_name,
            check_in=check_in,
            check_out=check_out,
            price=3999,
        )


def test_run_scrapers_isolates_single_platform_failure() -> None:
    results = _run_sync(
        run_scrapers(
            scrapers=[_FailingScraper(), _SuccessScraper()],
            context=None,  # type: ignore[arg-type]
            hotel_name="測試飯店",
            check_in="2026-12-01",
            check_out="2026-12-02",
        )
    )

    assert len(results) == 2
    assert results[0].platform == "BrokenSite"
    assert results[0].price is None
    assert "連線逾時" in (results[0].error or "")
    assert results[1].platform == "WorkingSite"
    assert results[1].price == 3999
    assert results[1].error is None


def test_trip_com_scraper_breakfast_url_and_platform_label() -> None:
    scraper_any = TripComScraper(site_code="tw", breakfast_only=False, ip_info="TW")
    scraper_bf = TripComScraper(site_code="tw", breakfast_only=True, ip_info="TW")

    url_any = scraper_any.build_search_url("沖繩餘家弗飯店", "2027-01-26", "2027-01-27")
    url_bf = scraper_bf.build_search_url("沖繩餘家弗飯店", "2027-01-26", "2027-01-27")

    assert "listFilters=5~1*5*1" not in url_any
    assert "listFilters=5~1*5*1" in url_bf
    assert "不限早餐" in scraper_any.platform_name
    assert "含早餐" in scraper_bf.platform_name


def test_extract_agoda_room_grid_price_filters_fit_and_breakfast() -> None:
    room_grid_data = {
        "masterRooms": [
            {
                "name": "小型雙床間",
                "rooms": [
                    {
                        "name": "小型雙床間",
                        "isFit": False,
                        "availability": 1,
                        "isBreakfastIncluded": False,
                        "inclusivePrice": {"display": 2714.98},
                    }
                ],
            },
            {
                "name": "Deluxe Ocean",
                "rooms": [
                    {
                        "name": "Deluxe Ocean",
                        "isFit": True,
                        "availability": 0,
                        "isBreakfastIncluded": False,
                        "inclusivePrice": {"display": 3100.0},
                    },
                    {
                        "name": "Deluxe Ocean",
                        "isFit": True,
                        "availability": 1,
                        "isBreakfastIncluded": False,
                        "inclusivePrice": {"display": 3500.45},
                    },
                    {
                        "name": "Deluxe Ocean",
                        "isFit": True,
                        "availability": 1,
                        "isBreakfastIncluded": True,
                        "inclusivePrice": {"display": 4380.37},
                    },
                ],
            },
        ]
    }

    price_any, room_any = extract_agoda_room_grid_price(room_grid_data, breakfast_only=False)
    price_bf, room_bf = extract_agoda_room_grid_price(room_grid_data, breakfast_only=True)

    assert price_any == 3500.0
    assert room_any == "Deluxe Ocean"
    assert price_bf == 4380.0
    assert room_bf == "Deluxe Ocean"

    scraper = AgodaScraper(
        hotel_path="/zh-tw/hotel-yugaf-inn-okinawa/hotel/okinawa-main-island-jp.html",
        breakfast_only=True,
        ip_info="TW",
    )
    url = scraper.build_search_url("2027-01-26", "2027-01-27")
    assert "checkIn=2027-01-26&los=1" in url
    assert "adults=2&children=1&childages=11" in url
    assert "Agoda [含早餐 | IP:TW]" == scraper.platform_name

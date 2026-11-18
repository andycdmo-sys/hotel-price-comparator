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


def test_trip_com_custom_hotel_clears_okinawa_params_and_extracts_translated_title() -> None:
    from src.scrapers.trip_com import _extract_hotel_price_and_room

    scraper = TripComScraper(site_code="tw", hotel_id="1001027", adults=2, children=0, currency="TWD")
    search_url = scraper.build_search_url("礁溪老爺酒店", "2026-11-30", "2026-12-01")
    assert "optionId=1001027" in search_url
    assert "cityId=" not in search_url
    assert "countryId=" not in search_url

    jp_lines = [
        "5軒のホテルがあります",
        "ホテルロイヤル礁渓",
        "9.2 素晴らしい",
        "洋室ツインルーム",
        "TWD 9,850",
        "空室を確認",
    ]
    price, room = _extract_hotel_price_and_room(jp_lines, "礁溪老爺酒店")
    assert price == 9850.0
    assert "ツイン" in room or "洋室" in room


def test_booking_scraper_table_occupancy_tax_and_search_cards() -> None:
    from src.scrapers.booking import (
        BookingScraper,
        extract_booking_price_from_cards,
        extract_booking_room_table_price,
    )

    rows = [
        {
            "room": "雙床房",
            "occ": "最多成人人數： 2",
            "price": "TWD 3,533",
            "tax": "含稅費與其他費用",
            "cond": "含早餐－評價非常好",
        },
        {
            "room": "標準雙人房",
            "occ": "+ 最多成人人數： 1 最多兒童人數： 1 限 1 人入住",
            "price": "TWD 1,766",
            "tax": "含稅費與其他費用",
            "cond": "包括自助早餐",
        },
        {
            "room": "海景豪華房",
            "occ": "+ 最多成人人數： 2 最多兒童人數： 1",
            "price": "TWD 3,000",
            "tax": "+ TWD 500 稅費及其他費用",
            "cond": "評價非常好 的早餐 TWD 454",
        },
        {
            "room": "海景豪華房",
            "occ": "+ 最多成人人數： 2 最多兒童人數： 1",
            "price": "TWD 4,600",
            "tax": "含稅費與其他費用",
            "cond": "包含早餐和晚餐",
        },
    ]

    price_any, room_any = extract_booking_room_table_price(rows, adults=2, children=1, breakfast_only=False)
    price_bf, room_bf = extract_booking_room_table_price(rows, adults=2, children=1, breakfast_only=True)
    assert price_any == 3500.0
    assert room_any == "海景豪華房"
    assert price_bf == 4600.0
    assert room_bf == "海景豪華房"

    cards = [
        {
            "title": "開羅尼羅河酒店集團",
            "prices": ["TWD 1,200"],
            "tax": "含稅費與其他費用",
            "units": "雙人房",
            "href": "https://www.booking.com/hotel/eg/cairo.zh-tw.html",
        },
        {
            "title": "沖繩餘家弗酒店",
            "prices": ["TWD 3,400"],
            "tax": "+ TWD 410 稅費及其他費用",
            "units": "推薦給您的團體\nDeluxe Ocean\n包括早餐",
            "href": "https://www.booking.com/hotel/jp/yugafu-inn-okinawa.zh-tw.html?aid=123",
        },
    ]
    c_price, c_room, c_path = extract_booking_price_from_cards(cards, "沖繩餘家弗飯店", breakfast_only=True)
    assert c_price == 3810.0
    assert c_room == "Deluxe Ocean"
    assert c_path == "/hotel/jp/yugafu-inn-okinawa.zh-tw.html"

    scraper = BookingScraper(
        hotel_path="",
        adults=2,
        children=1,
        child_ages="11",
        currency="TWD",
        breakfast_only=True,
        ip_info="TW",
    )
    search_url = scraper.build_search_url("沖繩餘家弗飯店", "2027-01-26", "2027-01-27")
    assert "group_adults=2&no_rooms=1&group_children=1&age=11" in search_url
    assert "nflt=mealplan%3D1" in search_url
    assert scraper.platform_name == "Booking [含早餐 | IP:TW]"

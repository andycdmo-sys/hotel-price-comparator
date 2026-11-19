"""飯店比價 CLI 主程式。"""

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from src.browser import open_incognito_context
from src.config import load_config
from src.exporter import (
    export_to_csv,
    export_to_html,
    export_user_dashboard_json,
    prune_csv_by_profile,
)
from src.ip_checker import detect_current_ip_info
from src.models import HotelPriceResult
from src.profiles import UserProfile, load_all_profiles, load_profile, profile_to_target_prices
from src.scrapers import AgodaScraper, BookingScraper, DemoHotelScraper, TripComScraper, run_scrapers

PROJECT_ROOT = Path(__file__).resolve().parent

OKINAWA_WATCHLIST: list[tuple[str, str, str, str]] = [
    ("沖繩餘家弗飯店", "704038", "2027-01-26", "2027-01-27"),
    ("REF沖繩體育館by Vessel酒店集團", "106709440", "2027-01-27", "2027-01-28"),
    ("B/C 那霸飯店", "120097372", "2027-01-28", "2027-01-29"),
    ("艾斯汀納特飯店", "2845951", "2027-01-29", "2027-01-30"),
]

AGODA_HOTEL_PATHS: dict[str, str] = {
    "沖繩餘家弗飯店": "/zh-tw/hotel-yugaf-inn-okinawa/hotel/okinawa-main-island-jp.html",
    "REF沖繩體育館by Vessel酒店集團": "/zh-tw/ref-okinawa-arena-by-vessel-hotels/hotel/okinawa-main-island-jp.html",
    "B/C 那霸飯店": "/zh-tw/b-c-hotel/hotel/okinawa-main-island-jp.html",
    "艾斯汀納特飯店": "/zh-tw/estinate-hotel/hotel/okinawa-main-island-jp.html",
}

BOOKING_HOTEL_PATHS: dict[str, str] = {
    "沖繩餘家弗飯店": "/hotel/jp/yugafu-inn-okinawa.zh-tw.html",
    "REF沖繩體育館by Vessel酒店集團": "/hotel/jp/rehuchong-nawa-arinaby-betuseruhoteruzu.zh-tw.html",
    "B/C 那霸飯店": "/hotel/jp/bbyc-naha-hotel-with-rental-car.zh-tw.html",
    "艾斯汀納特飯店": "/hotel/jp/estination.zh-tw.html",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Python + Playwright 飯店比價工具")
    parser.add_argument(
        "--mode",
        choices=["trip", "demo"],
        default="trip",
        help="執行模式：trip（真實抓取 Trip.com）或 demo（本機測試頁）",
    )
    parser.add_argument(
        "--watchlist",
        action="store_true",
        help="一次批次抓取預設的 4 間沖繩飯店追蹤清單 (2027-01-26 ~ 2027-01-30)",
    )
    parser.add_argument(
        "--profile",
        default="",
        help="指定單一使用者代號（讀取 profiles/<user_id>.json）",
    )
    parser.add_argument(
        "--all-profiles",
        action="store_true",
        help="掃描並執行 profiles/*.json 中的所有使用者追蹤清單",
    )
    parser.add_argument("--hotel", default="沖繩餘家弗飯店", help="欲查詢的飯店名稱")
    parser.add_argument("--hotel-id", default="704038", help="Trip.com 飯店 optionId")
    parser.add_argument(
        "--agoda-path",
        default="",
        help="單一飯店查詢時的 Agoda 頁面路徑（例如 /zh-tw/hotel-yugaf-inn-okinawa/hotel/okinawa-main-island-jp.html）",
    )
    parser.add_argument(
        "--booking-path",
        default="",
        help="單一飯店查詢時的 Booking.com 頁面路徑（例如 /hotel/jp/yugafu-inn-okinawa.zh-tw.html）",
    )
    parser.add_argument(
        "--platforms",
        default="trip,agoda,booking",
        help="要抓取的訂房平台，以逗號分隔（支援 trip,agoda,booking）",
    )
    parser.add_argument("--check-in", default="2027-01-26", help="入住日期 (YYYY-MM-DD)")
    parser.add_argument("--check-out", default="2027-01-27", help="退房日期 (YYYY-MM-DD)")
    parser.add_argument("--adults", type=int, default=2, help="成人人數")
    parser.add_argument("--children", type=int, default=1, help="兒童人數")
    parser.add_argument("--child-ages", default="11", help="兒童年齡（多位以逗號分隔）")
    parser.add_argument(
        "--sites",
        default="tw",
        help="要比較的 Trip.com 分站代碼，以逗號分隔（支援 tw,jp,hk,sg,us）",
    )
    parser.add_argument(
        "--meal-plan",
        choices=["both", "any", "breakfast"],
        default="both",
        help="早餐方案：both（預設同時抓不限早餐與含早餐）、breakfast（只抓含早餐）、any（只抓最低價不限早餐）",
    )
    meal_group = parser.add_mutually_exclusive_group()
    meal_group.add_argument(
        "--breakfast",
        action="store_true",
        help="捷徑：只抓「含早餐」方案（等同 --meal-plan breakfast）",
    )
    meal_group.add_argument(
        "--no-breakfast",
        action="store_true",
        help="捷徑：只抓「不限早餐/最低價」方案（等同 --meal-plan any）",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="將本次比價結果附加並依價格合併排序至既有 CSV",
    )
    parser.add_argument(
        "--output",
        default="",
        help="輸出 CSV 路徑（預設存放於 output/hotel_prices_<timestamp>.csv）",
    )
    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=None,
        help="強制以無頭模式執行瀏覽器",
    )
    headless_group.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="顯示瀏覽器視窗以利除錯",
    )
    return parser


def _resolve_breakfast_options(args: argparse.Namespace) -> list[bool]:
    meal_plan = args.meal_plan
    if args.breakfast:
        meal_plan = "breakfast"
    elif args.no_breakfast:
        meal_plan = "any"

    if meal_plan == "both":
        return [False, True]
    if meal_plan == "breakfast":
        return [True]
    return [False]


def _build_hotel_scrapers(
    *,
    hotel_name: str,
    trip_hotel_id: str,
    agoda_path: str,
    booking_path: str,
    platforms: set[str] | list[str],
    sites: list[str],
    adults: int,
    children: int,
    child_ages: str,
    rooms: int = 1,
    room_guests: list[int] | None = None,
    room_keyword: str = "",
    currency: str,
    ip_info: str,
    breakfast_options: list[bool],
):
    scrapers = []
    if "trip" in platforms:
        scrapers.extend(
            TripComScraper(
                site_code=code,
                hotel_id=trip_hotel_id,
                adults=adults,
                children=children,
                child_ages=child_ages,
                rooms=rooms,
                currency=currency,
                breakfast_only=bf_only,
                ip_info=ip_info,
                room_keyword=room_keyword,
            )
            for code in sites
            for bf_only in breakfast_options
        )

    resolved_agoda = agoda_path or AGODA_HOTEL_PATHS.get(hotel_name, "")
    if "agoda" in platforms and resolved_agoda:
        scrapers.extend(
            AgodaScraper(
                hotel_path=resolved_agoda,
                adults=adults,
                children=children,
                child_ages=child_ages,
                rooms=rooms,
                currency=currency,
                breakfast_only=bf_only,
                ip_info=ip_info,
                room_keyword=room_keyword,
            )
            for bf_only in breakfast_options
        )

    resolved_booking = booking_path or BOOKING_HOTEL_PATHS.get(hotel_name, "")
    if "booking" in platforms:
        scrapers.extend(
            BookingScraper(
                hotel_path=resolved_booking,
                adults=adults,
                children=children,
                child_ages=child_ages,
                rooms=rooms,
                room_guests=room_guests,
                currency=currency,
                breakfast_only=bf_only,
                ip_info=ip_info,
                room_keyword=room_keyword,
            )
            for bf_only in breakfast_options
        )
    return scrapers


async def _run_profile_batch(
    profile: UserProfile,
    context,
    ip_info: str,
    breakfast_options: list[bool],
) -> Path:
    csv_path = PROJECT_ROOT / "output" / f"history_{profile.user_id}.csv"
    json_path = PROJECT_ROOT / "docs" / "data" / f"{profile.user_id}.json"
    profile_path = PROJECT_ROOT / "profiles" / f"{profile.user_id}.json"

    if not profile.hotels:
        profile_path.unlink(missing_ok=True)
        csv_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)
        print(f"[{profile.user_id}] 飯店清單為空，已清除設定檔與歷史比價資料。")
        return csv_path

    profile_results: list[HotelPriceResult] = []
    for hotel in profile.hotels:
        hotel_rooms = hotel.rooms if hotel.rooms > 1 else profile.rooms
        hotel_room_guests = hotel.room_guests if hotel.room_guests else profile.room_guests
        hotel_adults = sum(hotel_room_guests) if hotel_room_guests else profile.adults
        hotel_room_keyword = hotel.room_type.strip() if hotel.match_room_type and hotel.room_type.strip() else ""
        scrapers = _build_hotel_scrapers(
            hotel_name=hotel.name,
            trip_hotel_id=hotel.trip_hotel_id,
            agoda_path=hotel.agoda_path,
            booking_path=hotel.booking_path,
            platforms=profile.platforms,
            sites=profile.sites,
            adults=hotel_adults,
            children=profile.children,
            child_ages=profile.child_ages,
            rooms=hotel_rooms,
            room_guests=hotel_room_guests,
            room_keyword=hotel_room_keyword,
            currency=profile.currency,
            ip_info=ip_info,
            breakfast_options=breakfast_options,
        )

        if not scrapers:
            continue

        batch = await run_scrapers(
            scrapers=scrapers,
            context=context,
            hotel_name=hotel.name,
            check_in=hotel.check_in,
            check_out=hotel.check_out,
            currency=profile.currency,
        )
        profile_results.extend(batch)
        print(f"[{profile.user_id}] 比價完成：{hotel.name} ({hotel.check_in} ~ {hotel.check_out})")
        for item in sorted(batch, key=lambda r: (r.price is None, r.price or 0)):
            if item.error:
                print(f"  - [{item.platform}] 查詢失敗：{item.error}")
            else:
                room_info = f" ({item.room_type})" if item.room_type else ""
                print(f"  - [{item.platform}] {item.currency} {item.price:,.0f}{room_info}")

    prune_csv_by_profile(csv_path, profile)
    export_to_csv(profile_results, csv_path, append=True)

    export_user_dashboard_json(csv_path, json_path, profile=profile)
    print(f"[{profile.user_id}] 已更新 {csv_path} 與 {json_path}")

    if profile.user_id == "Andy1025":
        legacy_csv = PROJECT_ROOT / "output" / "okinawa_2027_history.csv"
        prune_csv_by_profile(legacy_csv, profile)
        export_to_csv(profile_results, legacy_csv, append=True)
        export_to_html(
            legacy_csv,
            PROJECT_ROOT / "docs" / "index.html",
            target_prices=profile_to_target_prices(profile),
        )

    return csv_path


async def run_cli(args: argparse.Namespace) -> Path:
    env_file = PROJECT_ROOT / ".env"
    config = load_config(env_path=env_file if env_file.exists() else None)
    if args.headless is not None:
        config = replace(config, headless=args.headless)

    if args.all_profiles or args.profile:
        profiles_dir = PROJECT_ROOT / "profiles"
        if args.all_profiles:
            profiles = load_all_profiles(profiles_dir)
        else:
            profiles = [load_profile(profiles_dir / f"{args.profile.strip()}.json")]

        if not profiles:
            raise RuntimeError("profiles/ 目錄中找不到任何使用者設定檔")

        ip_info = detect_current_ip_info()
        print(f"目前對外 IP / 國家：{ip_info}")
        breakfast_options = _resolve_breakfast_options(args)
        last_csv = PROJECT_ROOT / "output" / f"history_{profiles[0].user_id}.csv"
        async with open_incognito_context(config) as context:
            for prof in profiles:
                last_csv = await _run_profile_batch(
                    profile=prof,
                    context=context,
                    ip_info=ip_info,
                    breakfast_options=breakfast_options,
                )
        return last_csv

    if args.watchlist:
        targets = OKINAWA_WATCHLIST
    else:
        targets = [(args.hotel, args.hotel_id, args.check_in, args.check_out)]

    all_results: list[HotelPriceResult] = []

    if args.mode == "demo":
        scrapers = [
            DemoHotelScraper("DemoBooking", sample_price=4850, room_type="豪華雙人房", currency=config.default_currency),
            DemoHotelScraper("DemoAgoda", sample_price=4520, room_type="標準雙人房", currency=config.default_currency),
            DemoHotelScraper("DemoHotels", sample_price=5100, room_type="尊榮雙人房", currency=config.default_currency),
        ]
        async with open_incognito_context(config) as context:
            for hotel_name, _, check_in, check_out in targets:
                batch = await run_scrapers(
                    scrapers=scrapers,
                    context=context,
                    hotel_name=hotel_name,
                    check_in=check_in,
                    check_out=check_out,
                    currency=config.default_currency,
                )
                all_results.extend(batch)
    else:
        ip_info = detect_current_ip_info()
        print(f"目前對外 IP / 國家：{ip_info}")
        site_codes = [s.strip() for s in args.sites.split(",") if s.strip()]
        enabled_platforms = {p.strip().lower() for p in args.platforms.split(",") if p.strip()}
        breakfast_options = _resolve_breakfast_options(args)

        async with open_incognito_context(config) as context:
            for hotel_name, hotel_id, check_in, check_out in targets:
                agoda_path = args.agoda_path if not args.watchlist and args.agoda_path else ""
                booking_path = args.booking_path if not args.watchlist and args.booking_path else ""
                scrapers = _build_hotel_scrapers(
                    hotel_name=hotel_name,
                    trip_hotel_id=hotel_id,
                    agoda_path=agoda_path,
                    booking_path=booking_path,
                    platforms=enabled_platforms,
                    sites=site_codes,
                    adults=args.adults,
                    children=args.children,
                    child_ages=args.child_ages,
                    currency=config.default_currency,
                    ip_info=ip_info,
                    breakfast_options=breakfast_options,
                )
                batch = await run_scrapers(
                    scrapers=scrapers,
                    context=context,
                    hotel_name=hotel_name,
                    check_in=check_in,
                    check_out=check_out,
                    currency=config.default_currency,
                )
                all_results.extend(batch)

                print(
                    f"比價完成：{hotel_name} ({check_in} ~ {check_out} | "
                    f"{args.adults}大{args.children}小)"
                )
                for item in sorted(batch, key=lambda r: (r.price is None, r.price or 0)):
                    if item.error:
                        print(f"  - [{item.platform}] 查詢失敗：{item.error}")
                    else:
                        room_info = f" ({item.room_type})" if item.room_type else ""
                        print(f"  - [{item.platform}] {item.currency} {item.price:,.0f}{room_info}")

    if args.output:
        out_candidate = Path(args.output)
        output_path = out_candidate if out_candidate.is_absolute() else (PROJECT_ROOT / out_candidate)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = PROJECT_ROOT / "output" / f"hotel_prices_{timestamp}.csv"

    exported_path = export_to_csv(all_results, output_path, append=args.append)
    print(f"CSV 已匯出至：{exported_path}")

    if args.watchlist:
        html_path = export_to_html(exported_path, PROJECT_ROOT / "docs" / "index.html")
        print(f"手機版網頁看板已更新至：{html_path}")

    return exported_path


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(run_cli(args))


if __name__ == "__main__":
    main()

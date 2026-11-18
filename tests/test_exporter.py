"""CSV 匯出與排序單元測試。"""

import csv
from pathlib import Path

from src.exporter import export_to_csv, export_to_html
from src.models import HotelPriceResult


def test_export_to_csv_sorts_prices_and_uses_utf8_bom(tmp_path: Path) -> None:
    results = [
        HotelPriceResult(
            hotel_name="台北君悅酒店",
            platform="PlatformB",
            check_in="2026-12-01",
            check_out="2026-12-02",
            price=5200,
            room_type="豪華房",
        ),
        HotelPriceResult(
            hotel_name="台北君悅酒店",
            platform="PlatformFail",
            check_in="2026-12-01",
            check_out="2026-12-02",
            price=None,
            error="Timeout",
        ),
        HotelPriceResult(
            hotel_name="台北君悅酒店",
            platform="PlatformA",
            check_in="2026-12-01",
            check_out="2026-12-02",
            price=4300,
            room_type="標準房",
        ),
    ]

    output_file = tmp_path / "nested" / "prices.csv"
    exported = export_to_csv(results, output_file)
    assert exported.exists()

    raw_bytes = exported.read_bytes()
    assert raw_bytes.startswith(b"\xef\xbb\xbf")

    with exported.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert [row["platform"] for row in rows] == [
        "PlatformA",
        "PlatformB",
        "PlatformFail",
    ]
    assert rows[0]["price"] == "4300"
    assert rows[0]["hotel_name"] == "台北君悅酒店"
    assert rows[2]["error"] == "Timeout"


def test_export_to_csv_append_merges_and_resorts(tmp_path: Path) -> None:
    output_file = tmp_path / "compare.csv"

    first_batch = [
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Trip.com [TW站 | IP:TW]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=3654,
            room_type="豪華雙床房",
        )
    ]
    export_to_csv(first_batch, output_file, append=False)

    second_batch = [
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Trip.com [JP站 | IP:JP]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=3420,
            room_type="豪華雙床房",
        )
    ]
    export_to_csv(second_batch, output_file, append=True)

    with output_file.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert len(rows) == 2
    assert [row["platform"] for row in rows] == [
        "Trip.com [JP站 | IP:JP]",
        "Trip.com [TW站 | IP:TW]",
    ]
    assert [row["price"] for row in rows] == ["3420", "3654"]


def test_export_to_html_shows_no_plan_and_line_chart(tmp_path: Path) -> None:
    csv_file = tmp_path / "history.csv"
    html_file = tmp_path / "index.html"

    results = [
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Trip.com [TW站 | IP:US]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=3654,
            room_type="豪華雙床房",
            url="https://tw.trip.com/hotels/list?optionId=704038&locale=zh-TW",
            scraped_at="2026-11-17T20:05:00+08:00",
        ),
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Trip.com [JP站 | IP:US]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=3500,
            room_type="デラックスツイン",
            url="https://jp.trip.com/hotels/list?optionId=704038&locale=ja-JP",
            scraped_at="2026-11-17T20:05:10+08:00",
        ),
    ]
    export_to_csv(results, csv_file)
    export_to_html(csv_file, html_file)

    content = html_file.read_text(encoding="utf-8")
    assert "無此方案" in content
    assert "尚無資料" not in content
    assert '<canvas id="priceChart"></canvas>' in content
    assert "CHART_DATA" in content
    assert "3654" in content
    assert "https://tw.trip.com/hotels/list?optionId=704038&amp;locale=zh-TW" in content
    assert "jp.trip.com" not in content


def test_export_to_html_renders_target_prices(tmp_path: Path) -> None:
    csv_file = tmp_path / "history.csv"
    html_file = tmp_path / "index.html"

    results = [
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Trip.com [TW站 | 含早餐 | IP:TW]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=4830,
            room_type="豪華雙床房",
            scraped_at="2026-11-18T08:40:00+08:00",
        )
    ]
    export_to_csv(results, csv_file)
    export_to_html(csv_file, html_file)

    content = html_file.read_text(encoding="utf-8")
    assert 'data-target-key="沖繩餘家弗飯店||含早餐方案"' in content
    assert 'value="4193"' in content
    assert "target-input" in content


def test_build_chart_payload_merges_different_ips_and_clusters_batches() -> None:
    from src.exporter import _build_chart_payload

    records = [
        HotelPriceResult(
            hotel_name="B/C 那霸飯店",
            platform="Trip.com [TW站 | 不限早餐 | IP:TW (114.137.92.57)]",
            check_in="2027-01-28",
            check_out="2027-01-29",
            price=2299,
            scraped_at="2026-11-17T16:57:00+08:00",
        ),
        HotelPriceResult(
            hotel_name="B/C 那霸飯店",
            platform="Trip.com [TW站 | 不限早餐 | IP:US (3.41.210.133)]",
            check_in="2027-01-28",
            check_out="2027-01-29",
            price=2250,
            scraped_at="2026-11-17T20:49:00+08:00",
        ),
        HotelPriceResult(
            hotel_name="艾斯汀納特飯店",
            platform="Trip.com [TW站 | 不限早餐 | IP:US (3.41.210.133)]",
            check_in="2027-01-29",
            check_out="2027-01-30",
            price=2126,
            scraped_at="2026-11-17T20:50:00+08:00",
        ),
    ]

    payload = _build_chart_payload(records)
    assert payload["labels"] == ["11-17 16:57", "11-17 20:49"]
    bc_series = payload["byHotel"]["B/C 那霸飯店"]
    assert len(bc_series) == 1
    assert bc_series[0]["label"] == "Trip.com [TW站 | 不限早餐]"
    assert bc_series[0]["data"] == [2299, 2250]


def test_export_to_html_separates_breakfast_charts_and_target_line(tmp_path: Path) -> None:
    from src.exporter import _build_chart_payload

    csv_file = tmp_path / "history.csv"
    html_file = tmp_path / "index.html"

    records = [
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Trip.com [TW站 | 不限早餐 | IP:TW]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=3654,
            scraped_at="2026-11-18T08:40:00+08:00",
        ),
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Trip.com [TW站 | 含早餐 | IP:TW]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=4830,
            scraped_at="2026-11-18T08:40:00+08:00",
        ),
    ]

    payload = _build_chart_payload(records)
    assert len(payload["any"]["byHotel"]["沖繩餘家弗飯店"]) == 1
    assert payload["any"]["byHotel"]["沖繩餘家弗飯店"][0]["label"] == "Trip.com [TW站 | 不限早餐]"
    assert len(payload["bf"]["byHotel"]["沖繩餘家弗飯店"]) == 1
    assert payload["bf"]["byHotel"]["沖繩餘家弗飯店"][0]["label"] == "Trip.com [TW站 | 含早餐]"

    export_to_csv(records, csv_file)
    export_to_html(csv_file, html_file)

    content = html_file.read_text(encoding="utf-8")
    assert 'id="priceChart"' in content
    assert 'id="priceChartBf"' in content
    assert "🛏️ 不限早餐（最低價）" in content
    assert "🍴 含早餐方案" in content
    assert "borderDash: [6, 6]" in content
    assert "🎯 目標價" in content


def test_export_to_html_renders_winner_and_collapses_extra_otas(tmp_path: Path) -> None:
    csv_file = tmp_path / "history.csv"
    html_file = tmp_path / "index.html"

    records = [
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Trip.com [TW站 | 不限早餐 | IP:TW]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=3654,
            url="https://tw.trip.com/hotels/list?optionId=704038",
            scraped_at="2026-11-18T13:00:00+08:00",
        ),
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Trip.com [JP站 | 不限早餐 | IP:TW]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=3773,
            url="https://jp.trip.com/hotels/list?optionId=704038&locale=ja-JP",
            scraped_at="2026-11-18T13:00:10+08:00",
        ),
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Agoda [不限早餐 | IP:TW]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=3500,
            url="https://www.agoda.com/zh-tw/hotel-yugaf-inn-okinawa/hotel/okinawa-main-island-jp.html",
            scraped_at="2026-11-18T13:00:20+08:00",
        ),
        HotelPriceResult(
            hotel_name="沖繩餘家弗飯店",
            platform="Booking [不限早餐 | IP:TW]",
            check_in="2027-01-26",
            check_out="2027-01-27",
            price=3900,
            url="https://www.booking.com/hotel/jp/yugaf.html",
            scraped_at="2026-11-18T13:00:30+08:00",
        ),
    ]

    export_to_csv(records, csv_file)
    export_to_html(csv_file, html_file)

    content = html_file.read_text(encoding="utf-8")
    assert "🏆 Agoda 最低" in content
    assert 'data-current-price="3500"' in content
    assert "展開其他 1 家報價" in content
    assert "jp.trip.com" not in content


def test_profile_loading_and_user_dashboard_json_export(tmp_path: Path) -> None:
    import json
    from src.exporter import export_user_dashboard_json
    from src.profiles import load_profile

    profile_file = tmp_path / "Andy1025.json"
    profile_file.write_text(
        json.dumps(
            {
                "user_id": "Andy1025",
                "title": "Andy1025 的沖繩飯店比價看板",
                "adults": 2,
                "children": 1,
                "child_ages": "11",
                "currency": "TWD",
                "hotels": [
                    {
                        "name": "沖繩餘家弗飯店",
                        "check_in": "2027-01-26",
                        "check_out": "2027-01-27",
                        "trip_hotel_id": "https://tw.trip.com/hotels/list?optionId=704038",
                        "agoda_path": "https://www.agoda.com/zh-tw/hotel-yugaf-inn-okinawa/hotel/okinawa-main-island-jp.html?checkIn=2027-01-26",
                        "target_bf": 4193,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    profile = load_profile(profile_file)
    assert profile.user_id == "Andy1025"
    assert profile.hotels[0].trip_hotel_id == "704038"
    assert profile.hotels[0].agoda_path == "/zh-tw/hotel-yugaf-inn-okinawa/hotel/okinawa-main-island-jp.html"

    csv_file = tmp_path / "history_Andy1025.csv"
    json_out = tmp_path / "data" / "Andy1025.json"
    html_out = tmp_path / "index.html"

    export_to_csv(
        [
            HotelPriceResult(
                hotel_name="沖繩餘家弗飯店",
                platform="Agoda [含早餐 | IP:TW]",
                check_in="2027-01-26",
                check_out="2027-01-27",
                price=4380,
                scraped_at="2026-11-18T13:30:00+08:00",
            )
        ],
        csv_file,
    )
    export_user_dashboard_json(csv_file, json_out, profile=profile)
    export_to_html(csv_file, html_out)

    dashboard_data = json.loads(json_out.read_text(encoding="utf-8"))
    assert dashboard_data["user_id"] == "Andy1025"
    assert 'value="4193"' in dashboard_data["cards_html"]
    assert "chart_payload" in dashboard_data

    html_content = html_out.read_text(encoding="utf-8")
    assert 'id="passcode-gate"' in html_content
    assert 'id="manage-panel"' in html_content
    assert '<div id="dashboard-content" class="hidden">' in html_content
    assert '"請手動複製以下設定：\\n"' in html_content


def test_manage_panel_dropdowns_single_url_and_no_overlap(tmp_path: Path) -> None:
    import json
    from src.profiles import load_profile

    profile_file = tmp_path / "FriendTest.json"
    profile_file.write_text(
        json.dumps(
            {
                "user_id": "FriendTest",
                "adults": 2,
                "children": 1,
                "child_ages": "11",
                "hotels": [
                    {
                        "name": "沖繩餘家弗飯店",
                        "check_in": "2027-01-26",
                        "check_out": "2027-01-27",
                        "urls": [
                            "https://tw.trip.com/hotels/list?optionId=704038",
                            "https://www.agoda.com/zh-tw/hotel-yugaf-inn-okinawa/hotel/okinawa-main-island-jp.html",
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    profile = load_profile(profile_file)
    assert profile.hotels[0].trip_hotel_id == "704038"
    assert profile.hotels[0].agoda_path == "/zh-tw/hotel-yugaf-inn-okinawa/hotel/okinawa-main-island-jp.html"

    csv_file = tmp_path / "history.csv"
    html_out = tmp_path / "index.html"
    export_to_csv([], csv_file)
    export_to_html(csv_file, html_out)

    content = html_out.read_text(encoding="utf-8")
    assert '<select id="cfg-adults"' in content
    assert '<select id="cfg-children"' in content
    assert 'id="cfg-ages-wrap"' in content
    assert "class=\"field-input h-url\"" in content
    assert "url-item-row" in content
    assert "url-item-top" in content
    assert "btn-add-url" in content
    assert 'id="btn-close-manage"' in content
    assert 'id="btn-collapse-manage"' in content
    assert "➕ 新增房價網" in content
    assert "width: 100%;" in content
    assert "hotel-row-line3" in content

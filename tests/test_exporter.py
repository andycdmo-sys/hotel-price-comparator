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

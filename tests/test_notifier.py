import json
from pathlib import Path

from src.exporter import export_to_csv, export_user_dashboard_json
from src.models import HotelPriceResult
from src.notifier import collect_price_alerts, load_previous_min_prices, notify_profile_if_needed
from src.profiles import load_profile, parse_profile_dict, save_profile_from_json_str


def test_collect_price_alerts_target_and_lowest(tmp_path: Path) -> None:
    profile = parse_profile_dict(
        {
            "user_id": "LineUser1",
            "notify_channel": "line",
            "notify_target": "U1234567890abcdef",
            "notify_on_target": True,
            "notify_on_lowest": True,
            "hotels": [
                {
                    "name": "沖繩餘家弗飯店",
                    "check_in": "2027-01-26",
                    "check_out": "2027-01-27",
                    "target_any": 3500,
                    "target_bf": 4000,
                }
            ],
        }
    )

    csv_path = tmp_path / "history_LineUser1.csv"
    old_results = [
        HotelPriceResult("沖繩餘家弗飯店", "Agoda [不限早餐 | IP:1.1.1.1]", "2027-01-26", "2027-01-27", 3600.0, "TWD"),
        HotelPriceResult("沖繩餘家弗飯店", "Agoda [含早餐 | IP:1.1.1.1]", "2027-01-26", "2027-01-27", 4500.0, "TWD"),
    ]
    export_to_csv(old_results, csv_path)
    prev_mins = load_previous_min_prices(csv_path)

    new_results = [
        HotelPriceResult("沖繩餘家弗飯店", "Agoda [不限早餐 | IP:1.1.1.1]", "2027-01-26", "2027-01-27", 3300.0, "TWD", "雙床房"),
        HotelPriceResult("沖繩餘家弗飯店", "Agoda [含早餐 | IP:1.1.1.1]", "2027-01-26", "2027-01-27", 4200.0, "TWD", "雙床房"),
    ]
    alerts = collect_price_alerts(profile, new_results, previous_mins=prev_mins)
    assert len(alerts) == 2
    assert "低於目標價" in alerts[0] and "創歷史新低" in alerts[0]
    assert "創歷史新低" in alerts[1] and "低於目標價" not in alerts[1]


def test_notify_sends_only_to_owner_and_masks_public_json(tmp_path: Path, monkeypatch) -> None:
    raw = json.dumps(
        {
            "user_id": "LineOwner",
            "notify_channel": "line",
            "notify_target": "U9988776655443322",
            "notify_on_target": True,
            "notify_on_lowest": False,
            "hotels": [
                {
                    "name": "B/C 那霸飯店",
                    "check_in": "2027-01-28",
                    "check_out": "2027-01-29",
                    "target_any": 2500,
                }
            ],
        },
        ensure_ascii=False,
    )
    saved = save_profile_from_json_str(raw, tmp_path, expected_user_id="LineOwner")
    loaded = load_profile(saved)
    assert loaded.notify_target == "U9988776655443322"

    # 匯出公開 JSON 時必須遮罩 LINE User ID
    csv_file = tmp_path / "history_LineOwner.csv"
    json_out = tmp_path / "data" / "LineOwner.json"
    export_to_csv([], csv_file)
    export_user_dashboard_json(csv_file, json_out, profile=loaded)
    dash = json.loads(json_out.read_text(encoding="utf-8"))
    assert dash["profile"]["notify_target"] == "U998...3322"

    # 若網頁送回遮罩值，save_profile_from_json_str 應自動保留原完整 ID
    masked_raw = json.dumps(dash["profile"], ensure_ascii=False)
    save_profile_from_json_str(masked_raw, tmp_path, expected_user_id="LineOwner")
    reloaded = load_profile(saved)
    assert reloaded.notify_target == "U9988776655443322"

    sent_calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "src.notifier.send_line_push_message",
        lambda token, to_id, msg: sent_calls.append((token, to_id, msg)),
    )
    ok = notify_profile_if_needed(
        reloaded,
        [HotelPriceResult("B/C 那霸飯店", "Booking [不限早餐 | IP:1.1.1.1]", "2027-01-28", "2027-01-29", 2200.0, "TWD")],
        line_token="secret_line_token",
    )
    assert ok is True
    assert len(sent_calls) == 1
    assert sent_calls[0][1] == "U9988776655443322"
    assert "B/C 那霸飯店" in sent_calls[0][2]

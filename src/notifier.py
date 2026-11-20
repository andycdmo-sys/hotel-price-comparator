from __future__ import annotations

import csv
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from src.exporter import _short_platform_label
from src.models import HotelPriceResult
from src.profiles import UserProfile

LINE_PUSH_API_URL = "https://api.line.me/v2/bot/message/push"
DEFAULT_DASHBOARD_BASE_URL = "https://andycdmo-sys.github.io/hotel-price-comparator/"


def _is_breakfast_platform(platform: str) -> bool:
    return "含早餐" in (platform or "") and "不限早餐" not in (platform or "")


def load_previous_min_prices(csv_path: Path) -> dict[tuple[str, str, str, bool], float]:
    """在寫入新抓價結果前，讀取各飯店與方案過往的歷史最低價。"""
    mins: dict[tuple[str, str, str, bool], float] = {}
    if not csv_path.exists():
        return mins

    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_price = (row.get("price") or "").strip()
            if not raw_price:
                continue
            try:
                price_val = float(raw_price)
            except ValueError:
                continue
            if price_val <= 0:
                continue
            key = (
                (row.get("hotel_name") or "").strip(),
                (row.get("check_in") or "").strip(),
                (row.get("check_out") or "").strip(),
                _is_breakfast_platform(row.get("platform") or ""),
            )
            prev = mins.get(key)
            if prev is None or price_val < prev:
                mins[key] = price_val
    return mins


def collect_price_alerts(
    profile: UserProfile,
    results: list[HotelPriceResult],
    previous_mins: Optional[dict[tuple[str, str, str, bool], float]] = None,
) -> list[str]:
    """依使用者設定的目標價與歷史新低條件，篩選出需要推播的飯店方案摘要。"""
    if not profile.notify_on_target and not profile.notify_on_lowest:
        return []

    prev_map = previous_mins or {}
    alerts: list[str] = []

    for hotel in profile.hotels:
        hotel_records = [
            r
            for r in results
            if r.hotel_name == hotel.name
            and r.check_in == hotel.check_in
            and r.check_out == hotel.check_out
            and r.price is not None
            and r.price > 0
        ]
        if not hotel_records:
            continue

        plans = [
            (False, "不限早餐", hotel.target_any),
            (True, "含早餐", hotel.target_bf),
        ]
        for is_bf, plan_label, target_val in plans:
            plan_records = [r for r in hotel_records if _is_breakfast_platform(r.platform) == is_bf]
            if not plan_records:
                continue
            best = min(plan_records, key=lambda r: r.price or float("inf"))
            assert best.price is not None

            reasons: list[str] = []
            if profile.notify_on_target and target_val is not None and best.price <= target_val:
                reasons.append(f"🎯 低於目標價 ({best.currency} {target_val:,.0f})")
            prev_min = prev_map.get((hotel.name, hotel.check_in, hotel.check_out, is_bf))
            if profile.notify_on_lowest and prev_min is not None and best.price < prev_min:
                reasons.append(f"📉 創歷史新低 (原 {best.currency} {prev_min:,.0f})")

            if not reasons:
                continue

            plat_short = _short_platform_label(best.platform)
            room_desc = f"｜{best.room_type}" if best.room_type else ""
            block = (
                f"🏨 {hotel.name} ({hotel.check_in} ~ {hotel.check_out})\n"
                f"方案：{plan_label}{room_desc}\n"
                f"最新最低：{best.currency} {best.price:,.0f}（{plat_short}）\n"
                f"觸發原因：{'、'.join(reasons)}"
            )
            alerts.append(block)

    return alerts


def format_line_alert_message(
    user_id: str,
    alert_blocks: list[str],
    dashboard_base_url: str = DEFAULT_DASHBOARD_BASE_URL,
) -> str:
    """組合要推播給單一使用者的 LINE 文字訊息。"""
    header = f"🔔 [{user_id}] 飯店降價／達標通知（共 {len(alert_blocks)} 筆）"
    body = "\n\n".join(alert_blocks[:8])
    if len(alert_blocks) > 8:
        body += f"\n\n…另有 {len(alert_blocks) - 8} 筆符合條件，請至看板查看。"
    link = f"{dashboard_base_url.rstrip('/')}/?user={user_id}"
    return f"{header}\n\n{body}\n\n📊 查看完整看板：{link}"


def send_line_push_message(channel_access_token: str, to_user_id: str, message: str) -> None:
    """透過 LINE Messaging API Push 端點發送單人文字訊息。"""
    payload = json.dumps(
        {
            "to": to_user_id.strip(),
            "messages": [{"type": "text", "text": message[:4900]}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        LINE_PUSH_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {channel_access_token.strip()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"LINE API 回傳狀態碼 {resp.status}")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"LINE API HTTP {exc.code}: {err_body}") from exc


def notify_profile_if_needed(
    profile: UserProfile,
    results: list[HotelPriceResult],
    previous_mins: Optional[dict[tuple[str, str, str, bool], float]] = None,
    line_token: str = "",
) -> bool:
    """檢查單一使用者的通知條件並只推播給該使用者自己。"""
    if profile.notify_channel != "line" or not profile.notify_target.strip():
        return False

    alert_blocks = collect_price_alerts(profile, results, previous_mins=previous_mins)
    if not alert_blocks:
        return False

    token = (line_token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")).strip()
    if not token:
        print(f"[{profile.user_id}] 符合 {len(alert_blocks)} 筆通知條件，但尚未設定 LINE_CHANNEL_ACCESS_TOKEN，已略過發送。")
        return False

    msg = format_line_alert_message(profile.user_id, alert_blocks)
    try:
        send_line_push_message(token, profile.notify_target, msg)
        print(f"[{profile.user_id}] 已發送 LINE 通知給本人（共 {len(alert_blocks)} 筆提醒）。")
        return True
    except Exception as exc:
        print(f"[{profile.user_id}] LINE 通知發送失敗（不影響看板更新）：{exc}")
        return False

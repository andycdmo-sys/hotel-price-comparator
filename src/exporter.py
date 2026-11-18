"""比價結果 CSV 與手機版網頁 (HTML) 匯出模組。"""

import csv
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from src.models import HotelPriceResult
from src.profiles import UserProfile, profile_to_target_prices

DEFAULT_TARGET_PRICES: dict[tuple[str, str], int] = {
    ("沖繩餘家弗飯店", "含早餐方案"): 4193,
}

CSV_COLUMNS = [
    "hotel_name",
    "platform",
    "check_in",
    "check_out",
    "price",
    "currency",
    "room_type",
    "url",
    "scraped_at",
    "error",
]


def _sort_key(result: HotelPriceResult) -> tuple[int, float, str]:
    """將有效價格由低到高排序，失敗或無價格的結果排在最後。"""
    if result.price is None or result.error:
        return (1, float("inf"), result.platform)
    return (0, result.price, result.platform)


def _load_existing_csv(output_path: Path) -> list[HotelPriceResult]:
    """讀取既有 CSV 檔案並還原為 HotelPriceResult 清單。"""
    existing: list[HotelPriceResult] = []
    with output_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            raw_price = (row.get("price") or "").strip()
            parsed_price = float(raw_price) if raw_price else None
            existing.append(
                HotelPriceResult(
                    hotel_name=row.get("hotel_name") or "",
                    platform=row.get("platform") or "",
                    check_in=row.get("check_in") or "",
                    check_out=row.get("check_out") or "",
                    price=parsed_price,
                    currency=row.get("currency") or "TWD",
                    room_type=row.get("room_type") or "",
                    url=row.get("url") or "",
                    scraped_at=row.get("scraped_at") or "",
                    error=(row.get("error") or "") or None,
                )
            )
    return existing


def export_to_csv(
    results: Iterable[HotelPriceResult],
    output_path: Path,
    append: bool = False,
) -> Path:
    """將飯店比價結果匯出為 UTF-8 BOM CSV 檔案（方便 Excel 直接開啟）。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined_results = list(results)
    if append and output_path.exists():
        combined_results = _load_existing_csv(output_path) + combined_results

    sorted_results = sorted(combined_results, key=_sort_key)

    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in sorted_results:
            writer.writerow(
                {
                    "hotel_name": item.hotel_name,
                    "platform": item.platform,
                    "check_in": item.check_in,
                    "check_out": item.check_out,
                    "price": "" if item.price is None else f"{item.price:.0f}",
                    "currency": item.currency,
                    "room_type": item.room_type,
                    "url": item.url,
                    "scraped_at": item.scraped_at,
                    "error": item.error or "",
                }
            )

    return output_path


def _is_record_allowed_by_profile(
    rec: HotelPriceResult,
    profile: UserProfile,
    active_keys: Optional[set[tuple[str, str, str]]] = None,
) -> bool:
    """檢查紀錄是否屬於目前清單內的飯店與啟用中的分站。"""
    keys = active_keys if active_keys is not None else {(h.name, h.check_in, h.check_out) for h in profile.hotels}
    if (rec.hotel_name, rec.check_in, rec.check_out) not in keys:
        return False
    if rec.platform.startswith("Trip.com [") and "站" in rec.platform:
        allowed_sites = [f"{s.strip().upper()}站" for s in profile.sites if s.strip()]
        if allowed_sites and not any(tag in rec.platform for tag in allowed_sites):
            return False
    return True


def prune_csv_by_profile(csv_path: Path, profile: UserProfile) -> list[HotelPriceResult]:
    """依使用者目前追蹤的飯店清單與分站設定清理歷史 CSV，移除已刪除飯店、日期或停用分站的舊紀錄。"""
    if not profile.hotels:
        if csv_path.exists():
            csv_path.unlink()
        return []
    if not csv_path.exists():
        return []

    active_keys = {(h.name, h.check_in, h.check_out) for h in profile.hotels}
    records = _load_existing_csv(csv_path)
    kept = [r for r in records if _is_record_allowed_by_profile(r, profile, active_keys)]
    if len(kept) != len(records):
        export_to_csv(kept, csv_path, append=False)
    return kept


def _normalize_series_label(platform: str) -> str:
    """移除變動的 IP 資訊（例如 `| IP:US (...)`），讓不同次抓價的同分站方案能連成同一條折線。"""
    return re.sub(r"\s*\|\s*IP:[^\]]+", "", platform).strip()


def _short_platform_label(normalized_platform: str) -> str:
    """將平台標籤精簡為適合手機比價清單顯示的短名稱（例如 `Trip.com (TW站)`、`Agoda`）。"""
    cleaned = re.sub(r"\s*\|\s*(?:不限早餐|含早餐)", "", normalized_platform).strip()
    if cleaned.startswith("Trip.com [") and cleaned.endswith("]"):
        inner = cleaned[len("Trip.com [") : -1].strip()
        return f"Trip.com ({inner})"
    if cleaned.startswith("Agoda [") and cleaned.endswith("]"):
        return "Agoda"
    if cleaned.startswith("Booking [") and cleaned.endswith("]"):
        return "Booking.com"
    return cleaned


def _normalize_ota_url(url: str) -> str:
    if not url:
        return ""
    return url.replace("://jp.trip.com/", "://tw.trip.com/").replace("locale=ja-JP", "locale=zh-TW")


def _cluster_batch_labels(timestamps: list[str], gap_minutes: int = 10) -> tuple[list[str], dict[str, str]]:
    """將相隔在 `gap_minutes` 分鐘內的抓價時間合併為同一個批次標籤（MM-DD HH:MM）。"""
    parsed: list[tuple[datetime, str]] = []
    for raw in sorted(set(timestamps)):
        try:
            dt = datetime.fromisoformat(raw)
            parsed.append((dt, raw))
        except ValueError:
            continue

    batch_labels: list[str] = []
    raw_to_label: dict[str, str] = {}
    current_batch_dt: datetime | None = None
    current_label = ""

    for dt, raw in parsed:
        if current_batch_dt is None or (dt - current_batch_dt).total_seconds() > gap_minutes * 60:
            current_batch_dt = dt
            current_label = dt.strftime("%m-%d %H:%M")
            if current_label not in batch_labels:
                batch_labels.append(current_label)
        raw_to_label[raw] = current_label

    return batch_labels, raw_to_label


def _build_plan_group(
    subset: list[HotelPriceResult],
    hotels: list[str],
    labels: list[str],
    raw_to_label: dict[str, str],
) -> dict:
    """依傳入子集（不限早餐或含早餐）產生總覽與各飯店時間序列。"""
    label_index = {lbl: idx for idx, lbl in enumerate(labels)}

    overview_datasets = []
    for hotel in hotels:
        series: list[float | None] = [None] * len(labels)
        for r in subset:
            if r.hotel_name != hotel or r.price is None:
                continue
            lbl = raw_to_label.get(r.scraped_at)
            if lbl is None:
                continue
            idx = label_index[lbl]
            cur = series[idx]
            series[idx] = r.price if cur is None else min(cur, r.price)
        if any(v is not None for v in series):
            overview_datasets.append({"label": hotel, "data": series})

    by_hotel: dict[str, list[dict]] = {}
    for hotel in hotels:
        hotel_recs = [r for r in subset if r.hotel_name == hotel]
        series_names = sorted({_normalize_series_label(r.platform) for r in hotel_recs})
        datasets = []
        for s_name in series_names:
            series = [None] * len(labels)
            for r in hotel_recs:
                if _normalize_series_label(r.platform) != s_name or r.price is None:
                    continue
                lbl = raw_to_label.get(r.scraped_at)
                if lbl is None:
                    continue
                idx = label_index[lbl]
                cur = series[idx]
                series[idx] = r.price if cur is None else min(cur, r.price)
            datasets.append({"label": s_name, "data": series})
        by_hotel[hotel] = datasets

    return {"overview": overview_datasets, "byHotel": by_hotel}


def _build_chart_payload(records: list[HotelPriceResult]) -> dict:
    """整理供前端折線圖使用的時間序列資料（拆分為不限早餐與含早餐兩組）。"""
    valid = [r for r in records if r.price is not None and not r.error and r.scraped_at]
    labels, raw_to_label = _cluster_batch_labels([r.scraped_at for r in valid])
    hotels = sorted({r.hotel_name for r in valid})

    any_records = [r for r in valid if "含早餐" not in r.platform]
    bf_records = [r for r in valid if "含早餐" in r.platform]

    any_group = _build_plan_group(any_records, hotels, labels, raw_to_label)
    bf_group = _build_plan_group(bf_records, hotels, labels, raw_to_label)

    return {
        "labels": labels,
        "hotels": hotels,
        "any": any_group,
        "bf": bf_group,
        "overview": any_group["overview"],
        "byHotel": any_group["byHotel"],
    }


def _latest_updated_str(records: list[HotelPriceResult]) -> str:
    return (
        max((r.scraped_at for r in records), default="")[:19].replace("T", " ")
        or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    )


def _build_cards_html(
    records: list[HotelPriceResult],
    targets: dict[tuple[str, str], int],
) -> list[str]:
    """依入住日期與飯店分組整理飯店卡片 HTML 片段。"""
    grouped: dict[tuple[str, str, str], list[HotelPriceResult]] = {}
    for rec in records:
        if not (rec.check_in and rec.check_out and rec.hotel_name):
            continue
        key = (rec.check_in, rec.check_out, rec.hotel_name)
        bucket = grouped.setdefault(key, [])
        if rec.price is not None and not rec.error:
            bucket.append(rec)

    cards_html: list[str] = []
    for (check_in, check_out, hotel_name), items in sorted(grouped.items()):
        any_items = [r for r in items if "含早餐" not in r.platform]
        bf_items = [r for r in items if "含早餐" in r.platform]

        def _format_plan_box(title: str, plan_items: list[HotelPriceResult]) -> str:
            if not plan_items:
                return f'<div class="plan-box"><div class="plan-title">{html.escape(title)}</div><div class="price-na">無此方案</div></div>'

            # 依正規化平台名分組，各取最新一次紀錄並依價格由低到高排序
            by_plat: dict[str, HotelPriceResult] = {}
            for item in plan_items:
                norm = _normalize_series_label(item.platform)
                prev = by_plat.get(norm)
                if prev is None or item.scraped_at >= prev.scraped_at:
                    by_plat[norm] = item

            latest_by_plat = sorted(
                by_plat.items(),
                key=lambda pair: (pair[1].price is None, pair[1].price or float("inf"), pair[0]),
            )
            winner_norm, winner = latest_by_plat[0]
            winner_short = _short_platform_label(winner_norm)
            lowest = min(plan_items, key=lambda x: x.price or float("inf"))

            diff = (winner.price or 0) - (lowest.price or 0)
            badge = (
                '<span class="badge low">★ 目前即歷史最低</span>'
                if diff <= 0
                else f'<span class="badge">比最低高 TWD {diff:,.0f}</span>'
            )

            def _render_ota_row(norm_name: str, rec: HotelPriceResult, is_winner: bool) -> str:
                short_name = _short_platform_label(norm_name)
                url_clean = _normalize_ota_url(rec.url)
                go_link = (
                    f'<a class="ota-link" href="{html.escape(url_clean)}" target="_blank" rel="noopener">前往</a>'
                    if url_clean
                    else ""
                )
                row_cls = "ota-row is-best" if is_winner else "ota-row"
                return (
                    f'<div class="{row_cls}">'
                    f'<span class="ota-name">{html.escape(short_name)}</span>'
                    f'<span class="ota-price">{html.escape(rec.currency)} {rec.price:,.0f}</span>'
                    f"{go_link}"
                    f"</div>"
                )

            primary_rows = [
                _render_ota_row(norm_n, rec, idx == 0)
                for idx, (norm_n, rec) in enumerate(latest_by_plat[:3])
            ]
            extra_rows = [
                _render_ota_row(norm_n, rec, False)
                for norm_n, rec in latest_by_plat[3:]
            ]
            more_block = ""
            if extra_rows:
                more_block = (
                    f'<details class="ota-more">'
                    f'<summary>展開其他 {len(extra_rows)} 家報價</summary>'
                    f'{"".join(extra_rows)}'
                    f"</details>"
                )

            target_key = f"{hotel_name}||{title}"
            default_target = targets.get((hotel_name, title))
            target_val_attr = str(default_target) if default_target is not None else ""
            return (
                f'<div class="plan-box" data-target-key="{html.escape(target_key)}" data-current-price="{winner.price:.0f}">'
                f'<div class="plan-title">{html.escape(title)}</div>'
                f'<div class="winner-row"><span class="winner-plat">🏆 {html.escape(winner_short)} 最低</span></div>'
                f'<div class="price-main">{html.escape(winner.currency)} {winner.price:,.0f}</div>'
                f'<div class="room-sub">{html.escape(winner.room_type or "標準房型")}</div>'
                f'<div class="meta-sub">歷史最低：{html.escape(lowest.currency)} {lowest.price:,.0f} {badge}</div>'
                f'<div class="ota-list">{"".join(primary_rows)}{more_block}</div>'
                f'<div class="target-row">'
                f'<span class="target-label">🎯 目標價</span>'
                f'<input type="number" class="target-input" min="0" step="1" placeholder="未設定" value="{html.escape(target_val_attr)}">'
                f'<span class="target-status"></span>'
                f'</div>'
                f"</div>"
            )

        cards_html.append(
            f'<section class="hotel-card">'
            f'<div class="date-pill">{html.escape(check_in)} ～ {html.escape(check_out)}</div>'
            f'<h2 class="hotel-name">{html.escape(hotel_name)}</h2>'
            f'<div class="plans-grid">'
            f'{_format_plan_box("不限早餐（最低價）", any_items)}'
            f'{_format_plan_box("含早餐方案", bf_items)}'
            f"</div>"
            f"</section>"
        )

    return cards_html


def export_user_dashboard_json(
    csv_path: Path,
    json_path: Path,
    profile: Optional[UserProfile] = None,
) -> Path:
    """產出單一使用者的專屬看板 JSON (`docs/data/<user_id>.json`)。"""
    records = _load_existing_csv(csv_path) if csv_path.exists() else []
    if profile is not None:
        active_keys = {(h.name, h.check_in, h.check_out) for h in profile.hotels}
        records = [r for r in records if _is_record_allowed_by_profile(r, profile, active_keys)]
    json_path.parent.mkdir(parents=True, exist_ok=True)
    targets = profile_to_target_prices(profile) if profile is not None else DEFAULT_TARGET_PRICES

    cards_html = "".join(_build_cards_html(records, targets))
    chart_payload = _build_chart_payload(records)
    updated_str = _latest_updated_str(records)

    if profile is not None:
        guest_str = f"{profile.adults} 成人" + (
            f" + {profile.children} 兒童 ({profile.child_ages}歲)"
            if profile.children > 0 and profile.child_ages
            else (f" + {profile.children} 兒童" if profile.children > 0 else "")
        )
        subtitle = f"條件：{guest_str} ｜ 幣別：{profile.currency} 含稅 ｜ 最後更新：{updated_str}"
        title = profile.title or f"{profile.user_id} 的飯店比價看板"
        user_id = profile.user_id
    else:
        subtitle = f"條件：2 成人 + 1 兒童 (11歲) ｜ 幣別：TWD 含稅 ｜ 最後更新：{updated_str}"
        title = "飯店每日比價看板"
        user_id = ""

    payload = {
        "user_id": user_id,
        "title": title,
        "subtitle": subtitle,
        "updated_at": updated_str,
        "cards_html": cards_html,
        "chart_payload": chart_payload,
        "profile": profile.to_dict() if profile is not None else None,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path


def export_to_html(
    csv_path: Path,
    html_path: Path,
    target_prices: dict[tuple[str, str], int] | None = None,
) -> Path:
    """根據歷史 CSV 產出適合手機瀏覽的響應式比價看板網頁 (`docs/index.html`)。"""
    records = _load_existing_csv(csv_path) if csv_path.exists() else []
    html_path.parent.mkdir(parents=True, exist_ok=True)
    targets = DEFAULT_TARGET_PRICES if target_prices is None else target_prices

    cards_html = _build_cards_html(records, targets)
    chart_payload = _build_chart_payload(records)
    chart_json = json.dumps(chart_payload, ensure_ascii=False).replace("</", "<\\/")
    updated_str = _latest_updated_str(records)

    page_html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>飯店每日比價看板</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🏨</text></svg>">
  <style>
    :root {{
      --bg: #f4f6fb;
      --card: #ffffff;
      --text: #1a202c;
      --muted: #64748b;
      --primary: #2563eb;
      --accent: #059669;
      --border: #e2e8f0;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft JhengHei", sans-serif;
      background: var(--bg);
      color: var(--text);
      max-width: 760px;
      margin-inline: auto;
    }}
    header {{
      margin-bottom: 16px;
    }}
    h1 {{
      font-size: 1.35rem;
      margin: 0 0 6px 0;
    }}
    .sub-info {{
      font-size: 0.85rem;
      color: var(--muted);
    }}
    .hotel-card {{
      background: var(--card);
      border-radius: 14px;
      padding: 16px;
      margin-bottom: 14px;
      box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06);
      border: 1px solid var(--border);
    }}
    .date-pill {{
      display: inline-block;
      background: #dbeafe;
      color: #1d4ed8;
      font-size: 0.78rem;
      font-weight: 600;
      padding: 3px 10px;
      border-radius: 999px;
      margin-bottom: 6px;
    }}
    .hotel-name {{
      font-size: 1.1rem;
      margin: 4px 0 12px 0;
    }}
    .plans-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }}
    @media (max-width: 480px) {{
      .plans-grid {{ grid-template-columns: 1fr; }}
    }}
    .plan-box {{
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
    }}
    .plan-box.target-hit {{
      background: #ecfdf5;
      border-color: #10b981;
    }}
    .winner-row {{
      margin: 4px 0 2px 0;
    }}
    .winner-plat {{
      display: inline-block;
      background: #eff6ff;
      color: #1d4ed8;
      border: 1px solid #bfdbfe;
      font-size: 0.72rem;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 999px;
    }}
    .ota-list {{
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px dashed var(--border);
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .ota-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      font-size: 0.75rem;
      color: #475569;
    }}
    .ota-row.is-best {{
      color: var(--text);
      font-weight: 700;
    }}
    .ota-name {{
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .ota-price {{
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }}
    .ota-link {{
      color: var(--primary);
      text-decoration: none;
      font-weight: 600;
      white-space: nowrap;
    }}
    .ota-more {{
      margin-top: 2px;
      font-size: 0.74rem;
      color: var(--muted);
    }}
    .ota-more summary {{
      cursor: pointer;
      color: var(--primary);
      font-weight: 600;
      user-select: none;
    }}
    .target-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px dashed var(--border);
      font-size: 0.76rem;
    }}
    .target-label {{
      color: var(--muted);
      font-weight: 600;
    }}
    .target-input {{
      width: 78px;
      padding: 3px 6px;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--text);
      background: #ffffff;
    }}
    .target-status {{
      font-weight: 600;
      color: #b45309;
    }}
    .target-status.hit {{
      color: var(--accent);
    }}
    .plan-title {{
      font-size: 0.82rem;
      color: var(--muted);
      font-weight: 600;
      margin-bottom: 4px;
    }}
    .price-main {{
      font-size: 1.35rem;
      font-weight: 800;
      color: var(--primary);
    }}
    .room-sub {{
      font-size: 0.82rem;
      margin-top: 4px;
      color: #334155;
    }}
    .meta-sub {{
      font-size: 0.76rem;
      color: var(--muted);
      margin-top: 6px;
    }}
    .badge {{
      display: inline-block;
      padding: 2px 6px;
      border-radius: 4px;
      background: #fef3c7;
      color: #b45309;
      font-weight: 600;
    }}
    .badge.low {{
      background: #d1fae5;
      color: var(--accent);
    }}
    .book-btn {{
      display: inline-block;
      margin-top: 8px;
      font-size: 0.78rem;
      color: var(--primary);
      text-decoration: none;
      font-weight: 600;
    }}
    .price-na {{
      font-size: 1rem;
      font-weight: 600;
      color: var(--muted);
      margin-top: 8px;
    }}
    .history-wrap {{
      background: var(--card);
      border-radius: 14px;
      padding: 16px;
      border: 1px solid var(--border);
    }}
    .chart-tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 12px;
    }}
    .chart-tab {{
      border: 1px solid var(--border);
      background: #f8fafc;
      color: var(--muted);
      border-radius: 999px;
      padding: 5px 12px;
      font-size: 0.78rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .chart-tab.active {{
      background: var(--primary);
      color: #ffffff;
      border-color: var(--primary);
    }}
    .chart-subtitle {{
      font-size: 0.9rem;
      font-weight: 700;
      color: var(--text);
      margin: 14px 0 8px 0;
    }}
    .chart-container {{
      position: relative;
      height: 280px;
      width: 100%;
    }}
    .user-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px 14px;
      margin-bottom: 14px;
    }}
    .user-badge {{
      font-size: 0.85rem;
      font-weight: 700;
      color: var(--text);
    }}
    .bar-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .bar-btn {{
      border: 1px solid var(--border);
      background: #f8fafc;
      color: var(--text);
      border-radius: 8px;
      padding: 6px 10px;
      font-size: 0.78rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .bar-btn.primary {{
      background: var(--primary);
      color: #ffffff;
      border-color: var(--primary);
    }}
    .bar-btn.danger {{
      color: #dc2626;
      border-color: #fecaca;
      background: #fef2f2;
    }}
    .gate-card, .manage-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 18px;
      margin-bottom: 14px;
      box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06);
    }}
    .gate-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }}
    .gate-input {{
      flex: 1;
      min-width: 140px;
      padding: 8px 10px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      font-size: 0.9rem;
    }}
    .field-input {{
      width: 100%;
      min-width: 0;
      display: block;
      box-sizing: border-box;
      padding: 8px 10px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      font-size: 0.88rem;
      background: #ffffff;
      color: var(--text);
    }}
    .manage-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }}
    .manage-grid > div, .hotel-row-line1 > div, .hotel-row-line2 > div {{
      min-width: 0;
    }}
    .ages-wrap {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
      min-height: 36px;
    }}
    .ages-wrap .field-input {{
      width: auto;
      flex: 1;
      min-width: 72px;
    }}
    .field-label {{
      display: block;
      font-size: 0.74rem;
      font-weight: 600;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .hotel-edit-row {{
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      margin-bottom: 10px;
    }}
    .hotel-row-line1 {{
      display: grid;
      grid-template-columns: 2fr 1fr 1fr;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .hotel-row-line2 {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-bottom: 8px;
    }}
    .hotel-row-line3 {{
      margin-bottom: 8px;
    }}
    .h-urls-list {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .url-item-row {{
      display: flex;
      flex-direction: column;
      gap: 3px;
    }}
    .url-item-top {{
      display: flex;
      align-items: stretch;
      gap: 8px;
    }}
    .url-item-top .h-url {{
      flex: 1;
      min-width: 0;
    }}
    .url-item-top .bar-btn {{
      flex-shrink: 0;
      white-space: nowrap;
    }}
    .manage-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .hotel-row-footer {{
      display: flex;
      justify-content: flex-end;
    }}
    .url-detected {{
      font-size: 0.72rem;
      color: var(--accent);
      font-weight: 700;
    }}
    .url-detected:empty {{
      display: none;
    }}
    @media (max-width: 560px) {{
      .manage-grid {{
        grid-template-columns: 1fr 1fr;
      }}
      .hotel-row-line1, .hotel-row-line2 {{
        grid-template-columns: 1fr;
      }}
      .url-item-top {{
        flex-wrap: wrap;
      }}
    }}
    .manage-status {{
      font-size: 0.8rem;
      margin-top: 8px;
      color: var(--accent);
      font-weight: 600;
    }}
    .empty-state {{
      background: var(--card);
      border: 1px dashed #cbd5e1;
      border-radius: 14px;
      padding: 20px;
      text-align: center;
      color: var(--muted);
      margin-bottom: 14px;
    }}
    .hidden {{
      display: none !important;
    }}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
</head>
<body>
  <div class="user-bar">
    <span id="current-user-badge" class="user-badge">🔑 尚未輸入專屬代號</span>
    <div class="bar-actions">
      <button type="button" id="btn-toggle-manage" class="bar-btn hidden">⚙️ 管理我的飯店</button>
      <button type="button" id="btn-switch-user" class="bar-btn">🔑 切換代號</button>
    </div>
  </div>

  <section id="passcode-gate" class="gate-card">
    <h2 style="font-size:1.1rem;margin:0 0 6px 0;">🔑 專屬飯店比價看板入口</h2>
    <div class="sub-info">每位使用者輸入自己的專屬代號後，只會載入您自己的飯店清單與價格紀錄（支援英數字、底線 _、連字號 -，區分大小寫）。</div>
    <div class="gate-row">
      <input id="passcode-input" class="gate-input" type="text" placeholder="請輸入您的代號（例如 travel2027）或建立新代號">
      <button type="button" id="btn-enter-user" class="bar-btn primary">進入我的看板</button>
    </div>
    <div id="gate-error" class="sub-info" style="color:#dc2626;margin-top:8px;"></div>
  </section>

  <section id="manage-panel" class="manage-card hidden">
    <div class="manage-header">
      <h2 style="font-size:1.05rem;margin:0;">⚙️ 管理我的飯店與追蹤條件</h2>
      <button type="button" id="btn-close-manage" class="bar-btn">🔼 收回</button>
    </div>
    <div class="manage-grid">
      <div>
        <label class="field-label">成人人數</label>
        <select id="cfg-adults" class="field-input">
          <option value="1">1 位成人</option>
          <option value="2" selected>2 位成人</option>
          <option value="3">3 位成人</option>
          <option value="4">4 位成人</option>
          <option value="5">5 位成人</option>
          <option value="6">6 位成人</option>
          <option value="7">7 位成人</option>
          <option value="8">8 位成人</option>
        </select>
      </div>
      <div>
        <label class="field-label">兒童人數</label>
        <select id="cfg-children" class="field-input">
          <option value="0" selected>0 位兒童</option>
          <option value="1">1 位兒童</option>
          <option value="2">2 位兒童</option>
          <option value="3">3 位兒童</option>
          <option value="4">4 位兒童</option>
        </select>
      </div>
      <div>
        <label class="field-label">兒童年齡</label>
        <div id="cfg-ages-wrap" class="ages-wrap"></div>
      </div>
      <div>
        <label class="field-label">幣別</label>
        <select id="cfg-currency" class="field-input">
          <option value="TWD" selected>TWD (台幣)</option>
          <option value="JPY">JPY (日圓)</option>
          <option value="USD">USD (美金)</option>
          <option value="HKD">HKD (港幣)</option>
          <option value="SGD">SGD (新幣)</option>
        </select>
      </div>
    </div>
    <div id="manage-hotels-list"></div>
    <div class="bar-actions" style="margin-top:10px;">
      <button type="button" id="btn-add-hotel" class="bar-btn">➕ 新增一間飯店</button>
      <button type="button" id="btn-save-profile" class="bar-btn primary">💾 儲存我的清單</button>
      <button type="button" id="btn-copy-profile" class="bar-btn">📋 複製設定檔 JSON</button>
      <button type="button" id="btn-clear-profile" class="bar-btn danger">🗑️ 清空此代號所有資料</button>
      <button type="button" id="btn-collapse-manage" class="bar-btn" style="margin-left:auto;">🔼 收回面板</button>
    </div>
    <div id="manage-status" class="manage-status"></div>
  </section>

  <div id="dashboard-content" class="hidden">
    <header>
      <h1 id="page-title">🏨 飯店每日比價看板</h1>
      <div id="page-subtitle" class="sub-info">條件：2 成人 + 1 兒童 (11歲) ｜ 幣別：TWD 含稅 ｜ 最後更新：{html.escape(updated_str)}</div>
    </header>
    <div id="cards-container">{"".join(cards_html)}</div>
    <div class="history-wrap">
      <h2 style="font-size:1rem;margin:0 0 10px 0;">📈 房價趨勢折線圖</h2>
      <div id="chart-tabs" class="chart-tabs"></div>
      <h3 class="chart-subtitle">🛏️ 不限早餐（最低價）</h3>
      <div class="chart-container">
        <canvas id="priceChart"></canvas>
      </div>
      <h3 class="chart-subtitle">🍴 含早餐方案</h3>
      <div class="chart-container">
        <canvas id="priceChartBf"></canvas>
      </div>
    </div>
  </div>
  <script>
    const CHART_DATA = {chart_json};
    const CLOUD_SYNC_URL = "https://script.google.com/macros/s/AKfycbztzmxWteIkVBG6yTGp_cSiJr9fnaOqYSnspyv4GuoGmUcAGPi_gMiGT4_iKqqfK9uH/exec";
    const PALETTE = ["#2563eb", "#059669", "#d97706", "#7c3aed", "#0891b2", "#db2777"];
    let activeChartData = CHART_DATA;
    let activeHotelKey = "__all__";
    let activeUserId = "";
    let activeProfile = null;

    function targetStorageKey(key) {{
      return activeUserId ? `target:${{activeUserId}}:${{key}}` : `target:${{key}}`;
    }}

    function updateTargetBox(box) {{
      const input = box.querySelector(".target-input");
      const status = box.querySelector(".target-status");
      const current = Number(box.dataset.currentPrice);
      const target = Number(input.value);
      box.classList.remove("target-hit");
      status.classList.remove("hit");
      if (!input.value || isNaN(target) || target <= 0) {{
        status.textContent = "";
        return;
      }}
      const diff = current - target;
      if (diff <= 0) {{
        box.classList.add("target-hit");
        status.classList.add("hit");
        status.textContent = diff === 0 ? "✅ 已達目標價！" : `✅ 低於目標 TWD ${{Math.abs(diff).toLocaleString()}}`;
      }} else {{
        status.textContent = `還差 TWD ${{diff.toLocaleString()}}`;
      }}
    }}

    function bindTargetBoxes() {{
      document.querySelectorAll(".plan-box[data-target-key]").forEach((box) => {{
        const key = targetStorageKey(box.dataset.targetKey);
        const legacyKey = "target:" + box.dataset.targetKey;
        const input = box.querySelector(".target-input");
        const saved = localStorage.getItem(key) ?? localStorage.getItem(legacyKey);
        if (saved !== null) {{
          input.value = saved;
        }}
        updateTargetBox(box);
        input.addEventListener("input", () => {{
          if (input.value === "") {{
            localStorage.removeItem(key);
          }} else {{
            localStorage.setItem(key, input.value);
          }}
          updateTargetBox(box);
          refreshCharts();
        }});
      }});
    }}

    function getTargetPrice(hotelName, planTitle) {{
      const expectedKey = `${{hotelName}}||${{planTitle}}`;
      let foundVal = null;
      document.querySelectorAll(".plan-box[data-target-key]").forEach((box) => {{
        if (box.dataset.targetKey !== expectedKey) return;
        const input = box.querySelector(".target-input");
        const val = Number(input && input.value);
        if (input && input.value && !isNaN(val) && val > 0) {{
          foundVal = val;
        }}
      }});
      return foundVal;
    }}

    function buildDatasets(rawSeries, hotelKey, planTitle) {{
      const datasets = rawSeries.map((item, idx) => {{
        const color = PALETTE[idx % PALETTE.length];
        return {{
          label: item.label,
          data: item.data,
          borderColor: color,
          backgroundColor: color + "22",
          borderWidth: 2.5,
          pointRadius: 4,
          pointHoverRadius: 6,
          tension: 0.25,
          spanGaps: true
        }};
      }});

      if (hotelKey !== "__all__") {{
        const targetVal = getTargetPrice(hotelKey, planTitle);
        if (targetVal !== null) {{
          datasets.push({{
            label: `🎯 目標價 (TWD ${{targetVal.toLocaleString()}})`,
            data: activeChartData.labels.map(() => targetVal),
            borderColor: "#dc2626",
            backgroundColor: "#dc262622",
            borderWidth: 2,
            borderDash: [6, 6],
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0,
            spanGaps: true
          }});
        }}
      }}
      return datasets;
    }}

    function createChartOptions() {{
      return {{
        responsive: true,
        maintainAspectRatio: false,
        interaction: {{ mode: "index", intersect: false }},
        plugins: {{
          legend: {{ position: "bottom", labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }},
          tooltip: {{
            callbacks: {{
              label: (c) => c.parsed.y == null ? `${{c.dataset.label}}: 無資料` : `${{c.dataset.label}}: TWD ${{c.parsed.y.toLocaleString()}}`
            }}
          }}
        }},
        scales: {{
          y: {{
            ticks: {{ callback: (v) => "NT$" + Number(v).toLocaleString() }}
          }}
        }}
      }};
    }}

    bindTargetBoxes();

    const chartAny = new Chart(document.getElementById("priceChart").getContext("2d"), {{
      type: "line",
      data: {{
        labels: activeChartData.labels,
        datasets: buildDatasets(activeChartData.any.overview, "__all__", "不限早餐（最低價）")
      }},
      options: createChartOptions()
    }});

    const chartBf = new Chart(document.getElementById("priceChartBf").getContext("2d"), {{
      type: "line",
      data: {{
        labels: activeChartData.labels,
        datasets: buildDatasets(activeChartData.bf.overview, "__all__", "含早餐方案")
      }},
      options: createChartOptions()
    }});

    function refreshCharts() {{
      const rawAny = activeHotelKey === "__all__" ? activeChartData.any.overview : (activeChartData.any.byHotel[activeHotelKey] || []);
      const rawBf = activeHotelKey === "__all__" ? activeChartData.bf.overview : (activeChartData.bf.byHotel[activeHotelKey] || []);
      chartAny.data.labels = activeChartData.labels;
      chartBf.data.labels = activeChartData.labels;
      chartAny.data.datasets = buildDatasets(rawAny, activeHotelKey, "不限早餐（最低價）");
      chartBf.data.datasets = buildDatasets(rawBf, activeHotelKey, "含早餐方案");
      chartAny.update();
      chartBf.update();
    }}

    function renderChartTabs() {{
      const tabsEl = document.getElementById("chart-tabs");
      tabsEl.innerHTML = "";
      activeHotelKey = "__all__";
      const tabOptions = [{{ key: "__all__", label: "全部飯店（最低價）" }}].concat(
        (activeChartData.hotels || []).map((h) => ({{ key: h, label: h }}))
      );
      tabOptions.forEach((opt, i) => {{
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chart-tab" + (i === 0 ? " active" : "");
        btn.textContent = opt.label;
        btn.addEventListener("click", () => {{
          tabsEl.querySelectorAll(".chart-tab").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          activeHotelKey = opt.key;
          refreshCharts();
        }});
        tabsEl.appendChild(btn);
      }});
    }}

    function renderChildAgeSelects(count, existingAgesStr = "") {{
      const wrap = document.getElementById("cfg-ages-wrap");
      const currentVals = Array.from(wrap.querySelectorAll(".cfg-age-select")).map((s) => s.value);
      const parsed = existingAgesStr
        ? existingAgesStr.split(",").map((x) => x.trim()).filter(Boolean)
        : currentVals;
      wrap.innerHTML = "";
      if (count <= 0) {{
        wrap.innerHTML = '<span class="sub-info">無須設定兒童年齡</span>';
        return;
      }}
      for (let i = 0; i < count; i += 1) {{
        const sel = document.createElement("select");
        sel.className = "field-input cfg-age-select";
        for (let age = 0; age <= 17; age += 1) {{
          const opt = document.createElement("option");
          opt.value = String(age);
          opt.textContent = `${{age}} 歲`;
          sel.appendChild(opt);
        }}
        const fallbackAge = parsed[i] !== undefined ? parsed[i] : "11";
        sel.value = String(Math.min(17, Math.max(0, Number(fallbackAge) || 0)));
        wrap.appendChild(sel);
      }}
    }}

    document.getElementById("cfg-children").addEventListener("change", (e) => {{
      renderChildAgeSelects(Number(e.target.value || 0));
    }});

    function parseBookingUrls(rawText) {{
      let trip_hotel_id = "";
      let agoda_path = "";
      let booking_path = "";
      const detectedLabels = [];
      const tokens = String(rawText || "").split(/[\\s,]+/).map((t) => t.trim()).filter(Boolean);
      tokens.forEach((tok) => {{
        const low = tok.toLowerCase();
        if (low.includes("trip.com") || /^\\d+$/.test(tok) || /(?:optionid|hotelid)=\\d+|hotel-detail-\\d+/i.test(tok)) {{
          const m = tok.match(/(?:optionId=|hotelId=|hotel-detail-)(\\d+)/i);
          trip_hotel_id = m ? m[1] : (/^\\d+$/.test(tok) ? tok : trip_hotel_id);
          if (trip_hotel_id && !detectedLabels.includes("Trip.com")) {{
            detectedLabels.push("Trip.com");
          }}
        }} else if (low.includes("booking.com") || (tok.startsWith("/hotel/") && low.includes(".html"))) {{
          try {{
            const parsed = new URL(tok.startsWith("http") ? tok : "https://www.booking.com" + tok);
            booking_path = parsed.pathname;
          }} catch (_e) {{
            booking_path = tok;
          }}
          if (booking_path && !detectedLabels.includes("Booking.com")) {{
            detectedLabels.push("Booking.com");
          }}
        }} else if (low.includes("agoda.com") || (tok.startsWith("/") && low.includes(".html"))) {{
          try {{
            const parsed = new URL(tok.startsWith("http") ? tok : "https://www.agoda.com" + tok);
            agoda_path = parsed.pathname;
          }} catch (_e) {{
            agoda_path = tok;
          }}
          if (agoda_path && !detectedLabels.includes("Agoda")) {{
            detectedLabels.push("Agoda");
          }}
        }}
      }});
      return {{ trip_hotel_id, agoda_path, booking_path, detectedLabels }};
    }}

    function updateUrlDetectedLabel(urlRow) {{
      const input = urlRow.querySelector(".h-url");
      const badge = urlRow.querySelector(".h-url-detected");
      const {{ detectedLabels }} = parseBookingUrls(input.value);
      badge.textContent = detectedLabels.length > 0 ? `✅ 已自動辨識：${{detectedLabels[0]}}` : "";
    }}

    function createUrlLine(urlsListEl, initialValue = "", isFirst = false) {{
      const urlRow = document.createElement("div");
      urlRow.className = "url-item-row";
      const actionBtnHtml = isFirst
        ? '<button type="button" class="bar-btn btn-add-url">➕ 新增房價網</button>'
        : '<button type="button" class="bar-btn btn-del-url">✕ 移除</button>';
      urlRow.innerHTML = `
        <div class="url-item-top">
          <input class="field-input h-url" type="text" placeholder="貼上單一房價網網址（如 Trip.com、Agoda 或 Booking.com），系統自動判斷">
          ${{actionBtnHtml}}
        </div>
        <div class="url-detected h-url-detected"></div>
      `;
      const inputEl = urlRow.querySelector(".h-url");
      inputEl.value = initialValue;
      updateUrlDetectedLabel(urlRow);
      inputEl.addEventListener("input", () => updateUrlDetectedLabel(urlRow));
      if (isFirst) {{
        urlRow.querySelector(".btn-add-url").addEventListener("click", () => {{
          createUrlLine(urlsListEl, "", false);
        }});
      }} else {{
        urlRow.querySelector(".btn-del-url").addEventListener("click", () => urlRow.remove());
      }}
      urlsListEl.appendChild(urlRow);
    }}

    function addHotelRow(item = {{}}) {{
      const listEl = document.getElementById("manage-hotels-list");
      const row = document.createElement("div");
      row.className = "hotel-edit-row";
      row.innerHTML = `
        <div class="hotel-row-line1">
          <div><label class="field-label">飯店名稱</label><input class="field-input h-name" type="text" placeholder="如 沖繩餘家弗飯店"></div>
          <div><label class="field-label">入住日期</label><input class="field-input h-in" type="date"></div>
          <div><label class="field-label">退房日期</label><input class="field-input h-out" type="date"></div>
        </div>
        <div class="hotel-row-line2">
          <div><label class="field-label">不含早目標價</label><input class="field-input h-tany" type="number" min="0" placeholder="選填"></div>
          <div><label class="field-label">含早目標價</label><input class="field-input h-tbf" type="number" min="0" placeholder="選填"></div>
        </div>
        <div class="hotel-row-line3">
          <label class="field-label">房價網網址（一行一個，選填，系統自動判斷平台）</label>
          <div class="h-urls-list"></div>
        </div>
        <div class="hotel-row-footer">
          <button type="button" class="bar-btn btn-del-hotel">🗑️ 刪除整間飯店</button>
        </div>
      `;
      row.querySelector(".h-name").value = item.name || "";
      row.querySelector(".h-in").value = item.check_in || "";
      row.querySelector(".h-out").value = item.check_out || "";
      row.querySelector(".h-tany").value = item.target_any ?? "";
      row.querySelector(".h-tbf").value = item.target_bf ?? "";
      const initialUrls = [];
      if (Array.isArray(item.urls) && item.urls.length > 0) {{
        item.urls.forEach((u) => {{
          if (String(u || "").trim()) initialUrls.push(String(u).trim());
        }});
      }} else if (item.url) {{
        String(item.url).split(/[\\s,]+/).forEach((u) => {{
          if (u.trim()) initialUrls.push(u.trim());
        }});
      }} else {{
        if (item.trip_hotel_id) {{
          initialUrls.push(/^\\d+$/.test(String(item.trip_hotel_id)) ? `https://tw.trip.com/hotels/list?optionId=${{item.trip_hotel_id}}` : String(item.trip_hotel_id));
        }}
        if (item.agoda_path) {{
          initialUrls.push(String(item.agoda_path).startsWith("http") ? String(item.agoda_path) : `https://www.agoda.com${{item.agoda_path}}`);
        }}
        if (item.booking_path) {{
          initialUrls.push(String(item.booking_path).startsWith("http") ? String(item.booking_path) : `https://www.booking.com${{item.booking_path}}`);
        }}
      }}
      const urlsListEl = row.querySelector(".h-urls-list");
      if (initialUrls.length === 0) {{
        createUrlLine(urlsListEl, "", true);
      }} else {{
        initialUrls.forEach((u, idx) => createUrlLine(urlsListEl, u, idx === 0));
      }}
      row.querySelector(".btn-del-hotel").addEventListener("click", () => row.remove());
      listEl.appendChild(row);
    }}

    function renderManageForm(profileObj) {{
      document.getElementById("cfg-adults").value = String(profileObj?.adults ?? 2);
      const childCount = Number(profileObj?.children ?? 0);
      document.getElementById("cfg-children").value = String(childCount);
      renderChildAgeSelects(childCount, profileObj?.child_ages || "");
      document.getElementById("cfg-currency").value = profileObj?.currency || "TWD";
      const listEl = document.getElementById("manage-hotels-list");
      listEl.innerHTML = "";
      const hotels = profileObj?.hotels || [];
      if (hotels.length === 0) {{
        addHotelRow({{}});
      }} else {{
        hotels.forEach((h) => addHotelRow(h));
      }}
    }}

    function collectProfileFromForm() {{
      const hotels = [];
      document.querySelectorAll("#manage-hotels-list .hotel-edit-row").forEach((row) => {{
        const name = row.querySelector(".h-name").value.trim();
        const check_in = row.querySelector(".h-in").value.trim();
        const check_out = row.querySelector(".h-out").value.trim();
        if (!name || !check_in || !check_out) return;
        const urls = Array.from(row.querySelectorAll(".h-url"))
          .map((inp) => inp.value.trim())
          .filter(Boolean);
        const {{ trip_hotel_id, agoda_path, booking_path }} = parseBookingUrls(urls.join(" "));
        const tAny = row.querySelector(".h-tany").value.trim();
        const tBf = row.querySelector(".h-tbf").value.trim();
        hotels.push({{
          name,
          check_in,
          check_out,
          urls,
          trip_hotel_id,
          agoda_path,
          booking_path,
          target_any: tAny ? Number(tAny) : null,
          target_bf: tBf ? Number(tBf) : null,
        }});
      }});
      const ageValues = Array.from(document.querySelectorAll("#cfg-ages-wrap .cfg-age-select")).map((s) => s.value);
      return {{
        user_id: activeUserId,
        title: `${{activeUserId}} 的飯店比價看板`,
        adults: Number(document.getElementById("cfg-adults").value || 2),
        children: Number(document.getElementById("cfg-children").value || 0),
        child_ages: ageValues.join(","),
        currency: (document.getElementById("cfg-currency").value || "TWD").trim().toUpperCase(),
        sites: ["tw"],
        platforms: ["trip", "agoda", "booking"],
        hotels,
      }};
    }}

    async function loadUserDashboard(userId) {{
      activeUserId = userId;
      localStorage.setItem("hpc_active_user", userId);
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.set("user", userId);
      window.history.replaceState({{}}, "", nextUrl.toString());

      document.getElementById("passcode-gate").classList.add("hidden");
      document.getElementById("dashboard-content").classList.remove("hidden");
      document.getElementById("btn-toggle-manage").classList.remove("hidden");
      document.getElementById("current-user-badge").textContent = `👤 目前看板：${{userId}}`;

      const draftRaw = localStorage.getItem(`profile_draft:${{userId}}`);
      const draftProfile = draftRaw ? JSON.parse(draftRaw) : null;

      try {{
        const resp = await fetch(`data/${{encodeURIComponent(userId)}}.json?t=${{Date.now()}}`);
        if (!resp.ok) throw new Error("NOT_FOUND");
        const data = await resp.json();
        document.getElementById("page-title").textContent = "🏨 " + (data.title || `${{userId}} 的飯店比價看板`);
        document.getElementById("page-subtitle").textContent = data.subtitle || "";
        document.getElementById("cards-container").innerHTML = data.cards_html || '<div class="empty-state">尚無房價資料，請點擊上方「⚙️ 管理我的飯店」新增飯店。</div>';
        activeChartData = data.chart_payload || {{ labels: [], hotels: [], any: {{ overview: [], byHotel: {{}} }}, bf: {{ overview: [], byHotel: {{}} }} }};
        activeProfile = draftProfile || data.profile || {{ user_id: userId, hotels: [] }};
        bindTargetBoxes();
        renderChartTabs();
        refreshCharts();
        renderManageForm(activeProfile);
      }} catch (_err) {{
        document.getElementById("page-title").textContent = `🏨 ${{userId}} 的飯店比價看板`;
        document.getElementById("page-subtitle").textContent = "尚無雲端抓價紀錄（請在下方新增飯店並送出，待雲端排程抓價後即會顯示）";
        document.getElementById("cards-container").innerHTML = '<div class="empty-state">目前還沒有「' + userId + '」的抓價紀錄。<br>請在上方設定您的飯店、日期與人數並儲存清單！</div>';
        activeChartData = {{ labels: [], hotels: [], any: {{ overview: [], byHotel: {{}} }}, bf: {{ overview: [], byHotel: {{}} }} }};
        activeProfile = draftProfile || {{ user_id: userId, adults: 2, children: 0, child_ages: "", currency: "TWD", hotels: [] }};
        renderChartTabs();
        refreshCharts();
        renderManageForm(activeProfile);
        document.getElementById("manage-panel").classList.remove("hidden");
      }}
    }}

    function showPasscodeGate() {{
      document.getElementById("passcode-gate").classList.remove("hidden");
      document.getElementById("dashboard-content").classList.add("hidden");
      document.getElementById("manage-panel").classList.add("hidden");
      document.getElementById("btn-toggle-manage").classList.add("hidden");
      document.getElementById("current-user-badge").textContent = "🔑 尚未輸入專屬代號";
      document.getElementById("cards-container").innerHTML = "";
      document.getElementById("page-title").textContent = "";
      document.getElementById("page-subtitle").textContent = "";
    }}

    document.getElementById("btn-enter-user").addEventListener("click", () => {{
      const raw = document.getElementById("passcode-input").value.trim();
      const errEl = document.getElementById("gate-error");
      if (!/^[A-Za-z0-9_-]{{2,32}}$/.test(raw)) {{
        errEl.textContent = "代號格式不符：請輸入 2～32 碼英數字、底線 _ 或連字號 -。";
        return;
      }}
      errEl.textContent = "";
      loadUserDashboard(raw);
    }});

    document.getElementById("btn-switch-user").addEventListener("click", () => {{
      localStorage.removeItem("hpc_active_user");
      const nextUrl = new URL(window.location.href);
      nextUrl.searchParams.delete("user");
      window.history.replaceState({{}}, "", nextUrl.toString());
      showPasscodeGate();
    }});

    document.getElementById("btn-toggle-manage").addEventListener("click", () => {{
      document.getElementById("manage-panel").classList.toggle("hidden");
    }});

    document.getElementById("btn-close-manage").addEventListener("click", () => {{
      document.getElementById("manage-panel").classList.add("hidden");
    }});

    document.getElementById("btn-collapse-manage").addEventListener("click", () => {{
      document.getElementById("manage-panel").classList.add("hidden");
    }});

    document.getElementById("btn-add-hotel").addEventListener("click", () => addHotelRow({{}}));

    async function syncProfileToCloud(profileObj, isClearing = false) {{
      const statusEl = document.getElementById("manage-status");
      const clearing = isClearing || !profileObj.hotels || profileObj.hotels.length === 0;
      if (clearing) {{
        localStorage.removeItem(`profile_draft:${{activeUserId}}`);
        Object.keys(localStorage).forEach((k) => {{
          if (k.startsWith(`target:${{activeUserId}}:`)) {{
            localStorage.removeItem(k);
          }}
        }});
        document.getElementById("cards-container").innerHTML = '<div class="empty-state">已清空「' + activeUserId + '」的所有飯店資料。</div>';
        activeChartData = {{ labels: [], hotels: [], any: {{ overview: [], byHotel: {{}} }}, bf: {{ overview: [], byHotel: {{}} }} }};
        renderChartTabs();
        refreshCharts();
      }} else {{
        localStorage.setItem(`profile_draft:${{activeUserId}}`, JSON.stringify(profileObj));
      }}

      const syncUrl = localStorage.getItem("hpc_cloud_sync_url") || CLOUD_SYNC_URL;
      if (!syncUrl) {{
        statusEl.textContent = clearing
          ? "✅ 已清空此代號於本機瀏覽器的所有資料！"
          : "✅ 已暫存於本機瀏覽器！（尚未設定雲端轉發網址，如需加入每日排程請點「📋 複製設定檔 JSON」傳給管理員，或填入免費 Google 轉發器即可一鍵同步。）";
        return;
      }}

      statusEl.textContent = clearing ? "正在清除 GitHub 雲端設定與歷史資料..." : "正在上傳設定至 GitHub 並啟動雲端抓價...";
      try {{
        const resp = await fetch(syncUrl, {{
          method: "POST",
          headers: {{ "Content-Type": "text/plain;charset=utf-8" }},
          body: JSON.stringify(profileObj),
        }});
        const resData = await resp.json().catch(() => ({{}}));
        if (!resp.ok || resData.ok === false) throw new Error(resData.error || "SYNC_FAIL");
        statusEl.textContent = clearing
          ? "✅ 已清空此代號於本機與 GitHub 雲端的所有資料！（約 1～2 分鐘後生效）"
          : "✅ 已儲存至 GitHub 並啟動雲端抓價！請約 1～2 分鐘後重新整理網頁查看價格。";
      }} catch (err) {{
        statusEl.textContent = clearing
          ? "⚠️ 已清空本機畫面與暫存，但雲端清除失敗（" + (err && err.message ? err.message : "請稍後再試") + "）。"
          : "⚠️ 已暫存於本機瀏覽器，但雲端同步失敗（" + (err && err.message ? err.message : "請改用複製 JSON 提交") + "）。";
      }}
    }}

    document.getElementById("btn-save-profile").addEventListener("click", async () => {{
      const profileObj = collectProfileFromForm();
      await syncProfileToCloud(profileObj, false);
    }});

    document.getElementById("btn-clear-profile").addEventListener("click", async () => {{
      if (!activeUserId) return;
      if (!window.confirm(`確定要徹底清空「${{activeUserId}}」的所有飯店設定與雲端歷史價格嗎？`)) return;
      const emptyProfile = {{
        user_id: activeUserId,
        title: `${{activeUserId}} 的飯店比價看板`,
        adults: 2,
        children: 0,
        child_ages: "",
        currency: "TWD",
        sites: ["tw"],
        platforms: ["trip", "agoda", "booking"],
        hotels: [],
      }};
      renderManageForm(emptyProfile);
      await syncProfileToCloud(emptyProfile, true);
    }});

    document.getElementById("btn-copy-profile").addEventListener("click", async () => {{
      const profileObj = collectProfileFromForm();
      const text = JSON.stringify(profileObj, null, 2);
      const statusEl = document.getElementById("manage-status");
      try {{
        await navigator.clipboard.writeText(text);
        statusEl.textContent = "📋 已複製您的設定檔 JSON（對應檔案：profiles/" + activeUserId + ".json）！";
      }} catch (_e) {{
        statusEl.textContent = "請手動複製以下設定：\\n" + text;
      }}
    }});

    renderChartTabs();
    const urlParams = new URLSearchParams(window.location.search);
    const initialUser = (urlParams.get("user") || localStorage.getItem("hpc_active_user") || "").trim();
    if (/^[A-Za-z0-9_-]{{2,32}}$/.test(initialUser)) {{
      loadUserDashboard(initialUser);
    }} else if (window.location.protocol.startsWith("http")) {{
      showPasscodeGate();
    }}
  </script>
</body>
</html>
"""
    html_path.write_text(page_html, encoding="utf-8")
    return html_path

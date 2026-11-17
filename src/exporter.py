"""比價結果 CSV 與手機版網頁 (HTML) 匯出模組。"""

import csv
import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.models import HotelPriceResult

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


def export_to_html(
    csv_path: Path,
    html_path: Path,
) -> Path:
    """根據歷史 CSV 產出適合手機瀏覽的響應式比價看板網頁 (`docs/index.html`)。"""
    records = _load_existing_csv(csv_path) if csv_path.exists() else []
    html_path.parent.mkdir(parents=True, exist_ok=True)

    # 依入住日期與飯店分組整理最新價格與歷史最低價
    grouped: dict[tuple[str, str, str], list[HotelPriceResult]] = {}
    for rec in records:
        if rec.price is None or rec.error:
            continue
        key = (rec.check_in, rec.check_out, rec.hotel_name)
        grouped.setdefault(key, []).append(rec)

    cards_html: list[str] = []
    for (check_in, check_out, hotel_name), items in sorted(grouped.items()):
        any_items = [r for r in items if "含早餐" not in r.platform]
        bf_items = [r for r in items if "含早餐" in r.platform]

        def _format_plan_box(title: str, plan_items: list[HotelPriceResult]) -> str:
            if not plan_items:
                return f'<div class="plan-box"><div class="plan-title">{html.escape(title)}</div><div class="price-na">尚無資料</div></div>'
            lowest = min(plan_items, key=lambda x: x.price or float("inf"))
            latest = max(plan_items, key=lambda x: x.scraped_at)
            diff = (latest.price or 0) - (lowest.price or 0)
            badge = (
                '<span class="badge low">★ 目前即歷史最低</span>'
                if diff <= 0
                else f'<span class="badge">比最低高 TWD {diff:,.0f}</span>'
            )
            link = f'<a class="book-btn" href="{html.escape(latest.url)}" target="_blank" rel="noopener">查看 Trip.com</a>' if latest.url else ""
            return (
                f'<div class="plan-box">'
                f'<div class="plan-title">{html.escape(title)}</div>'
                f'<div class="price-main">{html.escape(latest.currency)} {latest.price:,.0f}</div>'
                f'<div class="room-sub">{html.escape(latest.room_type or "標準房型")}</div>'
                f'<div class="meta-sub">歷史最低：{html.escape(lowest.currency)} {lowest.price:,.0f} {badge}</div>'
                f"{link}"
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

    # 歷史紀錄表格（依抓取時間由新到舊排序，最多顯示最近 60 筆）
    recent_rows = sorted(records, key=lambda r: r.scraped_at, reverse=True)[:60]
    table_rows_html = []
    for r in recent_rows:
        price_str = "失敗" if r.price is None else f"{r.currency} {r.price:,.0f}"
        time_short = r.scraped_at[:16].replace("T", " ")
        table_rows_html.append(
            f"<tr>"
            f"<td>{html.escape(time_short)}</td>"
            f"<td>{html.escape(r.hotel_name)}<br><small>{html.escape(r.check_in)}</small></td>"
            f"<td>{html.escape(r.platform)}</td>"
            f'<td class="td-price">{html.escape(price_str)}</td>'
            f"<td>{html.escape(r.room_type)}</td>"
            f"</tr>"
        )

    updated_str = (
        max((r.scraped_at for r in records), default="")[:19].replace("T", " ")
        or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    )

    page_html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>沖繩飯店每日比價看板 (2027/01/26 - 01/30)</title>
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
    .history-wrap {{
      background: var(--card);
      border-radius: 14px;
      padding: 14px;
      border: 1px solid var(--border);
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8rem;
    }}
    th, td {{
      text-align: left;
      padding: 8px 6px;
      border-bottom: 1px solid var(--border);
    }}
    .td-price {{
      font-weight: 700;
      color: var(--primary);
      white-space: nowrap;
    }}
  </style>
</head>
<body>
  <header>
    <h1>🏨 沖繩飯店每日比價看板</h1>
    <div class="sub-info">條件：2 成人 + 1 兒童 (11歲) ｜ 幣別：TWD 含稅 ｜ 最後更新：{html.escape(updated_str)}</div>
  </header>
  {"".join(cards_html)}
  <div class="history-wrap">
    <h2 style="font-size:1rem;margin:0 0 10px 0;">📊 歷史抓價紀錄（最近 60 筆）</h2>
    <table>
      <thead>
        <tr><th>時間</th><th>飯店 / 入住日</th><th>分站與方案</th><th>含稅價</th><th>房型</th></tr>
      </thead>
      <tbody>
        {"".join(table_rows_html)}
      </tbody>
    </table>
  </div>
</body>
</html>
"""
    html_path.write_text(page_html, encoding="utf-8")
    return html_path

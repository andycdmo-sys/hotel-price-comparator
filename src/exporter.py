"""比價結果 CSV 與手機版網頁 (HTML) 匯出模組。"""

import csv
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.models import HotelPriceResult

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


def export_to_html(
    csv_path: Path,
    html_path: Path,
    target_prices: dict[tuple[str, str], int] | None = None,
) -> Path:
    """根據歷史 CSV 產出適合手機瀏覽的響應式比價看板網頁 (`docs/index.html`)。"""
    records = _load_existing_csv(csv_path) if csv_path.exists() else []
    html_path.parent.mkdir(parents=True, exist_ok=True)
    targets = DEFAULT_TARGET_PRICES if target_prices is None else target_prices

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

    chart_payload = _build_chart_payload(records)
    chart_json = json.dumps(chart_payload, ensure_ascii=False).replace("</", "<\\/")

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
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
</head>
<body>
  <header>
    <h1>🏨 沖繩飯店每日比價看板</h1>
    <div class="sub-info">條件：2 成人 + 1 兒童 (11歲) ｜ 幣別：TWD 含稅 ｜ 最後更新：{html.escape(updated_str)}</div>
  </header>
  {"".join(cards_html)}
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
  <script>
    const CHART_DATA = {chart_json};
    const PALETTE = ["#2563eb", "#059669", "#d97706", "#7c3aed", "#0891b2", "#db2777"];
    let activeHotelKey = "__all__";

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

    document.querySelectorAll(".plan-box[data-target-key]").forEach((box) => {{
      const key = "target:" + box.dataset.targetKey;
      const input = box.querySelector(".target-input");
      const saved = localStorage.getItem(key);
      if (saved !== null) {{
        input.value = saved;
      }}
      updateTargetBox(box);
    }});

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
            data: CHART_DATA.labels.map(() => targetVal),
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

    const chartAny = new Chart(document.getElementById("priceChart").getContext("2d"), {{
      type: "line",
      data: {{
        labels: CHART_DATA.labels,
        datasets: buildDatasets(CHART_DATA.any.overview, "__all__", "不限早餐（最低價）")
      }},
      options: createChartOptions()
    }});

    const chartBf = new Chart(document.getElementById("priceChartBf").getContext("2d"), {{
      type: "line",
      data: {{
        labels: CHART_DATA.labels,
        datasets: buildDatasets(CHART_DATA.bf.overview, "__all__", "含早餐方案")
      }},
      options: createChartOptions()
    }});

    function refreshCharts() {{
      const rawAny = activeHotelKey === "__all__" ? CHART_DATA.any.overview : (CHART_DATA.any.byHotel[activeHotelKey] || []);
      const rawBf = activeHotelKey === "__all__" ? CHART_DATA.bf.overview : (CHART_DATA.bf.byHotel[activeHotelKey] || []);
      chartAny.data.datasets = buildDatasets(rawAny, activeHotelKey, "不限早餐（最低價）");
      chartBf.data.datasets = buildDatasets(rawBf, activeHotelKey, "含早餐方案");
      chartAny.update();
      chartBf.update();
    }}

    const tabsEl = document.getElementById("chart-tabs");
    const tabOptions = [{{ key: "__all__", label: "全部飯店（最低價）" }}].concat(
      CHART_DATA.hotels.map((h) => ({{ key: h, label: h }}))
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

    document.querySelectorAll(".plan-box[data-target-key]").forEach((box) => {{
      const key = "target:" + box.dataset.targetKey;
      const input = box.querySelector(".target-input");
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
  </script>
</body>
</html>
"""
    html_path.write_text(page_html, encoding="utf-8")
    return html_path

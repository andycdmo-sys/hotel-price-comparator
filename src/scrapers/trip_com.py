"""Trip.com 多國分站飯店房價爬蟲模組。"""

import re
from typing import Optional
from urllib.parse import quote

from playwright.async_api import BrowserContext, Response

from src.models import HotelPriceResult
from src.scrapers.base import BaseHotelScraper

_RESOLVED_HOTEL_CACHE: dict[str, str] = {}
OKINAWA_DEFAULT_IDS = {"704038", "106709440", "120097372", "2845951"}

SITE_MAP: dict[str, tuple[str, str]] = {
    "tw": ("tw.trip.com", "zh-TW"),
    "jp": ("jp.trip.com", "ja-JP"),
    "hk": ("hk.trip.com", "zh-HK"),
    "sg": ("sg.trip.com", "en-SG"),
    "us": ("us.trip.com", "en-US"),
}

HOTEL_NAME_KEYWORDS = (
    "餘家弗",
    "yugaf",
    "ゆがふいん",
    "vessel",
    "競技場",
    "體育館",
    "アリーナ",
    "b/c",
    "cabins",
    "埃斯蒂納特",
    "艾斯汀納特",
    "estinate",
    "エスティネート",
)
PRICE_LINE_RE = re.compile(r"^(?:TWD|NT\$|TW\$)\s*([\d,]+)$", re.IGNORECASE)
PRICE_INLINE_RE = re.compile(r"(?:TWD|NT\$|TW\$)\s*([\d,]+)", re.IGNORECASE)
ROOM_HINT_KEYWORDS = (
    "房",
    "room",
    "twin",
    "double",
    "king",
    "queen",
    "suite",
    "deluxe",
    "standard",
    "executive",
    "ツイン",
    "ダブル",
    "客室",
    "エグゼクティブ",
    "デラックス",
    "スタンダード",
)
CTA_KEYWORDS = (
    "查看空房",
    "查看房間供應",
    "選擇房間",
    "see availability",
    "check availability",
    "choose room",
    "空室を確認",
    "部屋を選択",
)
TAX_INCLUDED_KEYWORDS = (
    "含稅",
    "連稅",
    "總額",
    "税込",
    "税サ込",
    "includes taxes",
    "incl. taxes",
    "taxes and fees",
)
NON_ROOM_LINE_KEYWORDS = (
    "免費取消",
    "無須支付",
    "含早餐",
    "最新預訂",
    "最新訂單",
    "最新予約",
    "booked",
    "cancellation",
    "breakfast",
    "張單人床",
    "張雙人床",
    "張大床",
    "ベッド",
    "bed",
    "評論",
    "評價",
    "口コミ",
    "review",
    "只剩",
    "left",
    "残り",
)


def _extract_hotel_price_and_room(
    lines: list[str],
    hotel_name: str,
) -> tuple[Optional[float], str]:
    """從頁面文字行中定位飯店卡片並擷取含稅房價與房型名稱。"""
    short_name = re.sub(r"(?:大飯店|大酒店|飯店|酒店|渡假村|度假村|旅館)$", "", hotel_name).strip()
    extra_tokens = [short_name] if len(short_name) >= 2 else []
    name_tokens = [tok.lower() for tok in (hotel_name, *extra_tokens, *HOTEL_NAME_KEYWORDS) if tok]

    # 優先尋找卡片列表區（通常在「找到X處/間住宿 / X軒のホテルがあります / X properties found」之後）
    list_start_idx = 0
    for idx, line in enumerate(lines):
        if re.search(r"(找到\s*\d+\s*[處間]住宿|\d+\s*軒のホテル|\d+\s*propert(?:y|ies)\s*found)", line, re.IGNORECASE):
            list_start_idx = idx
            break

    candidate_indices: list[int] = []
    for idx in range(list_start_idx, len(lines)):
        low = lines[idx].lower()
        if any(tok in low for tok in name_tokens):
            candidate_indices.append(idx)

    # 若列表標記之後沒找到，退回全文搜尋飯店名稱
    if not candidate_indices and list_start_idx > 0:
        for idx, line in enumerate(lines):
            low = line.lower()
            if any(tok in low for tok in name_tokens):
                candidate_indices.append(idx)

    def _scan_candidates(indices: list[int]) -> tuple[Optional[float], str]:
        for start_idx in indices:
            window = lines[start_idx : start_idx + 40]
            window_joined = "\n".join(window).lower()
            if not any(cta in window_joined for cta in CTA_KEYWORDS) and not any(
                tax in window_joined for tax in TAX_INCLUDED_KEYWORDS
            ):
                continue

            # 同時收集獨立價格行與「總額/連稅」內嵌價格行（排除篩選條 `(TWD0 - TWD10,000+)` 與折扣券行）
            price_positions: list[tuple[int, float, bool]] = []
            for offset, line in enumerate(window):
                stripped = line.strip()
                low_line = stripped.lower()
                if any(skip in low_line for skip in ("折扣", "discount", "off", "coupon", "優惠", "高達", "最高", "クーポン")):
                    continue
                match = PRICE_LINE_RE.match(stripped)
                if match:
                    val = float(match.group(1).replace(",", ""))
                    if val > 0:
                        price_positions.append((offset, val, False))
                    continue
                if "(" in stripped and "-" in stripped:
                    continue
                if any(k in low_line for k in ("總額", "連稅", "total", "incl", "合計", "税込")):
                    inline_match = PRICE_INLINE_RE.search(stripped)
                    if inline_match:
                        val = float(inline_match.group(1).replace(",", ""))
                        if val > 0:
                            price_positions.append((offset, val, True))

            if not price_positions:
                continue

            # 優先取明確標示為含稅總額（總額/連稅）的價格；否則取相鄰價格區塊的最後一個價格
            explicit_totals = [item for item in price_positions if item[2]]
            if explicit_totals:
                price_offset, final_price, _ = explicit_totals[-1]
            else:
                price_offset, final_price, _ = price_positions[-1]
                for pos_idx in range(len(price_positions) - 1):
                    curr_off, _, _ = price_positions[pos_idx]
                    next_off, next_val, _ = price_positions[pos_idx + 1]
                    if next_off - curr_off <= 2:
                        price_offset, final_price = next_off, next_val

            # 在價格之前的幾行中尋找房型名稱
            room_type = ""
            search_start = max(1, price_offset - 10)
            candidates_room = window[search_start:price_offset]
            for line in candidates_room:
                low = line.lower()
                if any(hint in low for hint in ROOM_HINT_KEYWORDS) and not any(
                    skip in low for skip in NON_ROOM_LINE_KEYWORDS
                ):
                    room_type = line
                    break

            if not room_type:
                for line in reversed(candidates_room):
                    low = line.lower()
                    if re.fullmatch(r"x\d+|\d+(?:\.\d+)?", line.strip(), re.IGNORECASE):
                        continue
                    if any(skip in low for skip in NON_ROOM_LINE_KEYWORDS):
                        continue
                    room_type = line
                    break

            return final_price, room_type

        return None, ""

    found_price, found_room = _scan_candidates(candidate_indices)
    if found_price is not None:
        return found_price, found_room

    # 若日文/英文站將飯店名稱翻譯而無法比對原文，改用卡片 CTA 按鈕往回定位第一張目標飯店卡片
    cta_starts: list[int] = []
    for idx in range(list_start_idx, len(lines)):
        low = lines[idx].lower()
        if any(cta in low for cta in CTA_KEYWORDS):
            start = max(list_start_idx, idx - 25)
            if start not in cta_starts:
                cta_starts.append(start)

    return _scan_candidates(cta_starts)


class TripComScraper(BaseHotelScraper):
    """Trip.com 多國分站飯店爬蟲。"""

    def __init__(
        self,
        site_code: str = "tw",
        hotel_id: str = "704038",
        city_id: str = "35747",
        province_id: str = "11059",
        country_id: str = "78",
        adults: int = 2,
        children: int = 1,
        child_ages: str = "11",
        rooms: int = 1,
        currency: str = "TWD",
        breakfast_only: bool = False,
        ip_info: str = "",
    ) -> None:
        code = site_code.strip().lower()
        if code not in SITE_MAP:
            supported = ", ".join(sorted(SITE_MAP))
            raise ValueError(f"不支援的 Trip.com 分站代碼 {site_code!r}，可用值：{supported}")

        self.site_code = code
        self.subdomain, self.locale = SITE_MAP[code]
        self.hotel_id = hotel_id.strip()
        if self.hotel_id and self.hotel_id not in OKINAWA_DEFAULT_IDS and city_id.strip() == "35747":
            city_id = ""
            province_id = ""
            country_id = ""
        self.city_id = city_id.strip()
        self.province_id = province_id.strip()
        self.country_id = country_id.strip()
        self.adults = adults
        self.children = children
        self.child_ages = child_ages.strip()
        self.rooms = rooms
        self.currency = currency.strip().upper() or "TWD"
        self.breakfast_only = breakfast_only
        self.ip_info = ip_info.strip()
        meal_label = "含早餐" if self.breakfast_only else "不限早餐"
        self.platform_name = f"Trip.com [{self.site_code.upper()}站 | {meal_label} | IP:{self.ip_info or 'LOCAL'}]"

    def build_search_url(self, hotel_name: str, check_in: str, check_out: str) -> str:
        encoded_word = quote(hotel_name)
        location_params = ""
        if self.city_id:
            location_params += f"&cityId={self.city_id}"
        if self.province_id:
            location_params += f"&provinceId={self.province_id}"
        location_params += "&districtId=0"
        if self.country_id:
            location_params += f"&countryId={self.country_id}"

        url = (
            f"https://{self.subdomain}/hotels/list"
            f"?flexType=1{location_params}"
            f"&searchWord={encoded_word}"
            f"&searchType=H&optionId={self.hotel_id}"
            f"&searchValue=31%7C{self.hotel_id}*31*{self.hotel_id}"
            f"&checkin={check_in}&checkout={check_out}"
            f"&crn={self.rooms}&adult={self.adults}&children={self.children}"
        )
        if self.children > 0 and self.child_ages:
            url += f"&ages={quote(self.child_ages)}"
        if self.breakfast_only:
            url += "&listFilters=5~1*5*1"
        url += f"&curr={self.currency}&locale={self.locale}"
        return url

    async def _lookup_hotel_id(self, context: BrowserContext, hotel_name: str) -> str:
        cached = _RESOLVED_HOTEL_CACHE.get(hotel_name)
        if cached:
            return cached

        page = await context.new_page()
        found: dict[str, str] = {}

        async def _on_resp(resp: Response) -> None:
            if "getHotelKeywords" not in resp.url or resp.status != 200:
                return
            try:
                data = await resp.json()
                kw_list = data.get("data", {}).get("mainKeywordList", {}).get("keywords", [])
                if kw_list:
                    first = kw_list[0].get("keyword", {})
                    hotel_id = str(
                        (first.get("hotelInfo") or {}).get("hotelId")
                        or (first.get("keywordContentInfo") or {}).get("keywordId")
                        or ""
                    ).strip()
                    if hotel_id:
                        found["hotel_id"] = hotel_id
            except Exception:  # noqa: BLE001
                pass

        page.on("response", _on_resp)
        try:
            await page.goto("https://tw.trip.com/hotels/?locale=zh-TW&curr=TWD", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            search_input = page.locator("input[placeholder='打算去哪玩？'], input[type='text']").first
            if await search_input.count() > 0:
                await search_input.click()
                await search_input.fill(hotel_name)
                await page.wait_for_timeout(2500)
        finally:
            await page.close()

        resolved = found.get("hotel_id", "")
        if resolved:
            _RESOLVED_HOTEL_CACHE[hotel_name] = resolved
        return resolved

    async def fetch_price(
        self,
        context: BrowserContext,
        hotel_name: str,
        check_in: str,
        check_out: str,
    ) -> HotelPriceResult:
        if not self.hotel_id:
            self.hotel_id = await self._lookup_hotel_id(context, hotel_name)
            if not self.hotel_id:
                raise RuntimeError(f"無法在 Trip.com 自動搜尋到 {hotel_name} 的飯店 ID，請貼上飯店網址")

        # 非預設沖繩測試飯店時清除預設名護市地區參數，避免錯配
        if self.hotel_id not in OKINAWA_DEFAULT_IDS:
            self.city_id = ""
            self.province_id = ""
            self.country_id = ""

        target_url = self.build_search_url(hotel_name=hotel_name, check_in=check_in, check_out=check_out)
        page = await context.new_page()
        try:
            await page.goto(target_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(6000)

            current_url = page.url.lower()
            if "/account/signin" in current_url or "signin" in (await page.title()).lower():
                raise RuntimeError("頁面跳轉至登入驗證，無法讀取房價")

            body_text = await page.locator("body").inner_text()
            if any(flag in body_text.lower() for flag in ("verify you are human", "captcha", "人機驗證")):
                raise RuntimeError("觸發網站人機驗證 (CAPTCHA)，已停止該分站抓取")

            lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
            price, room_type = _extract_hotel_price_and_room(lines=lines, hotel_name=hotel_name)

            if price is None:
                raise RuntimeError(f"未在頁面中找到 {hotel_name} 的 {self.currency} 房價")

            return HotelPriceResult(
                hotel_name=hotel_name,
                platform=self.platform_name,
                check_in=check_in,
                check_out=check_out,
                price=price,
                currency=self.currency,
                room_type=room_type,
                url=page.url,
            )
        finally:
            await page.close()

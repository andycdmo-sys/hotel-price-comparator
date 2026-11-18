"""多使用者追蹤設定 (Profile) 載入與驗證模組。"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")


def _extract_trip_hotel_id(raw_value: str) -> str:
    val = raw_value.strip()
    if not val:
        return ""
    if val.isdigit():
        return val
    m = re.search(r"(?:optionId=|hotel-detail-)(\d+)", val)
    return m.group(1) if m else val


def _extract_agoda_path(raw_value: str) -> str:
    val = raw_value.strip()
    if not val:
        return ""
    if val.startswith("http://") or val.startswith("https://"):
        parsed = urlparse(val)
        return parsed.path
    return val if val.startswith("/") else "/" + val


def parse_hotel_urls(
    raw_url_field: str,
    trip_id: str = "",
    agoda_path: str = "",
) -> tuple[str, str]:
    """從單一網址輸入欄自動辨識 Trip.com ID 與 Agoda 頁面路徑（可同時包含多個以空白或逗號分隔的網址）。"""
    resolved_trip = _extract_trip_hotel_id(trip_id)
    resolved_agoda = _extract_agoda_path(agoda_path)

    tokens = [tok.strip() for tok in re.split(r"[\s,]+", raw_url_field or "") if tok.strip()]
    for tok in tokens:
        low = tok.lower()
        if "trip.com" in low or tok.isdigit() or "optionid=" in low or "hotel-detail-" in low:
            extracted = _extract_trip_hotel_id(tok)
            if extracted:
                resolved_trip = extracted
        elif "agoda.com" in low or (tok.startswith("/") and ".html" in low):
            extracted_path = _extract_agoda_path(tok)
            if extracted_path:
                resolved_agoda = extracted_path

    return resolved_trip, resolved_agoda


@dataclass
class HotelWatchItem:
    """單一追蹤飯店與日期／目標價設定。"""

    name: str
    check_in: str
    check_out: str
    trip_hotel_id: str = ""
    agoda_path: str = ""
    target_any: Optional[int] = None
    target_bf: Optional[int] = None


@dataclass
class UserProfile:
    """單一使用者的專屬比價看板與抓價條件。"""

    user_id: str
    title: str = ""
    adults: int = 2
    children: int = 0
    child_ages: str = ""
    currency: str = "TWD"
    sites: list[str] = field(default_factory=lambda: ["tw", "jp"])
    platforms: list[str] = field(default_factory=lambda: ["trip", "agoda"])
    hotels: list[HotelWatchItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_user_id(user_id: str) -> str:
    cleaned = user_id.strip()
    if not USER_ID_RE.match(cleaned):
        raise ValueError(
            f"代號 {user_id!r} 格式不符（僅允許 2～32 碼英數字、底線 _ 或連字號 -）"
        )
    return cleaned


def _optional_int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        parsed = int(val)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def load_profile(path: Path) -> UserProfile:
    """從 JSON 檔案載入單一使用者 Profile 並驗證代號格式。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    user_id = validate_user_id(str(raw.get("user_id") or path.stem))

    hotels: list[HotelWatchItem] = []
    for item in raw.get("hotels", []) or []:
        name = str(item.get("name") or "").strip()
        check_in = str(item.get("check_in") or "").strip()
        check_out = str(item.get("check_out") or "").strip()
        if not name or not check_in or not check_out:
            continue
        parsed_trip, parsed_agoda = parse_hotel_urls(
            str(item.get("url") or item.get("urls") or ""),
            trip_id=str(item.get("trip_hotel_id") or ""),
            agoda_path=str(item.get("agoda_path") or ""),
        )
        hotels.append(
            HotelWatchItem(
                name=name,
                check_in=check_in,
                check_out=check_out,
                trip_hotel_id=parsed_trip,
                agoda_path=parsed_agoda,
                target_any=_optional_int(item.get("target_any")),
                target_bf=_optional_int(item.get("target_bf")),
            )
        )

    sites = [str(s).strip().lower() for s in (raw.get("sites") or ["tw", "jp"]) if str(s).strip()]
    platforms = [str(p).strip().lower() for p in (raw.get("platforms") or ["trip", "agoda"]) if str(p).strip()]

    return UserProfile(
        user_id=user_id,
        title=str(raw.get("title") or f"{user_id} 的飯店比價看板").strip(),
        adults=max(1, int(raw.get("adults") or 2)),
        children=max(0, int(raw.get("children") or 0)),
        child_ages=str(raw.get("child_ages") or "").strip(),
        currency=str(raw.get("currency") or "TWD").strip().upper() or "TWD",
        sites=sites or ["tw", "jp"],
        platforms=platforms or ["trip", "agoda"],
        hotels=hotels,
    )


def load_all_profiles(profiles_dir: Path) -> list[UserProfile]:
    """掃描目錄下所有 `*.json` 並載入全部使用者設定。"""
    if not profiles_dir.exists():
        return []
    profiles: list[UserProfile] = []
    for json_file in sorted(profiles_dir.glob("*.json")):
        profiles.append(load_profile(json_file))
    return profiles


def profile_to_target_prices(profile: UserProfile) -> dict[tuple[str, str], int]:
    """轉換 Profile 中各飯店的預設目標價為 exporter 所需格式。"""
    targets: dict[tuple[str, str], int] = {}
    for hotel in profile.hotels:
        if hotel.target_any is not None:
            targets[(hotel.name, "不限早餐（最低價）")] = hotel.target_any
        if hotel.target_bf is not None:
            targets[(hotel.name, "含早餐方案")] = hotel.target_bf
    return targets

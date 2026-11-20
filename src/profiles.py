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
    m = re.search(r"(?:optionId=|hotelId=|hotel-detail-)(\d+)", val, re.IGNORECASE)
    return m.group(1) if m else val


def _extract_url_path(raw_value: str) -> str:
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
    booking_path: str = "",
) -> tuple[str, str, str]:
    """從單一網址輸入欄自動辨識 Trip.com ID、Agoda 路徑與 Booking.com 路徑（可同時包含多個以空白或逗號分隔的網址）。"""
    resolved_trip = _extract_trip_hotel_id(trip_id)
    resolved_agoda = _extract_url_path(agoda_path)
    resolved_booking = _extract_url_path(booking_path)

    tokens = [tok.strip() for tok in re.split(r"[\s,]+", raw_url_field or "") if tok.strip()]
    for tok in tokens:
        low = tok.lower()
        if "trip.com" in low or tok.isdigit() or "optionid=" in low or "hotelid=" in low or "hotel-detail-" in low:
            extracted = _extract_trip_hotel_id(tok)
            if extracted:
                resolved_trip = extracted
        elif "agoda.com" in low or (tok.startswith("/") and ".html" in low and not low.startswith("/hotel/")):
            extracted_path = _extract_url_path(tok)
            if extracted_path:
                resolved_agoda = extracted_path
        elif "booking.com" in low or (tok.startswith("/hotel/") and ".html" in low):
            extracted_path = _extract_url_path(tok)
            if extracted_path:
                resolved_booking = extracted_path

    return resolved_trip, resolved_agoda, resolved_booking


@dataclass
class HotelWatchItem:
    """單一追蹤飯店與日期／目標價設定。"""

    name: str
    check_in: str
    check_out: str
    trip_hotel_id: str = ""
    agoda_path: str = ""
    booking_path: str = ""
    rooms: int = 1
    room_guests: list[int] = field(default_factory=list)
    room_type: str = ""
    match_room_type: bool = False
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
    rooms: int = 1
    room_guests: list[int] = field(default_factory=list)
    currency: str = "TWD"
    sites: list[str] = field(default_factory=lambda: ["tw"])
    platforms: list[str] = field(default_factory=lambda: ["trip", "agoda", "booking"])
    notify_channel: str = "none"
    notify_target: str = ""
    notify_on_target: bool = True
    notify_on_lowest: bool = False
    hotels: list[HotelWatchItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> dict[str, Any]:
        """匯出至公開網頁資料時遮罩通知接收對象，避免暴露個人 ID。"""
        data = asdict(self)
        data["notify_target"] = mask_notify_target(self.notify_target)
        return data


def mask_notify_target(val: str) -> str:
    cleaned = (val or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) > 8:
        return f"{cleaned[:4]}...{cleaned[-4:]}"
    return "****"


def _is_masked_notify_target(val: str) -> bool:
    cleaned = (val or "").strip()
    return bool(cleaned) and ("..." in cleaned or set(cleaned) == {"*"})


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


def _parse_room_split(
    raw_obj: dict[str, Any],
    fallback_rooms: int = 1,
    fallback_guests: Optional[list[int]] = None,
) -> tuple[int, list[int]]:
    has_explicit = ("rooms" in raw_obj) or ("room_guests" in raw_obj)
    if not has_explicit:
        return fallback_rooms, list(fallback_guests or [])

    raw_room_guests = raw_obj.get("room_guests") or []
    room_guests: list[int] = []
    if isinstance(raw_room_guests, list):
        for g in raw_room_guests:
            try:
                val = int(g)
                if val > 0:
                    room_guests.append(val)
            except (TypeError, ValueError):
                continue

    rooms = max(1, int(raw_obj.get("rooms") or len(room_guests) or 1))
    if rooms == 1:
        room_guests = []
    elif room_guests and len(room_guests) != rooms:
        room_guests = room_guests[:rooms]
    return rooms, room_guests


def parse_profile_dict(raw: dict[str, Any], fallback_user_id: str = "") -> UserProfile:
    """從字典解析並正規化單一使用者 Profile。"""
    user_id = validate_user_id(str(raw.get("user_id") or fallback_user_id))
    profile_rooms, profile_room_guests = _parse_room_split(raw)

    hotels: list[HotelWatchItem] = []
    for item in raw.get("hotels", []) or []:
        name = str(item.get("name") or "").strip()
        check_in = str(item.get("check_in") or "").strip()
        check_out = str(item.get("check_out") or "").strip()
        if not name or not check_in or not check_out:
            continue
        raw_urls = item.get("urls")
        if isinstance(raw_urls, list):
            raw_url_str = " ".join(str(u).strip() for u in raw_urls if str(u).strip())
        else:
            raw_url_str = str(item.get("url") or raw_urls or "")
        parsed_trip, parsed_agoda, parsed_booking = parse_hotel_urls(
            raw_url_str,
            trip_id=str(item.get("trip_hotel_id") or ""),
            agoda_path=str(item.get("agoda_path") or ""),
            booking_path=str(item.get("booking_path") or ""),
        )
        h_rooms, h_room_guests = _parse_room_split(
            item,
            fallback_rooms=profile_rooms,
            fallback_guests=profile_room_guests,
        )
        h_room_type = str(item.get("room_type") or "").strip()
        h_match_room_type = bool(item.get("match_room_type")) and bool(h_room_type)
        hotels.append(
            HotelWatchItem(
                name=name,
                check_in=check_in,
                check_out=check_out,
                trip_hotel_id=parsed_trip,
                agoda_path=parsed_agoda,
                booking_path=parsed_booking,
                rooms=h_rooms,
                room_guests=h_room_guests,
                room_type=h_room_type,
                match_room_type=h_match_room_type,
                target_any=_optional_int(item.get("target_any")),
                target_bf=_optional_int(item.get("target_bf")),
            )
        )

    sites = [str(s).strip().lower() for s in (raw.get("sites") or ["tw"]) if str(s).strip()]
    platforms = [str(p).strip().lower() for p in (raw.get("platforms") or ["trip", "agoda", "booking"]) if str(p).strip()]
    adults_val = max(1, int(raw.get("adults") or (sum(profile_room_guests) if profile_room_guests else 2)))

    notify_channel = str(raw.get("notify_channel") or "none").strip().lower()
    if notify_channel not in {"none", "line"}:
        notify_channel = "none"
    notify_target = str(raw.get("notify_target") or "").strip()
    notify_on_target = bool(raw.get("notify_on_target", True))
    notify_on_lowest = bool(raw.get("notify_on_lowest", False))

    return UserProfile(
        user_id=user_id,
        title=str(raw.get("title") or f"{user_id} 的飯店比價看板").strip(),
        adults=adults_val,
        children=max(0, int(raw.get("children") or 0)),
        child_ages=str(raw.get("child_ages") or "").strip(),
        rooms=profile_rooms,
        room_guests=profile_room_guests,
        currency=str(raw.get("currency") or "TWD").strip().upper() or "TWD",
        sites=sites or ["tw"],
        platforms=platforms or ["trip", "agoda", "booking"],
        notify_channel=notify_channel,
        notify_target=notify_target,
        notify_on_target=notify_on_target,
        notify_on_lowest=notify_on_lowest,
        hotels=hotels,
    )


def load_profile(path: Path) -> UserProfile:
    """從 JSON 檔案載入單一使用者 Profile 並驗證代號格式。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return parse_profile_dict(raw, fallback_user_id=path.stem)


def save_profile_from_json_str(
    profile_json: str,
    profiles_dir: Path,
    expected_user_id: str = "",
) -> Path:
    """驗證來自網頁或工作流程的 JSON 字串，並安全寫入 `profiles/<user_id>.json`。"""
    raw = json.loads(profile_json)
    if not isinstance(raw, dict):
        raise ValueError("Profile JSON 必須為 JSON 物件格式")
    profile = parse_profile_dict(raw, fallback_user_id=expected_user_id)
    if expected_user_id and profile.user_id != validate_user_id(expected_user_id):
        raise ValueError(f"user_id 不一致：{profile.user_id!r} != {expected_user_id!r}")

    profiles_dir.mkdir(parents=True, exist_ok=True)
    target_path = profiles_dir / f"{profile.user_id}.json"
    if target_path.exists():
        existing = load_profile(target_path)
        if "notify_channel" not in raw:
            profile.notify_channel = existing.notify_channel
            profile.notify_target = existing.notify_target
            profile.notify_on_target = existing.notify_on_target
            profile.notify_on_lowest = existing.notify_on_lowest
        elif (
            profile.notify_channel != "none"
            and _is_masked_notify_target(profile.notify_target)
            and existing.notify_target
        ):
            profile.notify_target = existing.notify_target
    if profile.notify_channel == "none":
        profile.notify_target = ""

    target_path.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target_path


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

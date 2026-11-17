"""飯店比價資料模型。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class HotelPriceResult:
    """單一平台的飯店房價查詢結果。"""

    hotel_name: str
    platform: str
    check_in: str
    check_out: str
    price: Optional[float]
    currency: str = "TWD"
    room_type: str = ""
    url: str = ""
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )
    error: Optional[str] = None

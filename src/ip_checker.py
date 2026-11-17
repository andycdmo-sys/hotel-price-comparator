"""對外 IP 與國別偵測模組。"""

import json
import urllib.request


def detect_current_ip_info(timeout_sec: float = 5.0) -> str:
    """查詢目前對外 IP 與國家代碼，回傳如 `TW (1.2.3.4)`；失敗時回傳 `UNKNOWN`。"""
    request = urllib.request.Request(
        "https://ipinfo.io/json",
        headers={"User-Agent": "hotel-price-comparator/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            payload = json.loads(response.read().decode("utf-8"))
        country = str(payload.get("country") or "").strip()
        ip_addr = str(payload.get("ip") or "").strip()
        if country and ip_addr:
            return f"{country} ({ip_addr})"
        if country:
            return country
        if ip_addr:
            return ip_addr
    except Exception:  # noqa: BLE001 - IP 偵測失敗不應中斷比價主流程
        pass
    return "UNKNOWN"

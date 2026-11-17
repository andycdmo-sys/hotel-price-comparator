"""環境變數與 Proxy 設定載入模組。"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class ProxyConfig:
    """Playwright Proxy 設定。"""

    server: str = ""
    username: str = ""
    password: str = ""

    def to_playwright_proxy(self) -> Optional[dict[str, str]]:
        """轉換為 Playwright `proxy` 參數格式；未設定 server 時回傳 None。"""
        server = self.server.strip()
        if not server:
            return None

        proxy: dict[str, str] = {"server": server}
        if self.username.strip():
            proxy["username"] = self.username.strip()
        if self.password:
            proxy["password"] = self.password
        return proxy


@dataclass(frozen=True)
class AppConfig:
    """應用程式全域設定。"""

    proxy: ProxyConfig
    headless: bool = True
    timeout_ms: int = 30000
    default_currency: str = "TWD"


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        parsed = int(value.strip())
        return parsed if parsed > 0 else default
    except ValueError:
        return default


def load_config(env_path: Optional[Path] = None) -> AppConfig:
    """從 `.env` 與環境變數載入設定。"""
    if env_path is not None:
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv(override=False)

    proxy = ProxyConfig(
        server=os.getenv("PROXY_SERVER", ""),
        username=os.getenv("PROXY_USERNAME", ""),
        password=os.getenv("PROXY_PASSWORD", ""),
    )

    return AppConfig(
        proxy=proxy,
        headless=_parse_bool(os.getenv("HEADLESS"), default=True),
        timeout_ms=_parse_int(os.getenv("TIMEOUT_MS"), default=30000),
        default_currency=os.getenv("DEFAULT_CURRENCY", "TWD").strip() or "TWD",
    )

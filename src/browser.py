"""Playwright 瀏覽器與無痕 Context 管理模組。"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from playwright.async_api import BrowserContext, async_playwright

from src.config import AppConfig

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


@asynccontextmanager
async def open_incognito_context(config: AppConfig) -> AsyncIterator[BrowserContext]:
    """啟動 Chromium 並建立獨立的無痕 BrowserContext（自動套用 Proxy 設定）。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=config.headless)
        context_kwargs: dict = {
            "viewport": {"width": 1280, "height": 800},
            "locale": "zh-TW",
            "user_agent": DEFAULT_USER_AGENT,
        }

        proxy_settings = config.proxy.to_playwright_proxy()
        if proxy_settings is not None:
            context_kwargs["proxy"] = proxy_settings

        context = await browser.new_context(**context_kwargs)
        context.set_default_timeout(config.timeout_ms)
        try:
            yield context
        finally:
            await context.close()
            await browser.close()

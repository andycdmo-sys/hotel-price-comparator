"""環境設定與 Proxy 解析單元測試。"""

from pathlib import Path

from src.config import ProxyConfig, load_config


def test_proxy_config_disabled_when_server_empty() -> None:
    proxy = ProxyConfig(server="   ", username="user", password="secret")
    assert proxy.to_playwright_proxy() is None


def test_proxy_config_with_credentials() -> None:
    proxy = ProxyConfig(
        server="http://127.0.0.1:7890",
        username="demo_user",
        password="demo_password",
    )
    assert proxy.to_playwright_proxy() == {
        "server": "http://127.0.0.1:7890",
        "username": "demo_user",
        "password": "demo_password",
    }


def test_load_config_from_env_file(tmp_path: Path, monkeypatch) -> None:
    for key in (
        "PROXY_SERVER",
        "PROXY_USERNAME",
        "PROXY_PASSWORD",
        "HEADLESS",
        "TIMEOUT_MS",
        "DEFAULT_CURRENCY",
    ):
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PROXY_SERVER=http://proxy.local:8080",
                "PROXY_USERNAME=alice",
                "PROXY_PASSWORD=pw123",
                "HEADLESS=false",
                "TIMEOUT_MS=45000",
                "DEFAULT_CURRENCY=JPY",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env_file)
    assert config.proxy.to_playwright_proxy() == {
        "server": "http://proxy.local:8080",
        "username": "alice",
        "password": "pw123",
    }
    assert config.headless is False
    assert config.timeout_ms == 45000
    assert config.default_currency == "JPY"

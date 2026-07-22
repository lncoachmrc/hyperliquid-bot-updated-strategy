import pytest

from hyperliquid_v2.runtime.settings import Settings


def base_env(monkeypatch):
    monkeypatch.setenv("V2_DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("V2_WALLET_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("V2_SHADOW_ONLY", "true")
    monkeypatch.setenv("V2_LIVE_TRADING_ENABLED", "false")


def test_settings_require_shadow_only_mode(monkeypatch):
    base_env(monkeypatch)
    settings = Settings.from_env()
    assert settings.shadow_only is True
    assert settings.live_trading_enabled is False


def test_settings_reject_live_trading(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("V2_LIVE_TRADING_ENABLED", "true")
    with pytest.raises(RuntimeError, match="shadow-only"):
        Settings.from_env()

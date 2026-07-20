from pathlib import Path

import pandas as pd

from indicators import completed_candle_age_hours, completed_candle_close_time
from runtime_config import env_bool


def test_completed_daily_candle_age_is_measured_from_close():
    bar_open = pd.Timestamp("2026-07-19T00:00:00Z")
    now = pd.Timestamp("2026-07-20T15:00:00Z")

    assert completed_candle_close_time(bar_open, "1d") == pd.Timestamp(
        "2026-07-20T00:00:00Z"
    )
    assert completed_candle_age_hours(bar_open, "1d", now=now) == 15.0


def test_completed_candle_age_never_becomes_negative():
    bar_open = pd.Timestamp("2026-07-20T00:00:00Z")
    now = pd.Timestamp("2026-07-20T12:00:00Z")

    assert completed_candle_age_hours(bar_open, "1d", now=now) == 0.0


def test_testnet_false_is_parsed_from_environment(monkeypatch):
    monkeypatch.setenv("TESTNET", "false")
    assert env_bool("TESTNET", True) is False


def test_main_uses_runtime_network_for_account_and_market_data():
    source = Path("main.py").read_text(encoding="utf-8")

    assert 'TESTNET = env_bool("TESTNET", True)' in source
    # One occurrence configures HyperLiquidTrader, the other propagates the
    # same environment to analyze_multiple_tickers.
    assert source.count("testnet=TESTNET") >= 2

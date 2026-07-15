from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_config import DEFAULT_STRATEGY_CONFIG
from strategy_core import (
    build_strategy_snapshot,
    donchian_score,
    drawdown_factor,
    exposure_to_execution,
)

CFG = DEFAULT_STRATEGY_CONFIG


def make_frame(kind: str = "up", periods: int = 300) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=periods, freq="D", tz="UTC")
    if kind == "up":
        close = np.linspace(100.0, 220.0, periods)
    elif kind == "down":
        close = np.linspace(220.0, 100.0, periods)
    elif kind == "volatile_up":
        returns = np.where(np.arange(periods) % 2 == 0, 0.10, -0.075)
        close = 100.0 * np.cumprod(1.0 + returns)
    else:
        raise ValueError(kind)
    close = pd.Series(close, index=index)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.linspace(1_000.0, 1_500.0, periods),
        },
        index=index,
    )


def market(funding: float = 0.0001, dislocation_bps: float = 1.0):
    return {
        "funding": funding,
        "oi": 1_000_000.0,
        "mark_px": 220.0,
        "oracle_px": 219.98,
        "mark_oracle_dislocation_bps": dislocation_bps,
    }


def book(spread_bps: float = 1.0):
    return {
        "bid_volume": 100.0,
        "ask_volume": 100.0,
        "best_bid": 219.99,
        "best_ask": 220.01,
        "spread_bps": spread_bps,
        "depth_usd": 1_000_000.0,
    }


def test_donchian_uses_previous_channel_not_current_high():
    frame = make_frame("up")
    original = donchian_score(frame, (20, 55, 120)).iloc[-1]
    changed = frame.copy()
    changed.iloc[-1, changed.columns.get_loc("high")] = 1_000_000.0
    after = donchian_score(changed, (20, 55, 120)).iloc[-1]
    assert after == original


def test_valid_long_candidate_on_favorable_trend():
    snapshot = build_strategy_snapshot("BTC", make_frame("up"), market(), book())
    assert snapshot["status"] == "valid"
    assert snapshot["trend_long"] is True
    assert snapshot["regime"] == "favorable"
    assert snapshot["recommended_action"] == "long_candidate"
    assert snapshot["represented_effective_exposure_before_drawdown"] > 0
    assert snapshot["recommended_exchange_leverage_before_drawdown"] in (1, 2)


def test_adverse_regime_closes_or_holds():
    snapshot = build_strategy_snapshot("BTC", make_frame("down"), market(), book())
    assert snapshot["regime"] == "adverse"
    assert "adverse_market_regime" in snapshot["invalidations"]
    assert snapshot["recommended_action"] == "close_if_open_otherwise_hold"
    assert snapshot["recommended_effective_exposure_before_drawdown"] == 0


def test_extreme_funding_suspends_signal():
    snapshot = build_strategy_snapshot(
        "BTC", make_frame("up"), market(funding=CFG.funding_halt_abs), book()
    )
    assert snapshot["status"] == "suspended"
    assert "funding_filter_halt" in snapshot["invalidations"]
    assert snapshot["recommended_action"] == "close_if_open_otherwise_hold"


def test_wide_spread_suspends_signal():
    snapshot = build_strategy_snapshot(
        "ETH", make_frame("up"), market(), book(CFG.maximum_spread_bps)
    )
    assert snapshot["status"] == "suspended"
    assert "spread_or_orderbook_filter_halt" in snapshot["invalidations"]


def test_high_volatility_reduces_base_exposure():
    low = build_strategy_snapshot("BTC", make_frame("up"), market(), book())
    high = build_strategy_snapshot(
        "BTC", make_frame("volatile_up"), market(), book()
    )
    assert high["realized_volatility_annualized"] > low[
        "realized_volatility_annualized"
    ]
    assert high["volatility_base_exposure"] < low["volatility_base_exposure"]


def test_drawdown_deleveraging_boundaries():
    assert drawdown_factor(-0.01, 0.05, 0.15) == 1.0
    assert drawdown_factor(-0.15, 0.05, 0.15) == 0.0
    assert drawdown_factor(-0.10, 0.05, 0.15) == pytest.approx(0.5)


def test_fractional_exposure_is_represented_with_integer_leverage():
    mapped = exposure_to_execution(1.5, "BTC", CFG)
    assert mapped["exchange_leverage"] == 2
    assert mapped["target_portion_of_balance"] == pytest.approx(0.75)
    assert mapped["represented_effective_exposure"] == pytest.approx(1.5)


def test_sol_balance_portion_cap_is_enforced():
    mapped = exposure_to_execution(1.5, "SOL", CFG)
    assert mapped["exchange_leverage"] == 2
    assert mapped["target_portion_of_balance"] == CFG.asset_balance_portion_caps[
        "SOL"
    ]
    assert mapped["represented_effective_exposure"] == pytest.approx(0.8)


def test_unauthorized_asset_is_rejected():
    snapshot = build_strategy_snapshot("DOGE", make_frame("up"), market(), book())
    assert snapshot["authorized"] is False
    assert snapshot["recommended_action"] == "hold"


def test_insufficient_data_cannot_create_signal():
    snapshot = build_strategy_snapshot(
        "BTC", make_frame("up", periods=100), market(), book()
    )
    assert snapshot["status"] == "insufficient_data"
    assert snapshot["recommended_effective_exposure_before_drawdown"] == 0

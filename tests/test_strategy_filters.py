from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_config import DEFAULT_STRATEGY_CONFIG as CFG
from strategy_core import (
    build_strategy_snapshot,
    dislocation_factor,
    funding_factor,
    portfolio_correlation_factor,
    spread_factor,
    volume_factor,
)


def frame(periods: int = 300, low_last_volume: bool = False) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=periods, freq="D", tz="UTC")
    close = pd.Series(np.linspace(100.0, 220.0, periods), index=index)
    volume = pd.Series(np.linspace(1_000.0, 2_000.0, periods), index=index)
    if low_last_volume:
        volume.iloc[-1] = 1.0
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": volume,
        },
        index=index,
    )


def market(dislocation: float = 1.0):
    return {
        "funding": 0.0001,
        "oi": 1_000_000.0,
        "mark_px": 220.0,
        "oracle_px": 219.98,
        "mark_oracle_dislocation_bps": dislocation,
    }


def book():
    return {
        "bid_volume": 100.0,
        "ask_volume": 100.0,
        "best_bid": 219.99,
        "best_ask": 220.01,
        "spread_bps": 1.0,
        "depth_usd": 1_000_000.0,
    }


def test_mark_oracle_dislocation_halts_strategy():
    snapshot = build_strategy_snapshot(
        "BTC", frame(), market(CFG.dislocation_halt_bps), book()
    )
    assert snapshot["status"] == "suspended"
    assert "mark_oracle_dislocation_halt" in snapshot["invalidations"]


def test_low_daily_volume_reduces_not_increases_exposure():
    normal = build_strategy_snapshot("BTC", frame(), market(), book())
    low = build_strategy_snapshot("BTC", frame(low_last_volume=True), market(), book())
    assert low["liquidity_components"]["volume"] <= normal["liquidity_components"]["volume"]
    assert low["recommended_effective_exposure_before_drawdown"] <= normal[
        "recommended_effective_exposure_before_drawdown"
    ]


def test_high_correlation_uses_configured_reduction():
    assert portfolio_correlation_factor(0.90, CFG) == CFG.correlation_factor
    assert portfolio_correlation_factor(0.20, CFG) == 1.0


def test_missing_spread_or_funding_is_fail_closed():
    assert spread_factor(None, CFG) == 0.0
    assert funding_factor(None, CFG) == 0.0


def test_dislocation_factor_has_reduce_and_halt_regions():
    assert dislocation_factor(1.0, CFG) == 1.0
    assert dislocation_factor(CFG.dislocation_reduce_bps, CFG) == 0.5
    assert dislocation_factor(CFG.dislocation_halt_bps, CFG) == 0.0


def test_volume_factor_reports_threshold():
    factor, threshold = volume_factor(frame(), CFG)
    assert factor in (0.5, 1.0)
    assert threshold is not None and threshold > 0

from __future__ import annotations

import pandas as pd

from strategy_config import DEFAULT_STRATEGY_CONFIG
from strategy_dynamic import apply_dynamic_strategy_overlay


CFG = DEFAULT_STRATEGY_CONFIG


def _base_strategy(*, score=-1 / 3, regime="adverse", invalidations=None):
    return {
        "strategy_name": CFG.name,
        "strategy_version": CFG.version,
        "status": "suspended" if invalidations else "valid",
        "recommended_action": "close_if_open_otherwise_hold",
        "invalidations": list(invalidations or []),
        "donchian_score": score,
        "entry_score": CFG.entry_score,
        "exit_score": CFG.exit_score,
        "trend_long": False,
        "trend_exit": True,
        "regime": regime,
        "regime_factor": 0.0 if regime == "adverse" else 0.5,
        "volatility_base_exposure": 0.8,
        "risk_based_exposure_cap": 0.6,
        "liquidity_factor": 1.0,
        "correlation_factor": 1.0,
        "asset_effective_exposure_cap": CFG.asset_effective_exposure_caps["BTC"],
        "asset_balance_portion_cap": CFG.asset_balance_portion_caps["BTC"],
        "recommended_stop_loss_percent": 4.0,
    }


def _bullish_15m_frame():
    index = pd.date_range("2026-07-20", periods=20, freq="15min", tz="UTC")
    close = [100 + i * 0.2 for i in range(20)]
    frame = pd.DataFrame(index=index)
    frame["close"] = close
    frame["volume"] = 100.0
    frame["ema_20"] = [value - 0.5 for value in close]
    frame["ema_50"] = [value - 1.0 for value in close]
    frame["macd"] = [0.05 + i * 0.01 for i in range(20)]
    frame["rsi_14"] = 62.0
    frame["atr_14"] = 1.0
    return frame


def test_adverse_regime_can_create_capped_tactical_candidate():
    base = _base_strategy(
        score=-1 / 3,
        regime="adverse",
        invalidations=["adverse_market_regime"],
    )

    result = apply_dynamic_strategy_overlay(base, _bullish_15m_frame(), "BTC", CFG)

    assert result["status"] == "valid"
    assert "adverse_market_regime" not in result["invalidations"]
    assert result["regime_factor"] == CFG.adverse_regime_factor
    assert result["recommended_action"] == "tactical_long_candidate"
    assert result["tactical_intraday"]["candidate"] is True
    assert result["recommended_effective_exposure_before_drawdown"] > 0
    assert (
        result["recommended_effective_exposure_before_drawdown"]
        <= CFG.tactical_effective_exposure_cap + 1e-12
    )


def test_hard_invalidation_still_blocks_tactical_candidate():
    base = _base_strategy(
        score=-1 / 3,
        regime="adverse",
        invalidations=["adverse_market_regime", "funding_filter_halt"],
    )

    result = apply_dynamic_strategy_overlay(base, _bullish_15m_frame(), "BTC", CFG)

    assert result["status"] == "suspended"
    assert "funding_filter_halt" in result["invalidations"]
    assert result["recommended_action"] == "close_if_open_otherwise_hold"
    assert result["recommended_effective_exposure_before_drawdown"] == 0


def test_two_of_three_donchian_votes_are_explicit_moderate_long():
    base = _base_strategy(score=1 / 3, regime="neutral", invalidations=[])

    result = apply_dynamic_strategy_overlay(base, _bullish_15m_frame(), "BTC", CFG)

    assert result["donchian_positive_votes"] == 2
    assert result["daily_trend_strength"] == "moderate_long"
    assert result["trend_long"] is True
    assert result["recommended_action"] == "long_candidate"


def test_one_of_three_donchian_votes_do_not_create_daily_long():
    base = _base_strategy(score=-1 / 3, regime="favorable", invalidations=[])

    result = apply_dynamic_strategy_overlay(base, _bullish_15m_frame(), "BTC", CFG)

    assert result["donchian_positive_votes"] == 1
    assert result["trend_long"] is False
    assert result["recommended_action"] == "close_if_open_otherwise_hold"

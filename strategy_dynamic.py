"""Dynamic risk overlay for the daily strategy.

The daily trend/regime remains the strategic anchor, but an adverse daily
regime is treated as a risk multiplier rather than an unconditional trading
halt. A high-quality completed-bar 15-minute setup may therefore become a
risk-budgeted tactical long candidate. Hard data/liquidity invalidations remain
absolute.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from leverage_policy import (
    build_leverage_recommendation,
    tactical_risk_profile,
)
from strategy_config import DEFAULT_STRATEGY_CONFIG, StrategyConfig


ADVERSE_REGIME_INVALIDATION = "adverse_market_regime"


def _finite_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _donchian_positive_votes(score: Any, total_votes: int) -> int:
    numeric = _finite_float(score)
    if numeric is None or total_votes <= 0:
        return 0
    votes = int(round(((numeric + 1.0) / 2.0) * total_votes))
    return max(0, min(total_votes, votes))


def _daily_trend_strength(positive_votes: int, total_votes: int, minimum_votes: int) -> str:
    if positive_votes >= total_votes:
        return "strong_long"
    if positive_votes >= minimum_votes:
        return "moderate_long"
    if positive_votes > 0:
        return "weak_or_adverse"
    return "strong_adverse"


def build_tactical_intraday_snapshot(
    frame_15m: pd.DataFrame,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Evaluate a completed-bar 15m long setup without approving an order."""
    required = {"close", "volume", "ema_20", "ema_50", "macd", "rsi_14", "atr_14"}
    missing = sorted(required.difference(frame_15m.columns))
    if missing or len(frame_15m) < 5:
        return {
            "available": False,
            "candidate": False,
            "missing_columns": missing,
            "confirmations": 0,
            "required_confirmations": cfg.tactical_min_confirmations,
        }

    current = frame_15m.iloc[-1]
    previous = frame_15m.iloc[-2]
    one_hour_ago = frame_15m.iloc[-5]

    price = _finite_float(current.get("close"))
    ema20 = _finite_float(current.get("ema_20"))
    ema50 = _finite_float(current.get("ema_50"))
    macd = _finite_float(current.get("macd"))
    previous_macd = _finite_float(previous.get("macd"))
    rsi14 = _finite_float(current.get("rsi_14"))
    atr14 = _finite_float(current.get("atr_14"))
    volume = _finite_float(current.get("volume"))
    volume_average = _finite_float(frame_15m["volume"].tail(20).mean())
    price_1h_ago = _finite_float(one_hour_ago.get("close"))

    values = [
        price,
        ema20,
        ema50,
        macd,
        previous_macd,
        rsi14,
        atr14,
        volume,
        volume_average,
        price_1h_ago,
    ]
    if any(value is None for value in values):
        return {
            "available": False,
            "candidate": False,
            "reason": "required_15m_indicator_not_available",
            "confirmations": 0,
            "required_confirmations": cfg.tactical_min_confirmations,
        }

    assert price is not None
    assert ema20 is not None
    assert ema50 is not None
    assert macd is not None
    assert previous_macd is not None
    assert rsi14 is not None
    assert atr14 is not None
    assert volume is not None
    assert volume_average is not None
    assert price_1h_ago is not None

    momentum_1h_pct = (price / price_1h_ago - 1.0) * 100.0 if price_1h_ago > 0 else 0.0
    volume_ratio = volume / volume_average if volume_average > 0 else 0.0

    checks = {
        "price_above_ema20": bool(price > ema20),
        "ema20_above_ema50": bool(ema20 > ema50),
        "macd_positive": bool(macd > 0),
        "macd_rising": bool(macd > previous_macd),
        "rsi14_supportive": bool(cfg.tactical_rsi_min <= rsi14 <= cfg.tactical_rsi_max),
        "volume_confirmed": bool(volume_ratio >= cfg.tactical_volume_ratio_min),
        "momentum_1h_positive": bool(momentum_1h_pct > 0),
    }
    confirmations = sum(1 for passed in checks.values() if passed)
    mandatory = checks["price_above_ema20"] and checks["momentum_1h_positive"]
    candidate = bool(mandatory and confirmations >= cfg.tactical_min_confirmations)

    stop_percent = float(
        np.clip(
            cfg.tactical_stop_atr_multiple * atr14 / price * 100.0,
            cfg.minimum_stop_percent,
            cfg.tactical_max_stop_percent,
        )
    )

    return {
        "available": True,
        "candidate": candidate,
        "confirmations": confirmations,
        "required_confirmations": cfg.tactical_min_confirmations,
        "mandatory_conditions_met": bool(mandatory),
        "checks": checks,
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "macd": macd,
        "previous_macd": previous_macd,
        "rsi14": rsi14,
        "atr14": atr14,
        "momentum_1h_pct": momentum_1h_pct,
        "volume_ratio": volume_ratio,
        "recommended_stop_loss_percent": stop_percent,
    }


def apply_dynamic_strategy_overlay(
    strategy: Dict[str, Any],
    frame_15m: pd.DataFrame,
    symbol: str,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Create candidates and leverage evidence; the LLM keeps final authority."""
    result = dict(strategy)
    symbol = symbol.upper()
    original_action = result.get("recommended_action")

    invalidations = [
        item
        for item in list(result.get("invalidations") or [])
        if item != ADVERSE_REGIME_INVALIDATION
    ]
    result["invalidations"] = invalidations

    total_votes = len(cfg.donchian_lookbacks)
    positive_votes = _donchian_positive_votes(result.get("donchian_score"), total_votes)
    trend_long = positive_votes >= cfg.minimum_positive_donchian_votes
    trend_exit = positive_votes < cfg.minimum_positive_donchian_votes

    regime = str(result.get("regime") or "unknown")
    if regime == "favorable":
        regime_multiplier = 1.0
    elif regime == "neutral":
        regime_multiplier = cfg.neutral_regime_factor
    elif regime == "adverse":
        regime_multiplier = cfg.adverse_regime_factor
    else:
        regime_multiplier = 0.0

    result["daily_recommended_action_before_overlay"] = original_action
    result["donchian_positive_votes"] = positive_votes
    result["donchian_total_votes"] = total_votes
    result["minimum_positive_donchian_votes"] = cfg.minimum_positive_donchian_votes
    result["daily_trend_strength"] = _daily_trend_strength(
        positive_votes,
        total_votes,
        cfg.minimum_positive_donchian_votes,
    )
    result["trend_long"] = bool(trend_long)
    result["trend_exit"] = bool(trend_exit)
    result["regime_factor"] = float(regime_multiplier)
    result["adverse_regime_is_hard_halt"] = False
    result["entry_rule"] = "minimum_positive_donchian_votes"

    tactical = build_tactical_intraday_snapshot(frame_15m, cfg)
    result["tactical_intraday"] = tactical
    confirmations = int(tactical.get("confirmations") or 0)
    tactical_profile = tactical_risk_profile(
        symbol=symbol,
        regime=regime,
        donchian_positive_votes=positive_votes,
        tactical_confirmations=confirmations,
        cfg=cfg,
    )
    result["tactical_risk_profile"] = tactical_profile

    volatility_cap = max(
        0.0, _finite_float(result.get("volatility_base_exposure")) or 0.0
    )
    daily_risk_cap = max(
        0.0, _finite_float(result.get("risk_based_exposure_cap")) or 0.0
    )
    asset_cap = max(
        0.0, _finite_float(result.get("asset_effective_exposure_cap")) or 0.0
    )
    daily_raw_cap = min(volatility_cap, daily_risk_cap, asset_cap)

    tactical_stop_percent = max(
        0.0, _finite_float(tactical.get("recommended_stop_loss_percent")) or 0.0
    )
    tactical_stop_fraction = tactical_stop_percent / 100.0
    tactical_risk_cap = (
        cfg.risk_per_trade / tactical_stop_fraction
        if tactical_stop_fraction > 0
        else 0.0
    )
    tactical_raw_cap = min(volatility_cap, tactical_risk_cap, asset_cap)
    result["daily_raw_effective_exposure_cap"] = float(daily_raw_cap)
    result["tactical_stop_based_effective_exposure_cap"] = float(
        tactical_risk_cap
    )
    result["tactical_raw_effective_exposure_cap"] = float(tactical_raw_cap)

    liquidity = max(0.0, _finite_float(result.get("liquidity_factor")) or 0.0)
    correlation = max(0.0, _finite_float(result.get("correlation_factor")) or 0.0)

    final_exposure = 0.0
    action = "close_if_open_otherwise_hold"
    stop_percent = result.get("recommended_stop_loss_percent")

    if invalidations:
        action = "close_if_open_otherwise_hold"
    elif regime == "adverse":
        if tactical.get("candidate"):
            final_exposure = min(
                tactical_raw_cap
                * float(tactical_profile["risk_multiplier"])
                * liquidity
                * correlation,
                float(tactical_profile["effective_exposure_cap"]),
            )
            if final_exposure > 0:
                action = "tactical_long_candidate"
                stop_percent = tactical_stop_percent
        else:
            action = "close_if_open_otherwise_hold"
    elif trend_long:
        final_exposure = daily_raw_cap * regime_multiplier * liquidity * correlation
        action = "long_candidate" if final_exposure > 0 else "hold_or_flat"
    elif trend_exit:
        action = "close_if_open_otherwise_hold"
    else:
        action = "hold_or_flat"

    leverage = build_leverage_recommendation(
        action=action,
        symbol=symbol,
        regime=regime,
        donchian_positive_votes=positive_votes,
        tactical_confirmations=confirmations,
        effective_exposure=final_exposure,
        stop_loss_percent=stop_percent,
        tactical_profile=tactical_profile,
        cfg=cfg,
    )
    represented = float(leverage["represented_effective_exposure"])
    if action in {"long_candidate", "tactical_long_candidate"} and represented <= 0:
        action = "hold_or_flat"

    result["status"] = "valid" if not invalidations else "suspended"
    result["recommended_action"] = action
    result["recommended_stop_loss_percent"] = stop_percent
    result["recommended_effective_exposure_before_drawdown"] = represented
    result["recommended_exchange_leverage_before_drawdown"] = leverage[
        "exchange_leverage"
    ]
    result["recommended_balance_portion_before_drawdown"] = leverage[
        "target_portion_of_balance"
    ]
    result["represented_effective_exposure_before_drawdown"] = represented
    result["dynamic_leverage_policy"] = leverage
    result["tactical_effective_exposure_cap"] = tactical_profile[
        "effective_exposure_cap"
    ]
    result["estimated_account_risk_at_stop_before_drawdown"] = leverage[
        "estimated_account_risk_at_stop"
    ]
    return result

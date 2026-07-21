"""Pure dynamic leverage and exposure policy.

Leverage is treated as a collateral representation of an already approved
economic exposure. It never multiplies the strategy's risk budget by itself.
The selector normally uses 1x-5x and keeps 10x only as an absolute technical
ceiling subject to the live Hyperliquid asset limit.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from strategy_config import DEFAULT_STRATEGY_CONFIG, StrategyConfig


CANDIDATE_ACTIONS = {"long_candidate", "tactical_long_candidate"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def tactical_risk_profile(
    *,
    symbol: str,
    regime: str,
    donchian_positive_votes: int,
    tactical_confirmations: int,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Return quality-sensitive tactical multiplier, cap and leverage tier."""
    symbol = symbol.upper()
    votes = max(0, donchian_positive_votes)
    confirmations = max(0, tactical_confirmations)

    if regime != "adverse":
        cap = cfg.tactical_standard_effective_exposure_cap
        multiplier = 1.0
        leverage = cfg.tactical_standard_leverage
        quality = "non_adverse_tactical"
    elif votes >= 2 and confirmations >= 7:
        cap = cfg.tactical_strong_effective_exposure_cap
        multiplier = 1.0
        leverage = cfg.tactical_strong_leverage
        quality = "adverse_2of3_7of7_strong"
    elif votes >= 2 and confirmations >= 6:
        cap = cfg.tactical_strong_effective_exposure_cap
        multiplier = 0.75
        leverage = cfg.tactical_strong_leverage
        quality = "adverse_2of3_6of7_strong"
    elif votes >= 2 and confirmations >= 5:
        cap = cfg.tactical_moderate_effective_exposure_cap
        multiplier = 0.50
        leverage = cfg.tactical_standard_leverage
        quality = "adverse_2of3_5of7_moderate"
    elif confirmations >= 7:
        cap = cfg.tactical_standard_effective_exposure_cap
        multiplier = 0.35
        leverage = cfg.tactical_standard_leverage
        quality = "adverse_1of3_7of7_standard"
    elif confirmations >= 6:
        cap = cfg.tactical_standard_effective_exposure_cap
        multiplier = 0.30
        leverage = cfg.tactical_standard_leverage
        quality = "adverse_1of3_6of7_standard"
    else:
        cap = cfg.tactical_weak_effective_exposure_cap
        multiplier = cfg.adverse_regime_factor
        leverage = cfg.tactical_weak_leverage
        quality = "adverse_weak"

    symbol_factor = max(
        0.0,
        _as_float(cfg.tactical_symbol_exposure_factors.get(symbol, 1.0), 1.0),
    )
    cap *= symbol_factor
    leverage = max(1, min(leverage, cfg.normal_max_exchange_leverage))
    return {
        "quality": quality,
        "risk_multiplier": float(multiplier),
        "effective_exposure_cap": float(cap),
        "recommended_exchange_leverage": int(leverage),
        "symbol_exposure_factor": float(symbol_factor),
    }


def select_exchange_leverage(
    *,
    action: str,
    regime: str,
    donchian_positive_votes: int,
    tactical_confirmations: int,
    tactical_profile: Optional[Dict[str, Any]] = None,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> int:
    """Select the normal live leverage tier without changing exposure."""
    if action not in CANDIDATE_ACTIONS:
        return 1

    if action == "tactical_long_candidate":
        profile = tactical_profile or {}
        selected = _as_int(
            profile.get("recommended_exchange_leverage"),
            cfg.tactical_weak_leverage,
        )
    elif regime == "favorable":
        selected = (
            cfg.daily_favorable_leverage
            if donchian_positive_votes >= 3
            else max(3, cfg.daily_neutral_leverage)
        )
    elif regime == "neutral":
        selected = (
            cfg.daily_neutral_leverage
            if donchian_positive_votes >= 3
            else max(2, cfg.daily_neutral_leverage - 1)
        )
    else:
        selected = cfg.tactical_standard_leverage

    return max(1, min(selected, cfg.normal_max_exchange_leverage))


def represent_effective_exposure(
    *,
    effective_exposure: float,
    exchange_leverage: int,
    symbol: str,
    live_max_leverage: Optional[int] = None,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Represent exposure with integer leverage while never increasing it."""
    symbol = symbol.upper()
    asset_cap = _as_float(cfg.asset_effective_exposure_caps.get(symbol, 0.0))
    exposure = max(0.0, min(_as_float(effective_exposure), asset_cap))

    hard_cap = max(1, cfg.maximum_exchange_leverage)
    if live_max_leverage is not None:
        hard_cap = min(hard_cap, max(1, _as_int(live_max_leverage, 1)))
    leverage = max(1, min(_as_int(exchange_leverage, 1), hard_cap))

    portion_cap = max(0.0, min(1.0, _as_float(
        cfg.asset_balance_portion_caps.get(symbol, 0.0)
    )))
    desired_portion = exposure / leverage if leverage > 0 else 0.0
    portion = min(desired_portion, portion_cap)
    represented = portion * leverage

    return {
        "exchange_leverage": int(leverage),
        "target_portion_of_balance": float(portion),
        "represented_effective_exposure": float(min(represented, exposure)),
        "requested_effective_exposure": float(exposure),
        "asset_effective_exposure_cap": float(asset_cap),
        "asset_balance_portion_cap": float(portion_cap),
        "hard_exchange_leverage_cap": int(hard_cap),
    }


def build_leverage_recommendation(
    *,
    action: str,
    symbol: str,
    regime: str,
    donchian_positive_votes: int,
    tactical_confirmations: int,
    effective_exposure: float,
    stop_loss_percent: Any,
    tactical_profile: Optional[Dict[str, Any]] = None,
    live_max_leverage: Optional[int] = None,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Apply stop-risk cap, select leverage, then derive collateral portion."""
    stop_fraction = max(0.0, _as_float(stop_loss_percent) / 100.0)
    stop_risk_cap = (
        cfg.risk_per_trade / stop_fraction
        if stop_fraction > 0
        else 0.0
    )
    bounded_exposure = min(max(0.0, _as_float(effective_exposure)), stop_risk_cap)

    selected_leverage = select_exchange_leverage(
        action=action,
        regime=regime,
        donchian_positive_votes=donchian_positive_votes,
        tactical_confirmations=tactical_confirmations,
        tactical_profile=tactical_profile,
        cfg=cfg,
    )
    representation = represent_effective_exposure(
        effective_exposure=bounded_exposure,
        exchange_leverage=selected_leverage,
        symbol=symbol,
        live_max_leverage=live_max_leverage,
        cfg=cfg,
    )
    represented = _as_float(representation["represented_effective_exposure"])
    expected_account_risk = represented * stop_fraction

    return {
        **representation,
        "policy_version": cfg.version,
        "normal_live_leverage_cap": cfg.normal_max_exchange_leverage,
        "absolute_technical_leverage_cap": cfg.maximum_exchange_leverage,
        "stop_loss_percent": _as_float(stop_loss_percent),
        "stop_based_effective_exposure_cap": float(stop_risk_cap),
        "estimated_account_risk_at_stop": float(expected_account_risk),
        "risk_per_trade_limit": float(cfg.risk_per_trade),
        "risk_budget_respected": bool(
            expected_account_risk <= cfg.risk_per_trade + 1e-12
        ),
        "leverage_changes_collateral_not_approved_exposure": True,
    }

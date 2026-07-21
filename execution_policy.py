"""Pre-execution feasibility and dynamic-leverage normalization evidence.

The strategy expresses approved economic exposure. Hyperliquid enforces market
size precision, minimum notional and asset-specific leverage limits. This module
annotates each candidate before the LLM is called so leverage only changes the
collateral representation, never the approved risk budget.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

from leverage_policy import build_leverage_recommendation
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


def enrich_constraints_with_live_leverage(
    constraints: Dict[str, Dict[str, Any]],
    meta: Mapping[str, Any],
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> None:
    """Attach the current Hyperliquid maxLeverage metadata for each asset."""
    for asset in meta.get("universe", []) if isinstance(meta, Mapping) else []:
        if not isinstance(asset, Mapping) or not asset.get("name"):
            continue
        symbol = str(asset["name"]).upper()
        if symbol not in constraints:
            continue
        live_max = max(1, _as_int(asset.get("maxLeverage"), 1))
        constraints[symbol]["live_max_leverage"] = live_max
        constraints[symbol]["bot_absolute_max_leverage"] = min(
            live_max, cfg.maximum_exchange_leverage
        )


def annotate_execution_feasibility(
    indicators: Iterable[Dict[str, Any]],
    constraints: Mapping[str, Dict[str, Any]],
    *,
    portfolio_drawdown_factor: float = 1.0,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> None:
    """Mutate strategy snapshots with final leverage, portion and risk limits."""
    drawdown_factor = max(0.0, min(1.0, _as_float(portfolio_drawdown_factor, 1.0)))
    for item in indicators:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("ticker") or "").upper()
        strategy = item.get("strategy") or {}
        if not symbol or not isinstance(strategy, dict):
            continue

        constraint = constraints.get(symbol) or {}
        available_balance = _as_float(constraint.get("available_balance_usd"))
        minimum_notional = _as_float(
            constraint.get("minimum_executable_notional_usd")
        )
        minimum_effective_exposure = _as_float(
            constraint.get("minimum_executable_effective_exposure")
        )
        recommended_before_drawdown = _as_float(
            strategy.get("represented_effective_exposure_before_drawdown")
            or strategy.get("recommended_effective_exposure_before_drawdown")
        )
        requested_after_drawdown = recommended_before_drawdown * drawdown_factor
        action = str(strategy.get("recommended_action") or "hold")
        candidate = action in CANDIDATE_ACTIONS
        tactical = strategy.get("tactical_intraday") or {}
        tactical_profile = strategy.get("tactical_risk_profile") or {}
        confirmations = _as_int(
            tactical.get("confirmations") if isinstance(tactical, dict) else 0
        )
        positive_votes = _as_int(strategy.get("donchian_positive_votes"), 0)
        live_max = _as_int(
            constraint.get("live_max_leverage"),
            cfg.maximum_exchange_leverage,
        )

        leverage = build_leverage_recommendation(
            action=action,
            symbol=symbol,
            regime=str(strategy.get("regime") or "unknown"),
            donchian_positive_votes=positive_votes,
            tactical_confirmations=confirmations,
            effective_exposure=requested_after_drawdown,
            stop_loss_percent=strategy.get("recommended_stop_loss_percent"),
            tactical_profile=(
                tactical_profile if isinstance(tactical_profile, dict) else None
            ),
            live_max_leverage=live_max,
            cfg=cfg,
        )
        final_effective_exposure = _as_float(
            leverage.get("represented_effective_exposure")
        )
        recommended_notional = final_effective_exposure * available_balance
        risk_respected = leverage.get("risk_budget_respected") is True
        feasible = bool(
            candidate
            and constraint.get("available") is True
            and risk_respected
            and recommended_notional + 1e-9 >= minimum_notional
            and final_effective_exposure + 1e-12 >= minimum_effective_exposure
        )

        strategy["execution_feasible"] = feasible
        strategy["final_dynamic_leverage"] = leverage
        strategy["execution_feasibility"] = {
            "candidate_action": candidate,
            "available": constraint.get("available", False),
            "recommended_effective_exposure_before_drawdown": recommended_before_drawdown,
            "portfolio_drawdown_factor": drawdown_factor,
            "final_effective_exposure": final_effective_exposure,
            "final_exchange_leverage": leverage.get("exchange_leverage"),
            "final_target_portion_of_balance": leverage.get(
                "target_portion_of_balance"
            ),
            "estimated_account_risk_at_stop": leverage.get(
                "estimated_account_risk_at_stop"
            ),
            "risk_per_trade_limit": cfg.risk_per_trade,
            "risk_budget_respected": risk_respected,
            "live_max_leverage": live_max,
            "bot_absolute_max_leverage": min(
                live_max, cfg.maximum_exchange_leverage
            ),
            "recommended_order_notional_usd": recommended_notional,
            "minimum_executable_effective_exposure": minimum_effective_exposure,
            "minimum_executable_notional_usd": minimum_notional,
            "minimum_executable_size": constraint.get("minimum_executable_size"),
            "size_decimals": constraint.get("size_decimals"),
            "reason": (
                "executable"
                if feasible
                else (
                    "not_a_candidate"
                    if not candidate
                    else (
                        "risk_budget_not_respected"
                        if not risk_respected
                        else "final_order_below_exchange_minimum"
                    )
                )
            ),
        }
        item["strategy"] = strategy


def compact_execution_feasibility(
    indicators: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for item in indicators:
        if not isinstance(item, dict) or not item.get("ticker"):
            continue
        strategy = item.get("strategy") or {}
        result[str(item["ticker"]).upper()] = strategy.get(
            "execution_feasibility", {}
        )
    return result

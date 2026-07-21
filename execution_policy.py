"""Pre-execution feasibility checks for strategy candidates.

The strategy expresses desired effective exposure. Hyperliquid also enforces a
minimum notional and market-specific size precision. This module annotates the
strategy evidence before the LLM is called so an impossible order is not sent
or silently increased.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping


CANDIDATE_ACTIONS = {"long_candidate", "tactical_long_candidate"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def annotate_execution_feasibility(
    indicators: Iterable[Dict[str, Any]],
    constraints: Mapping[str, Dict[str, Any]],
) -> None:
    """Mutate indicator strategy snapshots with auditable execution limits."""
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
        recommended_effective_exposure = _as_float(
            strategy.get("represented_effective_exposure_before_drawdown")
            or strategy.get("recommended_effective_exposure_before_drawdown")
        )
        recommended_notional = recommended_effective_exposure * available_balance
        action = strategy.get("recommended_action")
        candidate = action in CANDIDATE_ACTIONS
        feasible = bool(
            candidate
            and constraint.get("available") is True
            and recommended_notional + 1e-9 >= minimum_notional
            and recommended_effective_exposure + 1e-12
            >= minimum_effective_exposure
        )

        strategy["execution_feasible"] = feasible
        strategy["execution_feasibility"] = {
            "candidate_action": candidate,
            "available": constraint.get("available", False),
            "recommended_effective_exposure": recommended_effective_exposure,
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
                    else "recommended_order_below_exchange_minimum"
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

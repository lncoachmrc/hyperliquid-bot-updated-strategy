"""Post-LLM safety guard for position-aware decisions.

The LLM still chooses OPEN/CLOSE/HOLD. This guard enforces only deterministic
constraints that the execution adapter must never violate: close hysteresis,
minimum holding time, one position per coin and executable candidate status.
Any adjustment is stored alongside the original LLM decision for auditability.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping


CANDIDATE_ACTIONS = {"long_candidate", "tactical_long_candidate"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "1.0", "yes"}
    return False


def _strategy_map(indicators: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in indicators:
        if not isinstance(item, dict) or not item.get("ticker"):
            continue
        strategy = item.get("strategy") or {}
        result[str(item["ticker"]).upper()] = (
            strategy if isinstance(strategy, dict) else {}
        )
    return result


def _to_safe_hold(
    decision: Dict[str, Any],
    *,
    symbol: str,
    guard_reason: str,
    original: Mapping[str, Any],
) -> Dict[str, Any]:
    guarded = dict(decision)
    guarded.update(
        {
            "operation": "hold",
            "symbol": symbol,
            "direction": "long",
            "target_portion_of_balance": 0.0,
            "leverage": 1,
            "stop_loss_percent": 1.0,
            "reason": guard_reason[:300],
            "decision_guard_adjusted": True,
            "decision_guard_reason": guard_reason,
            "llm_original_decision": dict(original),
        }
    )
    return guarded


def apply_decision_guard(
    decision: Dict[str, Any],
    account_status: Dict[str, Any],
    indicators: Iterable[Dict[str, Any]],
    management_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Return the executable decision while retaining the original for audit."""
    original = deepcopy(decision)
    guarded = dict(decision)
    operation = str(guarded.get("operation") or "hold")
    symbol = str(guarded.get("symbol") or "BTC").upper()
    guarded["symbol"] = symbol

    positions = account_status.get("open_positions") or []
    open_symbols = {
        str(position.get("symbol") or "").upper()
        for position in positions
        if position.get("symbol")
    }
    preferred = str(
        management_state.get("preferred_hold_symbol")
        or (sorted(open_symbols)[0] if open_symbols else symbol)
    ).upper()
    eligible_close = {
        str(item).upper()
        for item in (management_state.get("eligible_close_symbols") or [])
    }
    strategies = _strategy_map(indicators)

    if operation == "hold":
        if open_symbols and symbol not in open_symbols:
            guarded["symbol"] = preferred
            guarded["decision_guard_adjusted"] = True
            guarded["decision_guard_reason"] = (
                "HOLD rebound to an actually open position; the LLM selected a flat asset."
            )
            guarded["llm_original_decision"] = original
        return guarded

    if operation == "close":
        if symbol not in open_symbols:
            return _to_safe_hold(
                guarded,
                symbol=preferred,
                guard_reason=(
                    "CLOSE blocked because the selected symbol has no open position."
                ),
                original=original,
            )
        if symbol not in eligible_close:
            return _to_safe_hold(
                guarded,
                symbol=symbol,
                guard_reason=(
                    "CLOSE blocked by minimum-hold/tactical-hysteresis policy; "
                    "no hard invalidation or confirmed two-cycle exit was present."
                ),
                original=original,
            )
        return guarded

    if operation == "open":
        if symbol in open_symbols:
            return _to_safe_hold(
                guarded,
                symbol=symbol,
                guard_reason="OPEN blocked because a position for this symbol already exists.",
                original=original,
            )
        strategy = strategies.get(symbol) or {}
        action = strategy.get("recommended_action")
        feasible_raw = strategy.get("execution_feasible")
        feasible = True if feasible_raw is None else _as_bool(feasible_raw)
        if action not in CANDIDATE_ACTIONS or not feasible:
            return _to_safe_hold(
                guarded,
                symbol=preferred if open_symbols else symbol,
                guard_reason=(
                    "OPEN blocked because the symbol is not a currently executable "
                    "daily/tactical candidate."
                ),
                original=original,
            )
        return guarded

    return _to_safe_hold(
        guarded,
        symbol=preferred,
        guard_reason="Unknown operation blocked by decision safety guard.",
        original=original,
    )

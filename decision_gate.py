"""Cheap deterministic prefilter for deciding when an LLM call is useful.

This gate can only skip an unnecessary LLM call by producing HOLD. It never
creates OPEN or CLOSE decisions, so the LLM remains the final authority when a
candidate or a position-management review is due.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional, Tuple


ACTIONABLE_CANDIDATES = {"long_candidate", "tactical_long_candidate"}


def _has_recent_stop_loss(stop_losses: Any) -> bool:
    if stop_losses is None:
        return False
    if isinstance(stop_losses, str):
        text = stop_losses.strip()
        if not text:
            return False
        try:
            return bool(json.loads(text))
        except Exception:  # noqa: BLE001
            return text not in {"[]", "{}", "null", "None"}
    return bool(stop_losses)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "1.0", "yes"}
    return False


def _actionable_symbols(indicators: Iterable[Dict[str, Any]]) -> list[str]:
    candidates: list[str] = []
    for item in indicators:
        if not isinstance(item, dict):
            continue
        strategy = item.get("strategy") or {}
        action = strategy.get("recommended_action")
        feasible_raw = strategy.get("execution_feasible")
        feasible = True if feasible_raw is None else _as_bool(feasible_raw)
        if action in ACTIONABLE_CANDIDATES and feasible:
            candidates.append(str(item.get("ticker") or "unknown").upper())
    return candidates


def should_invoke_llm(
    indicators: Iterable[Dict[str, Any]],
    account_status: Dict[str, Any],
    stop_losses: Any = None,
    management_state: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Call the LLM only when it can add value to this cycle."""
    if _has_recent_stop_loss(stop_losses):
        return True, "recent_stop_loss_requires_review"

    open_positions = account_status.get("open_positions") or []
    if open_positions:
        # Missing management state fails open to the LLM rather than leaving a
        # live position unmanaged.
        if not isinstance(management_state, dict):
            return True, "position_management_state_unavailable"

        immediate = management_state.get("immediate_llm_reasons") or []
        if immediate:
            return True, "position_event:" + ",".join(str(item) for item in immediate)

        if management_state.get("llm_review_due") is True:
            return True, "stable_position_scheduled_review"

        return False, "stable_open_position_review_not_due"

    candidates = _actionable_symbols(indicators)
    if candidates:
        return True, "actionable_candidates:" + ",".join(candidates)

    return False, "flat_account_and_no_executable_candidate"


def deterministic_hold(
    reason: str,
    *,
    symbol: str = "BTC",
    management_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a schema-valid HOLD without calling the LLM."""
    selected_symbol = str(symbol or "BTC").upper()
    if isinstance(management_state, dict):
        selected_symbol = str(
            management_state.get("preferred_hold_symbol") or selected_symbol
        ).upper()
    return {
        "operation": "hold",
        "symbol": selected_symbol,
        "direction": "long",
        "target_portion_of_balance": 0.0,
        "leverage": 1,
        "stop_loss_percent": 1.0,
        "reason": f"LLM skipped by deterministic prefilter: {reason}.",
    }

"""Cheap deterministic prefilter for deciding when an LLM call is useful.

This gate can only skip an unnecessary LLM call by producing HOLD while the
account is flat and no strategy candidate exists.  It never creates OPEN or
CLOSE decisions, so the LLM remains the final authority for actionable trading
and position-management decisions.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple


ACTIONABLE_CANDIDATES = {"long_candidate", "tactical_long_candidate"}


def should_invoke_llm(
    indicators: Iterable[Dict[str, Any]],
    account_status: Dict[str, Any],
    stop_losses: Any = None,
) -> Tuple[bool, str]:
    open_positions = account_status.get("open_positions") or []
    if open_positions:
        return True, "open_position_requires_management"

    if stop_losses:
        return True, "recent_stop_loss_requires_review"

    candidates = []
    for item in indicators:
        strategy = item.get("strategy") or {}
        action = strategy.get("recommended_action")
        if action in ACTIONABLE_CANDIDATES:
            candidates.append(str(item.get("ticker") or "unknown"))

    if candidates:
        return True, "actionable_candidates:" + ",".join(candidates)

    return False, "flat_account_and_no_actionable_candidate"


def deterministic_hold(reason: str) -> Dict[str, Any]:
    """Return a schema-valid HOLD without calling the LLM."""
    return {
        "operation": "hold",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.0,
        "leverage": 1,
        "stop_loss_percent": 1.0,
        "reason": f"LLM skipped by deterministic prefilter: {reason}.",
    }

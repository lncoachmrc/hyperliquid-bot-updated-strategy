"""Cheap deterministic prefilter for deciding when an LLM call is useful.

This gate can only skip an unnecessary LLM call by producing HOLD. It never
creates OPEN or CLOSE decisions, so the LLM remains the final authority when a
candidate or a position-management review is due.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, Optional, Tuple


ACTIONABLE_CANDIDATES = {"long_candidate", "tactical_long_candidate"}
DEFAULT_FLAT_CANDIDATE_REVIEW_MINUTES = 15.0
MINIMUM_FLAT_CANDIDATE_REVIEW_MINUTES = 5.0


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


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _flat_candidate_review_minutes() -> float:
    """Return the flat-candidate review cadence without affecting open positions."""
    raw = os.getenv(
        "FLAT_CANDIDATE_LLM_REVIEW_MINUTES",
        str(DEFAULT_FLAT_CANDIDATE_REVIEW_MINUTES),
    )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_FLAT_CANDIDATE_REVIEW_MINUTES
    return max(MINIMUM_FLAT_CANDIDATE_REVIEW_MINUTES, value)


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
    if isinstance(management_state, dict):
        blocked = {
            str(item).upper()
            for item in (management_state.get("reentry_blocked_symbols") or [])
        }
        candidates = [symbol for symbol in candidates if symbol not in blocked]

    if not candidates:
        return False, "flat_account_and_no_executable_candidate"

    # New and materially improved candidates remain immediate. An unchanged but
    # still executable flat-account candidate is reviewed more frequently than a
    # stable open position because its entry window can disappear before the old
    # 30-minute global cadence. This changes only LLM timing; it cannot create a
    # candidate, bypass re-entry, alter risk, or authorize execution.
    if isinstance(management_state, dict) and "llm_review_due" in management_state:
        new_candidates = {
            str(item).upper()
            for item in (management_state.get("new_candidate_symbols") or [])
        }
        transitioned = [symbol for symbol in candidates if symbol in new_candidates]
        if transitioned:
            return True, "new_actionable_candidates:" + ",".join(transitioned)

        upgraded_candidates = {
            str(item).upper()
            for item in (management_state.get("candidate_upgrade_symbols") or [])
        }
        upgraded = [symbol for symbol in candidates if symbol in upgraded_candidates]
        if upgraded:
            return True, "candidate_quality_upgrade:" + ",".join(upgraded)

        minutes_since_last_llm = _as_float(
            management_state.get("minutes_since_last_llm")
        )
        accelerated_review_minutes = _flat_candidate_review_minutes()
        if (
            minutes_since_last_llm is not None
            and minutes_since_last_llm >= accelerated_review_minutes
        ):
            return (
                True,
                "persistent_flat_candidate_review:"
                + ",".join(candidates)
                + f":after_{accelerated_review_minutes:g}m",
            )

        if management_state.get("llm_review_due") is True:
            return True, "persistent_candidate_scheduled_review"
        return False, "persistent_candidate_review_not_due"

    return True, "actionable_candidates:" + ",".join(candidates)


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

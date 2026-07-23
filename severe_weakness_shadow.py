"""Non-operational shadow evaluation for severe tactical-signal collapse.

This module is intentionally excluded from the prompt and live close authority.
It records when an adverse tactical position would have qualified for an earlier
hypothetical exit after a first completed weak bar and a material loss in R.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Optional


POLICY_VERSION = "1.0"
MAX_CONFIRMATIONS = 2.0
MAX_CURRENT_R = -0.25
MIN_POSITION_AGE_MINUTES = 15.0
MIN_CONSECUTIVE_WEAK_BARS = 1


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def build_severe_weakness_exit_shadow(
    management_state: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Return a non-mutating shadow verdict for each adverse tactical position."""
    state = deepcopy(dict(management_state or {}))
    positions = state.get("positions") or {}
    if not isinstance(positions, Mapping):
        positions = {}

    result: Dict[str, Any] = {
        "mode": "shadow",
        "operational": False,
        "policy_version": POLICY_VERSION,
        "thresholds": {
            "maximum_confirmations": MAX_CONFIRMATIONS,
            "maximum_current_r": MAX_CURRENT_R,
            "minimum_position_age_minutes": MIN_POSITION_AGE_MINUTES,
            "minimum_consecutive_weak_bars": MIN_CONSECUTIVE_WEAK_BARS,
        },
        "evaluated_symbols": [],
        "triggered_symbols": [],
        "observations": {},
    }

    for raw_symbol, raw_position in positions.items():
        symbol = str(raw_symbol or "").upper()
        if not symbol or not isinstance(raw_position, Mapping):
            continue
        position = dict(raw_position)
        eligible_context = bool(
            str(position.get("regime") or "") == "adverse"
            and str(position.get("position_mode") or "") == "tactical"
            and str(position.get("side") or "").lower() == "long"
        )
        if not eligible_context:
            continue

        confirmations = _as_float(position.get("tactical_confirmations"))
        current_r = _as_float(position.get("current_r"))
        age_minutes = _as_float(position.get("position_age_minutes"))
        weak_bars = _as_int(position.get("consecutive_weak_bars"))
        completed_bar = position.get("current_completed_15m_bar")
        opened_at = position.get("opened_at")
        mark_price = _as_float(position.get("mark_price"))
        entry_price = _as_float(position.get("entry_price"))

        checks = {
            "confirmations_at_or_below_threshold": bool(
                confirmations is not None and confirmations <= MAX_CONFIRMATIONS
            ),
            "current_r_at_or_below_threshold": bool(
                current_r is not None and current_r <= MAX_CURRENT_R
            ),
            "minimum_age_reached": bool(
                age_minutes is not None and age_minutes >= MIN_POSITION_AGE_MINUTES
            ),
            "first_completed_weak_bar_present": bool(
                weak_bars >= MIN_CONSECUTIVE_WEAK_BARS and completed_bar is not None
            ),
            "mark_price_available": bool(mark_price is not None and mark_price > 0),
        }
        triggered = all(checks.values())
        sample_key = "|".join(
            [
                "severe-weakness-exit-shadow",
                POLICY_VERSION,
                symbol,
                str(opened_at or "unknown-open"),
                str(completed_bar or "unknown-bar"),
            ]
        )
        observation = {
            "sample_key": sample_key,
            "symbol": symbol,
            "opened_at": opened_at,
            "completed_15m_bar": completed_bar,
            "position_age_minutes": age_minutes,
            "entry_price": entry_price,
            "hypothetical_exit_price": mark_price,
            "current_r": current_r,
            "tactical_confirmations": confirmations,
            "consecutive_weak_bars": weak_bars,
            "checks": checks,
            "triggered": triggered,
            "hypothetical_action": "close" if triggered else "hold",
            "live_exit_authorized_unchanged": bool(position.get("exit_authorized")),
            "live_management_status_unchanged": position.get("management_status"),
        }
        result["evaluated_symbols"].append(symbol)
        if triggered:
            result["triggered_symbols"].append(symbol)
        result["observations"][symbol] = observation

    result["evaluated_symbols"] = sorted(set(result["evaluated_symbols"]))
    result["triggered_symbols"] = sorted(set(result["triggered_symbols"]))
    result["observation_count"] = len(result["observations"])
    result["trigger_count"] = len(result["triggered_symbols"])
    return result

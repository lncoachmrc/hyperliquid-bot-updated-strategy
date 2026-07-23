"""Last-moment deterministic revalidation for fragile adverse breakouts.

The LLM remains the final decision maker, but an OPEN based on a weak 1/3
Donchian adverse breakout must still be true at the live exchange mid immediately
before persistence/execution. This guard can only downgrade OPEN to HOLD.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping, Optional


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "1.0", "yes"}
    return False


def _strategy_for_symbol(
    indicators: Iterable[Dict[str, Any]], symbol: str
) -> Mapping[str, Any]:
    wanted = str(symbol or "").upper()
    for item in indicators:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("ticker") or "").upper() != wanted:
            continue
        strategy = item.get("strategy") or {}
        return strategy if isinstance(strategy, Mapping) else {}
    return {}


def _safe_hold(
    decision: Mapping[str, Any],
    *,
    symbol: str,
    reason: str,
    audit: Mapping[str, Any],
) -> Dict[str, Any]:
    original = deepcopy(dict(decision))
    guarded = dict(decision)
    guarded.update(
        {
            "operation": "hold",
            "symbol": symbol,
            "direction": "long",
            "target_portion_of_balance": 0.0,
            "leverage": 1,
            "stop_loss_percent": 1.0,
            "reason": reason[:300],
            "pre_trade_revalidation_adjusted": True,
            "pre_trade_revalidation": dict(audit),
            "pre_trade_original_decision": original,
        }
    )
    return guarded


def apply_live_breakout_revalidation(
    decision: Dict[str, Any],
    indicators: Iterable[Dict[str, Any]],
    live_mids: Mapping[str, Any] | None,
    *,
    live_mid_error: Optional[str] = None,
) -> Dict[str, Any]:
    """Fail closed when a weak adverse breakout has already fallen back below it.

    Only adverse ``weak_1of3`` OPEN decisions are affected. All other decisions
    and candidate classes pass through unchanged. The input decision and indicator
    payloads are not mutated.
    """
    guarded = deepcopy(decision)
    operation = str(guarded.get("operation") or "hold").lower()
    symbol = str(guarded.get("symbol") or "BTC").upper()
    guarded["symbol"] = symbol

    if operation != "open":
        return guarded

    strategy = _strategy_for_symbol(indicators, symbol)
    quality = strategy.get("adverse_entry_quality") or {}
    if not isinstance(quality, Mapping):
        quality = {}

    applicable = bool(
        str(strategy.get("regime") or "") == "adverse"
        and str(quality.get("vote_class") or "") == "weak_1of3"
        and _as_bool(quality.get("passed"))
    )
    if not applicable:
        guarded["pre_trade_revalidation"] = {
            "policy_version": "1.0",
            "mode": "deterministic_safety",
            "applicable": False,
            "passed": True,
            "symbol": symbol,
            "reason": "not_an_allowed_adverse_weak_1of3_open",
        }
        return guarded

    previous_1h_high = _as_float(quality.get("previous_1h_high"))
    mids = live_mids if isinstance(live_mids, Mapping) else {}
    live_mid = _as_float(mids.get(symbol))
    passed = bool(
        previous_1h_high is not None
        and previous_1h_high > 0
        and live_mid is not None
        and live_mid > previous_1h_high
    )
    audit = {
        "policy_version": "1.0",
        "mode": "deterministic_safety",
        "applicable": True,
        "symbol": symbol,
        "vote_class": "weak_1of3",
        "required_condition": "live_mid_strictly_above_previous_1h_high",
        "previous_1h_high": previous_1h_high,
        "live_mid": live_mid,
        "live_mid_error": live_mid_error,
        "passed": passed,
        "risk_leverage_exposure_unchanged": True,
    }
    if passed:
        guarded["pre_trade_revalidation"] = audit
        return guarded

    if live_mid is None:
        reason = (
            f"OPEN blocked by live breakout revalidation for {symbol}: "
            "current Hyperliquid mid is unavailable."
        )
    elif previous_1h_high is None or previous_1h_high <= 0:
        reason = (
            f"OPEN blocked by live breakout revalidation for {symbol}: "
            "previous 1h high is unavailable."
        )
    else:
        reason = (
            f"OPEN blocked by live breakout revalidation for {symbol}: "
            f"live mid {live_mid} is not above previous 1h high {previous_1h_high}."
        )
    audit["block_reason"] = "pre_trade_breakout_revalidation_failed"
    return _safe_hold(
        guarded,
        symbol=symbol,
        reason=reason,
        audit=audit,
    )

"""Post-LLM safety guard for position-aware, risk-budgeted decisions.

The LLM still chooses OPEN/CLOSE/HOLD. This guard enforces deterministic
constraints that execution must never violate: close hysteresis, minimum hold,
one position per coin, executable candidate status, portfolio gross exposure,
stop-risk budget and the dynamic leverage tier selected by the strategy.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping

from strategy_config import DEFAULT_STRATEGY_CONFIG, StrategyConfig


CANDIDATE_ACTIONS = {"long_candidate", "tactical_long_candidate"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "1.0", "yes"}
    return False


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 1) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


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


def _portfolio_gross_effective_exposure(
    account_status: Mapping[str, Any],
) -> float:
    balance = _as_float(account_status.get("balance_usd"))
    if balance <= 0:
        return 0.0
    gross = 0.0
    for position in account_status.get("open_positions") or []:
        size = abs(_as_float(position.get("size")))
        mark = _as_float(position.get("mark_price") or position.get("entry_price"))
        gross += size * mark / balance
    return gross


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


def _normalize_open_to_dynamic_leverage(
    guarded: Dict[str, Any],
    original: Mapping[str, Any],
    strategy: Mapping[str, Any],
    account_status: Mapping[str, Any],
    cfg: StrategyConfig,
) -> Dict[str, Any]:
    feasibility = strategy.get("execution_feasibility") or {}
    if not isinstance(feasibility, Mapping):
        return _to_safe_hold(
            guarded,
            symbol=str(guarded.get("symbol") or "BTC"),
            guard_reason="OPEN blocked because final leverage feasibility is unavailable.",
            original=original,
        )

    allowed_exposure = max(
        0.0, _as_float(feasibility.get("final_effective_exposure"))
    )
    policy_leverage = max(
        1, _as_int(feasibility.get("final_exchange_leverage"), 1)
    )
    live_max = max(1, _as_int(feasibility.get("live_max_leverage"), 1))
    technical_max = max(
        1,
        _as_int(
            feasibility.get("bot_absolute_max_leverage"),
            cfg.maximum_exchange_leverage,
        ),
    )
    policy_leverage = min(
        policy_leverage,
        live_max,
        technical_max,
        cfg.maximum_exchange_leverage,
    )

    proposed_portion = max(
        0.0, _as_float(guarded.get("target_portion_of_balance"))
    )
    proposed_leverage = max(1, _as_int(guarded.get("leverage"), 1))
    proposed_effective_exposure = proposed_portion * proposed_leverage
    if proposed_effective_exposure <= 0 or allowed_exposure <= 0:
        return _to_safe_hold(
            guarded,
            symbol=str(guarded.get("symbol") or "BTC"),
            guard_reason="OPEN blocked because proposed or allowed exposure is zero.",
            original=original,
        )

    existing_gross = _portfolio_gross_effective_exposure(account_status)
    remaining_portfolio_capacity = max(0.0, cfg.portfolio_gross_cap - existing_gross)
    safe_effective_exposure = min(
        proposed_effective_exposure,
        allowed_exposure,
        remaining_portfolio_capacity,
    )
    if safe_effective_exposure <= 0:
        return _to_safe_hold(
            guarded,
            symbol=str(guarded.get("symbol") or "BTC"),
            guard_reason="OPEN blocked because the portfolio gross-exposure cap is full.",
            original=original,
        )

    recommended_stop = max(
        0.0, _as_float(strategy.get("recommended_stop_loss_percent"))
    )
    proposed_stop = max(0.0, _as_float(guarded.get("stop_loss_percent")))
    if recommended_stop <= 0:
        return _to_safe_hold(
            guarded,
            symbol=str(guarded.get("symbol") or "BTC"),
            guard_reason="OPEN blocked because a verified stop distance is unavailable.",
            original=original,
        )
    safe_stop = min(proposed_stop, recommended_stop) if proposed_stop > 0 else recommended_stop
    estimated_risk = safe_effective_exposure * safe_stop / 100.0
    if estimated_risk > cfg.risk_per_trade + 1e-12:
        safe_effective_exposure = min(
            safe_effective_exposure,
            cfg.risk_per_trade / max(safe_stop / 100.0, 1e-12),
        )
        estimated_risk = safe_effective_exposure * safe_stop / 100.0

    safe_portion = safe_effective_exposure / policy_leverage
    if safe_portion <= 0:
        return _to_safe_hold(
            guarded,
            symbol=str(guarded.get("symbol") or "BTC"),
            guard_reason="OPEN blocked because normalized collateral portion is zero.",
            original=original,
        )

    changed = (
        policy_leverage != proposed_leverage
        or abs(safe_portion - proposed_portion) > 1e-12
        or abs(safe_stop - proposed_stop) > 1e-12
    )
    guarded["leverage"] = int(policy_leverage)
    guarded["target_portion_of_balance"] = float(safe_portion)
    guarded["stop_loss_percent"] = float(safe_stop)
    guarded["dynamic_leverage_execution"] = {
        "policy_exchange_leverage": int(policy_leverage),
        "llm_proposed_leverage": int(proposed_leverage),
        "llm_proposed_effective_exposure": float(proposed_effective_exposure),
        "maximum_strategy_effective_exposure": float(allowed_exposure),
        "existing_portfolio_gross_effective_exposure": float(existing_gross),
        "remaining_portfolio_capacity": float(remaining_portfolio_capacity),
        "final_effective_exposure": float(safe_effective_exposure),
        "final_target_portion_of_balance": float(safe_portion),
        "final_stop_loss_percent": float(safe_stop),
        "estimated_account_risk_at_stop": float(estimated_risk),
        "risk_per_trade_limit": float(cfg.risk_per_trade),
        "live_max_leverage": int(live_max),
        "technical_max_leverage": int(technical_max),
        "leverage_does_not_increase_approved_exposure": True,
    }
    if changed:
        guarded["decision_guard_adjusted"] = True
        guarded["decision_guard_reason"] = (
            "OPEN normalized to the dynamic leverage tier while preserving or "
            "reducing the LLM-proposed effective exposure and stop risk."
        )
        guarded["llm_original_decision"] = dict(original)
    return guarded


def apply_decision_guard(
    decision: Dict[str, Any],
    account_status: Dict[str, Any],
    indicators: Iterable[Dict[str, Any]],
    management_state: Dict[str, Any],
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
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
        return _normalize_open_to_dynamic_leverage(
            guarded,
            original,
            strategy,
            account_status,
            cfg,
        )

    return _to_safe_hold(
        guarded,
        symbol=preferred,
        guard_reason="Unknown operation blocked by decision safety guard.",
        original=original,
    )

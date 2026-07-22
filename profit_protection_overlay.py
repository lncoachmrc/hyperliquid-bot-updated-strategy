"""Earlier fee-aware profit protection for adverse tactical positions.

The base position manager retains its broader 1.5R/0.5R rule. This overlay can
only authorize an earlier review/close for adverse tactical trades after a
meaningful favorable excursion has been given back. It never opens a trade or
changes leverage, exposure, stop distance or account-risk limits.
"""
from __future__ import annotations

from typing import Any, Dict

from strategy_config import DEFAULT_STRATEGY_CONFIG, StrategyConfig


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def apply_adverse_profit_protection(
    management_state: Dict[str, Any],
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Mutate management state with an earlier adverse-regime give-back rule."""
    if not isinstance(management_state, dict):
        return management_state

    positions = management_state.get("positions") or {}
    if not isinstance(positions, dict):
        return management_state

    eligible = list(management_state.get("eligible_close_symbols") or [])
    immediate = list(management_state.get("immediate_llm_reasons") or [])
    triggered: list[str] = []

    for symbol, state in positions.items():
        if not isinstance(state, dict):
            continue
        adverse_tactical = bool(
            str(state.get("regime") or "") == "adverse"
            or str(state.get("position_mode") or "") == "tactical"
        )
        if not adverse_tactical:
            continue

        stop_percent = _as_float(state.get("initial_stop_loss_percent"))
        mfe_r = _as_float(state.get("maximum_favorable_excursion_r"))
        current_r = _as_float(state.get("current_r"))
        fee_adjusted_floor_r = cfg.adverse_profit_protection_floor_r
        if stop_percent is not None and stop_percent > 0:
            fee_adjusted_floor_r = max(
                fee_adjusted_floor_r,
                cfg.adverse_estimated_round_trip_cost_pct / stop_percent,
            )

        state["adverse_profit_protection"] = {
            "policy_version": "1.0",
            "trigger_r": cfg.adverse_profit_protection_trigger_r,
            "base_floor_r": cfg.adverse_profit_protection_floor_r,
            "estimated_round_trip_cost_pct": cfg.adverse_estimated_round_trip_cost_pct,
            "fee_adjusted_floor_r": fee_adjusted_floor_r,
            "mfe_r": mfe_r,
            "current_r": current_r,
            "eligible": mfe_r is not None and current_r is not None,
            "triggered": False,
        }

        already_hard_exit = bool(state.get("hard_invalidations"))
        trigger = bool(
            not already_hard_exit
            and mfe_r is not None
            and current_r is not None
            and mfe_r >= cfg.adverse_profit_protection_trigger_r
            and current_r <= fee_adjusted_floor_r
        )
        state["adverse_profit_protection"]["triggered"] = trigger
        if not trigger:
            continue

        state["profit_protection_exit_ready"] = True
        state["exit_authorized"] = True
        state["management_status"] = "adverse_profit_protection_exit"
        state["profit_protection_trigger_r"] = cfg.adverse_profit_protection_trigger_r
        state["profit_protection_floor_r"] = fee_adjusted_floor_r
        state["profit_protection_policy"] = "adverse_early_fee_adjusted"
        if symbol not in eligible:
            eligible.append(symbol)
        reason = f"{symbol}:adverse_profit_protection_exit"
        if reason not in immediate:
            immediate.append(reason)
        triggered.append(symbol)

    management_state["eligible_close_symbols"] = eligible
    management_state["immediate_llm_reasons"] = immediate
    management_state["adverse_profit_protection_triggered_symbols"] = triggered
    rules = management_state.get("rules") or {}
    if isinstance(rules, dict):
        rules.update(
            {
                "adverse_profit_protection_trigger_r": cfg.adverse_profit_protection_trigger_r,
                "adverse_profit_protection_base_floor_r": cfg.adverse_profit_protection_floor_r,
                "adverse_estimated_round_trip_cost_pct": cfg.adverse_estimated_round_trip_cost_pct,
            }
        )
        management_state["rules"] = rules
    return management_state

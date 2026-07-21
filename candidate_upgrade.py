"""Detect material quality upgrades in persistent executable candidates.

The LLM cadence intentionally skips repetitive candidate reviews. This module
restores immediacy when a still-flat symbol materially improves: stronger 15m
confirmation count, higher Donchian vote count, a higher dynamic-leverage tier,
or a material increase in strategy-approved effective exposure.

It never opens/closes a position and never changes risk limits. It only annotates
the existing management state so the decision gate can decide that an immediate
LLM review is justified.
"""
from __future__ import annotations

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
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _strategy(entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, Mapping):
        return {}
    strategy = entry.get("strategy") or {}
    return dict(strategy) if isinstance(strategy, Mapping) else {}


def _actionable(strategy: Mapping[str, Any]) -> bool:
    action = str(strategy.get("recommended_action") or "")
    feasible_raw = strategy.get("execution_feasible")
    feasible = True if feasible_raw is None else _as_bool(feasible_raw)
    return action in CANDIDATE_ACTIONS and feasible


def _candidate_metrics(strategy: Mapping[str, Any]) -> Dict[str, Any]:
    tactical = strategy.get("tactical_intraday") or {}
    feasibility = strategy.get("execution_feasibility") or {}
    dynamic = strategy.get("final_dynamic_leverage") or {}

    if not isinstance(tactical, Mapping):
        tactical = {}
    if not isinstance(feasibility, Mapping):
        feasibility = {}
    if not isinstance(dynamic, Mapping):
        dynamic = {}

    leverage = _as_int(
        feasibility.get("final_exchange_leverage"),
        _as_int(dynamic.get("exchange_leverage"), 1),
    )
    exposure = _as_float(
        feasibility.get("final_effective_exposure"),
        _as_float(dynamic.get("represented_effective_exposure"), 0.0),
    )

    return {
        "confirmations": _as_int(tactical.get("confirmations"), 0),
        "donchian_positive_votes": _as_int(
            strategy.get("donchian_positive_votes"), 0
        ),
        "exchange_leverage": max(1, leverage),
        "effective_exposure": max(0.0, exposure),
        "quality": str(
            (strategy.get("tactical_risk_profile") or {}).get("quality")
            if isinstance(strategy.get("tactical_risk_profile") or {}, Mapping)
            else ""
        ),
    }


def annotate_candidate_quality_upgrades(
    indicators: Iterable[Dict[str, Any]],
    account_status: Mapping[str, Any],
    history_context: Mapping[str, Any],
    management_state: Dict[str, Any],
    *,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Annotate material persistent-candidate improvements for immediate review."""
    open_symbols = {
        str(position.get("symbol") or "").upper()
        for position in (account_status.get("open_positions") or [])
        if isinstance(position, Mapping) and position.get("symbol")
    }
    blocked_symbols = {
        str(symbol).upper()
        for symbol in (management_state.get("reentry_blocked_symbols") or [])
    }
    history_by_symbol = history_context.get("history_by_symbol") or {}

    upgraded_symbols: list[str] = []
    upgrade_state: Dict[str, Any] = {}
    immediate_reasons = list(management_state.get("immediate_llm_reasons") or [])

    for item in indicators:
        if not isinstance(item, Mapping) or not item.get("ticker"):
            continue
        symbol = str(item.get("ticker") or "").upper()
        if not symbol or symbol in open_symbols or symbol in blocked_symbols:
            continue

        current_strategy_raw = item.get("strategy") or {}
        if not isinstance(current_strategy_raw, Mapping):
            continue
        current_strategy = dict(current_strategy_raw)
        if not _actionable(current_strategy):
            continue

        previous_entries = history_by_symbol.get(symbol) or []
        previous_strategy = _strategy(previous_entries[0]) if previous_entries else {}
        # New candidates are already handled by position_management.new_candidate_symbols.
        if not _actionable(previous_strategy):
            continue

        current = _candidate_metrics(current_strategy)
        previous = _candidate_metrics(previous_strategy)
        reasons: list[str] = []

        confirmation_gain = current["confirmations"] - previous["confirmations"]
        if (
            current["confirmations"] >= cfg.candidate_upgrade_min_confirmations
            and confirmation_gain >= cfg.candidate_upgrade_min_confirmation_gain
        ):
            reasons.append(
                f"confirmations:{previous['confirmations']}->{current['confirmations']}"
            )

        if current["donchian_positive_votes"] > previous["donchian_positive_votes"]:
            reasons.append(
                "donchian_votes:"
                f"{previous['donchian_positive_votes']}->{current['donchian_positive_votes']}"
            )

        if current["exchange_leverage"] > previous["exchange_leverage"]:
            reasons.append(
                f"leverage_tier:{previous['exchange_leverage']}x->{current['exchange_leverage']}x"
            )

        previous_exposure = previous["effective_exposure"]
        current_exposure = current["effective_exposure"]
        exposure_increase_fraction = 0.0
        if previous_exposure > 0:
            exposure_increase_fraction = current_exposure / previous_exposure - 1.0
            if (
                exposure_increase_fraction
                >= cfg.candidate_upgrade_effective_exposure_increase_fraction
            ):
                reasons.append(
                    "effective_exposure:"
                    f"{previous_exposure:.6f}->{current_exposure:.6f}"
                )

        if not reasons:
            continue

        upgraded_symbols.append(symbol)
        upgrade_state[symbol] = {
            "reasons": reasons,
            "previous": previous,
            "current": current,
            "effective_exposure_increase_fraction": exposure_increase_fraction,
            "immediate_llm_review": True,
        }
        immediate_reasons.append(
            f"{symbol}:candidate_quality_upgrade[{'|'.join(reasons)}]"
        )

    management_state["candidate_upgrade_symbols"] = upgraded_symbols
    management_state["candidate_upgrade_state_by_symbol"] = upgrade_state
    management_state["immediate_llm_reasons"] = immediate_reasons
    return management_state

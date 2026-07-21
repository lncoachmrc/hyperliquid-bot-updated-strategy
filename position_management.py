"""Stateful position-management policy backed by the existing PostgreSQL audit.

The LLM remains the final OPEN/CLOSE/HOLD decision maker when a review is due.
This module supplies deterministic safety boundaries around that decision:
minimum holding time, tactical exit hysteresis, hard-invalidation bypasses and
stable-position review cadence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

import db_utils
from strategy_config import DEFAULT_STRATEGY_CONFIG, StrategyConfig


CANDIDATE_ACTIONS = {"long_candidate", "tactical_long_candidate"}


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


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_management_history(
    symbols: Iterable[str],
    open_symbols: Iterable[str],
    *,
    history_limit: int = 3,
) -> Dict[str, Any]:
    """Load only the compact state required by the current decision cycle."""
    symbol_list = sorted({str(symbol).upper() for symbol in symbols if symbol})
    open_list = sorted({str(symbol).upper() for symbol in open_symbols if symbol})
    context: Dict[str, Any] = {
        "history_by_symbol": {symbol: [] for symbol in symbol_list},
        "opened_at_by_symbol": {},
        "last_llm_at": None,
    }

    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT created_at
                FROM bot_operations
                WHERE raw_payload->>'decision_source' = 'llm'
                ORDER BY created_at DESC
                LIMIT 1;
                """
            )
            row = cursor.fetchone()
            if row:
                context["last_llm_at"] = row[0]

            for symbol in symbol_list:
                cursor.execute(
                    """
                    SELECT a.created_at, i.strategy
                    FROM indicators_contexts i
                    JOIN ai_contexts a ON a.id = i.context_id
                    WHERE i.ticker = %s AND i.strategy IS NOT NULL
                    ORDER BY a.created_at DESC
                    LIMIT %s;
                    """,
                    (symbol, history_limit),
                )
                context["history_by_symbol"][symbol] = [
                    {"created_at": row[0], "strategy": row[1] or {}}
                    for row in cursor.fetchall()
                ]

            for symbol in open_list:
                cursor.execute(
                    """
                    SELECT bo.created_at
                    FROM bot_operations bo
                    JOIN execution_results er ON er.operation_id = bo.id
                    WHERE bo.symbol = %s
                      AND bo.operation = 'open'
                      AND er.execution_status = 'success'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM bot_operations bo2
                          JOIN execution_results er2 ON er2.operation_id = bo2.id
                          WHERE bo2.symbol = bo.symbol
                            AND bo2.operation = 'close'
                            AND er2.execution_status = 'success'
                            AND bo2.created_at > bo.created_at
                      )
                    ORDER BY bo.created_at DESC
                    LIMIT 1;
                    """,
                    (symbol,),
                )
                row = cursor.fetchone()
                if row:
                    context["opened_at_by_symbol"][symbol] = row[0]

    return context


def _strategy_for_symbol(
    indicator_map: Mapping[str, Dict[str, Any]], symbol: str
) -> Dict[str, Any]:
    item = indicator_map.get(symbol) or {}
    strategy = item.get("strategy") or {}
    return strategy if isinstance(strategy, dict) else {}


def _history_strategy(entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    strategy = entry.get("strategy") or {}
    return strategy if isinstance(strategy, dict) else {}


def _confirmation_count(strategy: Mapping[str, Any]) -> Optional[float]:
    tactical = strategy.get("tactical_intraday") or {}
    if not isinstance(tactical, dict):
        return None
    return _as_float(tactical.get("confirmations"))


def _candidate(strategy: Mapping[str, Any]) -> bool:
    tactical = strategy.get("tactical_intraday") or {}
    if not isinstance(tactical, dict):
        return False
    return _as_bool(tactical.get("candidate"))


def build_position_management_state(
    indicators: Iterable[Dict[str, Any]],
    account_status: Dict[str, Any],
    history_context: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Derive review cadence and per-position close eligibility."""
    current_time = _as_utc(now) or datetime.now(timezone.utc)
    indicator_items = [item for item in indicators if isinstance(item, dict)]
    indicator_map = {
        str(item.get("ticker") or "").upper(): item
        for item in indicator_items
        if item.get("ticker")
    }
    positions = account_status.get("open_positions") or []
    open_symbols = [
        str(position.get("symbol") or "").upper()
        for position in positions
        if position.get("symbol")
    ]
    open_set = set(open_symbols)

    history_by_symbol = history_context.get("history_by_symbol") or {}
    opened_at_by_symbol = history_context.get("opened_at_by_symbol") or {}
    position_states: Dict[str, Any] = {}
    immediate_reasons: List[str] = []
    eligible_close_symbols: List[str] = []

    for position in positions:
        symbol = str(position.get("symbol") or "").upper()
        if not symbol:
            continue
        strategy = _strategy_for_symbol(indicator_map, symbol)
        history = history_by_symbol.get(symbol) or []
        confirmations = _confirmation_count(strategy)
        tactical_candidate = _candidate(strategy)
        invalidations = [str(item) for item in (strategy.get("invalidations") or [])]
        hard_invalidations = bool(invalidations)
        regime = str(strategy.get("regime") or "unknown")
        recommended_action = str(strategy.get("recommended_action") or "unknown")
        is_tactical_context = regime == "adverse" or bool(
            strategy.get("tactical_intraday")
        )

        opened_at = _as_utc(opened_at_by_symbol.get(symbol))
        age_minutes = (
            max(0.0, (current_time - opened_at).total_seconds() / 60.0)
            if opened_at is not None
            else None
        )
        minimum_hold_met = (
            True
            if age_minutes is None
            else age_minutes >= cfg.minimum_position_hold_minutes
        )

        current_weak = (
            confirmations is not None
            and confirmations <= cfg.tactical_exit_confirmations
        )
        consecutive_weak_cycles = 1 if current_weak else 0
        if current_weak:
            for entry in history:
                previous = _history_strategy(entry)
                previous_confirmations = _confirmation_count(previous)
                if (
                    previous_confirmations is not None
                    and previous_confirmations <= cfg.tactical_exit_confirmations
                ):
                    consecutive_weak_cycles += 1
                else:
                    break

        tactical_exit_ready = bool(
            is_tactical_context
            and minimum_hold_met
            and consecutive_weak_cycles >= cfg.tactical_exit_consecutive_cycles
        )
        daily_exit_ready = bool(
            not is_tactical_context
            and minimum_hold_met
            and recommended_action == "close_if_open_otherwise_hold"
        )
        exit_authorized = bool(
            hard_invalidations or tactical_exit_ready or daily_exit_ready
        )

        if hard_invalidations:
            management_status = "hard_exit"
        elif exit_authorized:
            management_status = "exit_ready"
        elif not minimum_hold_met and current_weak:
            management_status = "minimum_hold_protection"
        elif confirmations is not None and confirmations <= cfg.tactical_warning_confirmations:
            management_status = "warning"
        elif tactical_candidate or recommended_action == "long_candidate":
            management_status = "stable"
        else:
            management_status = "monitor"

        if hard_invalidations:
            immediate_reasons.append(f"{symbol}:hard_invalidation")
        elif exit_authorized:
            immediate_reasons.append(f"{symbol}:exit_hysteresis_confirmed")
        if exit_authorized:
            eligible_close_symbols.append(symbol)

        position_states[symbol] = {
            "symbol": symbol,
            "side": position.get("side"),
            "pnl_usd": position.get("pnl_usd"),
            "opened_at": opened_at.isoformat() if opened_at else None,
            "position_age_minutes": age_minutes,
            "minimum_hold_minutes": cfg.minimum_position_hold_minutes,
            "minimum_hold_met": minimum_hold_met,
            "recommended_action": recommended_action,
            "regime": regime,
            "tactical_candidate": tactical_candidate,
            "tactical_confirmations": confirmations,
            "warning_at_confirmations": cfg.tactical_warning_confirmations,
            "exit_at_or_below_confirmations": cfg.tactical_exit_confirmations,
            "required_consecutive_weak_cycles": cfg.tactical_exit_consecutive_cycles,
            "consecutive_weak_cycles": consecutive_weak_cycles,
            "hard_invalidations": invalidations,
            "exit_authorized": exit_authorized,
            "management_status": management_status,
        }

    new_candidate_symbols: List[str] = []
    for symbol, item in indicator_map.items():
        if symbol in open_set:
            continue
        strategy = item.get("strategy") or {}
        action = strategy.get("recommended_action")
        feasible = strategy.get("execution_feasible")
        is_feasible = True if feasible is None else _as_bool(feasible)
        if action not in CANDIDATE_ACTIONS or not is_feasible:
            continue
        previous_entries = history_by_symbol.get(symbol) or []
        previous_strategy = (
            _history_strategy(previous_entries[0]) if previous_entries else {}
        )
        previous_action = previous_strategy.get("recommended_action")
        previous_feasible_raw = previous_strategy.get("execution_feasible")
        previous_feasible = (
            True
            if previous_feasible_raw is None
            else _as_bool(previous_feasible_raw)
        )
        if previous_action not in CANDIDATE_ACTIONS or not previous_feasible:
            new_candidate_symbols.append(symbol)
            immediate_reasons.append(f"{symbol}:new_executable_candidate")

    last_llm_at = _as_utc(history_context.get("last_llm_at"))
    minutes_since_last_llm = (
        max(0.0, (current_time - last_llm_at).total_seconds() / 60.0)
        if last_llm_at is not None
        else None
    )
    llm_review_due = bool(
        last_llm_at is None
        or minutes_since_last_llm >= cfg.stable_position_llm_review_minutes
    )

    def _hold_priority(symbol: str) -> tuple:
        state = position_states.get(symbol) or {}
        status_rank = {
            "hard_exit": 0,
            "exit_ready": 1,
            "warning": 2,
            "minimum_hold_protection": 3,
            "monitor": 4,
            "stable": 5,
        }.get(str(state.get("management_status")), 6)
        confirmations = state.get("tactical_confirmations")
        confirmation_rank = float(confirmations) if confirmations is not None else 99.0
        pnl = _as_float(state.get("pnl_usd"))
        pnl_rank = pnl if pnl is not None else 0.0
        return status_rank, confirmation_rank, pnl_rank, symbol

    preferred_hold_symbol = (
        sorted(open_symbols, key=_hold_priority)[0] if open_symbols else "BTC"
    )

    return {
        "policy_version": "1.0",
        "generated_at": current_time.isoformat(),
        "open_symbols": open_symbols,
        "positions": position_states,
        "eligible_close_symbols": eligible_close_symbols,
        "new_candidate_symbols": new_candidate_symbols,
        "immediate_llm_reasons": immediate_reasons,
        "last_llm_at": last_llm_at.isoformat() if last_llm_at else None,
        "minutes_since_last_llm": minutes_since_last_llm,
        "stable_review_interval_minutes": cfg.stable_position_llm_review_minutes,
        "llm_review_due": llm_review_due,
        "preferred_hold_symbol": preferred_hold_symbol,
        "rules": {
            "entry_confirmations": cfg.tactical_min_confirmations,
            "warning_confirmations": cfg.tactical_warning_confirmations,
            "exit_confirmations": cfg.tactical_exit_confirmations,
            "exit_consecutive_cycles": cfg.tactical_exit_consecutive_cycles,
            "minimum_hold_minutes": cfg.minimum_position_hold_minutes,
            "hard_invalidations_bypass_hysteresis": True,
        },
    }

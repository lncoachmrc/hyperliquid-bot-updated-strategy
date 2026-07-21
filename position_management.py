"""Stateful position-management policy backed by the existing PostgreSQL audit.

The LLM remains the final OPEN/CLOSE/HOLD decision maker when a review is due.
This module supplies deterministic safety boundaries around that decision:
unique-completed-bar exit hysteresis, minimum holding time, post-close re-entry
cooldown, profit give-back protection and stable-position review cadence.
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
    history_limit: int = 8,
) -> Dict[str, Any]:
    """Load the compact persistent state required by the current decision cycle."""
    symbol_list = sorted({str(symbol).upper() for symbol in symbols if symbol})
    open_list = sorted({str(symbol).upper() for symbol in open_symbols if symbol})
    context: Dict[str, Any] = {
        "history_by_symbol": {symbol: [] for symbol in symbol_list},
        "opened_at_by_symbol": {},
        "open_stop_loss_percent_by_symbol": {},
        "max_observed_price_by_symbol": {},
        "last_close_at_by_symbol": {},
        "last_close_price_by_symbol": {},
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

                cursor.execute(
                    """
                    SELECT bo.created_at, er.avg_price
                    FROM bot_operations bo
                    JOIN execution_results er ON er.operation_id = bo.id
                    WHERE bo.symbol = %s
                      AND bo.operation = 'close'
                      AND er.execution_status = 'success'
                    ORDER BY bo.created_at DESC
                    LIMIT 1;
                    """,
                    (symbol,),
                )
                close_row = cursor.fetchone()
                if close_row:
                    context["last_close_at_by_symbol"][symbol] = close_row[0]
                    if close_row[1] is not None:
                        context["last_close_price_by_symbol"][symbol] = float(close_row[1])

            for symbol in open_list:
                cursor.execute(
                    """
                    SELECT bo.created_at, bo.stop_loss_percent
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
                    opened_at = row[0]
                    context["opened_at_by_symbol"][symbol] = opened_at
                    if row[1] is not None:
                        context["open_stop_loss_percent_by_symbol"][symbol] = float(row[1])

                    cursor.execute(
                        """
                        SELECT MAX(i.price)
                        FROM indicators_contexts i
                        JOIN ai_contexts a ON a.id = i.context_id
                        WHERE i.ticker = %s
                          AND a.created_at >= %s
                          AND i.price IS NOT NULL;
                        """,
                        (symbol, opened_at),
                    )
                    price_row = cursor.fetchone()
                    if price_row and price_row[0] is not None:
                        context["max_observed_price_by_symbol"][symbol] = float(price_row[0])

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


def _tactical(strategy: Mapping[str, Any]) -> Dict[str, Any]:
    tactical = strategy.get("tactical_intraday") or {}
    return tactical if isinstance(tactical, dict) else {}


def _confirmation_count(strategy: Mapping[str, Any]) -> Optional[float]:
    return _as_float(_tactical(strategy).get("confirmations"))


def _candidate(strategy: Mapping[str, Any]) -> bool:
    return _as_bool(_tactical(strategy).get("candidate"))


def _completed_bar_id(strategy: Mapping[str, Any]) -> Optional[str]:
    tactical = _tactical(strategy)
    value = tactical.get("completed_bar_open_time") or tactical.get("completed_bar_close_time")
    if value is None:
        return None
    return str(value)


def _reentry_breakout_override(
    strategy: Mapping[str, Any], cfg: StrategyConfig
) -> bool:
    tactical = _tactical(strategy)
    confirmations = _as_float(tactical.get("confirmations")) or 0.0
    volume_ratio = _as_float(tactical.get("volume_ratio")) or 0.0
    breakout = _as_bool(tactical.get("breakout_above_previous_1h_high"))
    return bool(
        confirmations >= cfg.reentry_breakout_override_confirmations
        and volume_ratio >= cfg.reentry_breakout_override_volume_ratio
        and breakout
    )


def build_position_management_state(
    indicators: Iterable[Dict[str, Any]],
    account_status: Dict[str, Any],
    history_context: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Derive review cadence, close eligibility and post-close entry constraints."""
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
    open_stop_by_symbol = history_context.get("open_stop_loss_percent_by_symbol") or {}
    max_observed_by_symbol = history_context.get("max_observed_price_by_symbol") or {}
    last_close_at_by_symbol = history_context.get("last_close_at_by_symbol") or {}
    last_close_price_by_symbol = history_context.get("last_close_price_by_symbol") or {}

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
        is_tactical_context = bool(
            regime == "adverse"
            or recommended_action == "tactical_long_candidate"
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

        # Tactical weakness is counted by DISTINCT completed 15m bars. Repeated
        # 10-minute worker cycles reading the same bar can never advance hysteresis.
        current_weak = (
            confirmations is not None
            and confirmations <= cfg.tactical_exit_confirmations
        )
        current_bar_id = _completed_bar_id(strategy)
        consecutive_weak_bars = 1 if current_weak else 0
        seen_bar_ids = {current_bar_id} if current_weak and current_bar_id else set()
        if current_weak and current_bar_id is not None:
            for entry in history:
                entry_time = _as_utc(entry.get("created_at")) if isinstance(entry, dict) else None
                if opened_at is not None and entry_time is not None and entry_time < opened_at:
                    break
                previous = _history_strategy(entry)
                previous_bar_id = _completed_bar_id(previous)
                if previous_bar_id is None:
                    # Without a bar identity we cannot prove that this is a new
                    # completed candle, so fail closed against a premature exit.
                    break
                if previous_bar_id in seen_bar_ids:
                    continue
                previous_confirmations = _confirmation_count(previous)
                if (
                    previous_confirmations is not None
                    and previous_confirmations <= cfg.tactical_exit_confirmations
                ):
                    seen_bar_ids.add(previous_bar_id)
                    consecutive_weak_bars += 1
                else:
                    break

        tactical_exit_ready = bool(
            is_tactical_context
            and minimum_hold_met
            and consecutive_weak_bars >= cfg.tactical_exit_consecutive_cycles
        )
        daily_exit_ready = bool(
            not is_tactical_context
            and minimum_hold_met
            and recommended_action == "close_if_open_otherwise_hold"
        )

        # Profit give-back protection. It does not move exchange stops; it simply
        # makes the position immediately eligible for LLM review/close once a
        # material >=1.5R favorable move has retraced to <=0.5R.
        side = str(position.get("side") or "").lower()
        entry_price = _as_float(position.get("entry_price"))
        mark_price = _as_float(position.get("mark_price"))
        initial_stop_percent = _as_float(open_stop_by_symbol.get(symbol))
        tactical_bar_high = _as_float(_tactical(strategy).get("bar_high"))
        max_observed_price = _as_float(max_observed_by_symbol.get(symbol))
        mfe_price = None
        mfe_r = None
        current_r = None
        profit_protection_ready = False
        if (
            side == "long"
            and entry_price is not None
            and entry_price > 0
            and mark_price is not None
            and initial_stop_percent is not None
            and initial_stop_percent > 0
        ):
            observed_prices = [entry_price, mark_price]
            if max_observed_price is not None:
                observed_prices.append(max_observed_price)
            if tactical_bar_high is not None:
                observed_prices.append(tactical_bar_high)
            mfe_price = max(observed_prices)
            initial_risk_per_unit = entry_price * initial_stop_percent / 100.0
            if initial_risk_per_unit > 0:
                mfe_r = (mfe_price - entry_price) / initial_risk_per_unit
                current_r = (mark_price - entry_price) / initial_risk_per_unit
                profit_protection_ready = bool(
                    mfe_r >= cfg.profit_protection_trigger_r
                    and current_r <= cfg.profit_protection_floor_r
                )

        exit_authorized = bool(
            hard_invalidations
            or profit_protection_ready
            or tactical_exit_ready
            or daily_exit_ready
        )

        if hard_invalidations:
            management_status = "hard_exit"
        elif profit_protection_ready:
            management_status = "profit_protection_exit"
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
        elif profit_protection_ready:
            immediate_reasons.append(f"{symbol}:profit_protection_exit")
        elif exit_authorized:
            immediate_reasons.append(f"{symbol}:exit_hysteresis_confirmed")
        if exit_authorized:
            eligible_close_symbols.append(symbol)

        position_states[symbol] = {
            "symbol": symbol,
            "side": position.get("side"),
            "pnl_usd": position.get("pnl_usd"),
            "entry_price": entry_price,
            "mark_price": mark_price,
            "opened_at": opened_at.isoformat() if opened_at else None,
            "position_age_minutes": age_minutes,
            "minimum_hold_minutes": cfg.minimum_position_hold_minutes,
            "minimum_hold_met": minimum_hold_met,
            "recommended_action": recommended_action,
            "regime": regime,
            "position_mode": "tactical" if is_tactical_context else "daily",
            "tactical_candidate": tactical_candidate,
            "tactical_confirmations": confirmations,
            "current_completed_15m_bar": current_bar_id,
            "warning_at_confirmations": cfg.tactical_warning_confirmations,
            "exit_at_or_below_confirmations": cfg.tactical_exit_confirmations,
            "required_consecutive_weak_bars": cfg.tactical_exit_consecutive_cycles,
            "consecutive_weak_bars": consecutive_weak_bars,
            "hard_invalidations": invalidations,
            "initial_stop_loss_percent": initial_stop_percent,
            "maximum_favorable_price_observed": mfe_price,
            "maximum_favorable_excursion_r": mfe_r,
            "current_r": current_r,
            "profit_protection_trigger_r": cfg.profit_protection_trigger_r,
            "profit_protection_floor_r": cfg.profit_protection_floor_r,
            "profit_protection_exit_ready": profit_protection_ready,
            "exit_authorized": exit_authorized,
            "management_status": management_status,
        }

    new_candidate_symbols: List[str] = []
    reentry_blocked_symbols: List[str] = []
    reentry_override_symbols: List[str] = []
    reentry_state_by_symbol: Dict[str, Any] = {}

    for symbol, item in indicator_map.items():
        if symbol in open_set:
            continue
        strategy = item.get("strategy") or {}
        action = strategy.get("recommended_action")
        feasible = strategy.get("execution_feasible")
        is_feasible = True if feasible is None else _as_bool(feasible)
        if action not in CANDIDATE_ACTIONS or not is_feasible:
            continue

        last_close_at = _as_utc(last_close_at_by_symbol.get(symbol))
        minutes_since_close = (
            max(0.0, (current_time - last_close_at).total_seconds() / 60.0)
            if last_close_at is not None
            else None
        )
        cooldown_active = bool(
            minutes_since_close is not None
            and minutes_since_close < cfg.post_close_reentry_cooldown_minutes
        )
        breakout_override = bool(
            cooldown_active and _reentry_breakout_override(strategy, cfg)
        )
        reentry_state_by_symbol[symbol] = {
            "last_close_at": last_close_at.isoformat() if last_close_at else None,
            "last_close_price": _as_float(last_close_price_by_symbol.get(symbol)),
            "minutes_since_close": minutes_since_close,
            "cooldown_minutes": cfg.post_close_reentry_cooldown_minutes,
            "cooldown_active": cooldown_active,
            "breakout_override": breakout_override,
        }
        if cooldown_active and not breakout_override:
            reentry_blocked_symbols.append(symbol)
            continue
        if breakout_override:
            reentry_override_symbols.append(symbol)

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
        if previous_action not in CANDIDATE_ACTIONS or not previous_feasible or breakout_override:
            new_candidate_symbols.append(symbol)
            reason = (
                f"{symbol}:breakout_reentry_override"
                if breakout_override
                else f"{symbol}:new_executable_candidate"
            )
            immediate_reasons.append(reason)

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
            "profit_protection_exit": 1,
            "exit_ready": 2,
            "warning": 3,
            "minimum_hold_protection": 4,
            "monitor": 5,
            "stable": 6,
        }.get(str(state.get("management_status")), 7)
        confirmations = state.get("tactical_confirmations")
        confirmation_rank = float(confirmations) if confirmations is not None else 99.0
        pnl = _as_float(state.get("pnl_usd"))
        pnl_rank = pnl if pnl is not None else 0.0
        return status_rank, confirmation_rank, pnl_rank, symbol

    preferred_hold_symbol = (
        sorted(open_symbols, key=_hold_priority)[0] if open_symbols else "BTC"
    )

    return {
        "policy_version": "1.1",
        "generated_at": current_time.isoformat(),
        "open_symbols": open_symbols,
        "positions": position_states,
        "eligible_close_symbols": eligible_close_symbols,
        "new_candidate_symbols": new_candidate_symbols,
        "reentry_blocked_symbols": reentry_blocked_symbols,
        "reentry_override_symbols": reentry_override_symbols,
        "reentry_state_by_symbol": reentry_state_by_symbol,
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
            "exit_consecutive_distinct_15m_bars": cfg.tactical_exit_consecutive_cycles,
            "minimum_hold_minutes": cfg.minimum_position_hold_minutes,
            "post_close_reentry_cooldown_minutes": cfg.post_close_reentry_cooldown_minutes,
            "breakout_override_confirmations": cfg.reentry_breakout_override_confirmations,
            "breakout_override_volume_ratio": cfg.reentry_breakout_override_volume_ratio,
            "profit_protection_trigger_r": cfg.profit_protection_trigger_r,
            "profit_protection_floor_r": cfg.profit_protection_floor_r,
            "hard_invalidations_bypass_hysteresis": True,
        },
    }

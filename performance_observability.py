"""Non-operational performance observability for entry filters and external closes.

This module writes audit-only records. It never creates or changes a trading
decision, leverage, exposure, stop, order, or position.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping, Optional

from psycopg2.extras import Json

import db_utils


ENTRY_OBSERVATION_POLICY_VERSION = "1.0"
ENTRY_HORIZONS_MINUTES = (15, 60, 180)
EXTERNAL_CLOSE_LOOKBACK_MINUTES = 30
EXTERNAL_CLOSE_LOOKAHEAD_MINUTES = 5
EXTERNAL_CLOSE_RETRY_HOURS = 6


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entry_opportunity_samples (
    id BIGSERIAL PRIMARY KEY,
    sample_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    strategy_version TEXT,
    entry_policy_version TEXT NOT NULL,
    regime TEXT,
    completed_15m_bar_close_time TEXT,
    policy_outcome TEXT NOT NULL,
    baseline_price NUMERIC(30, 10) NOT NULL,
    block_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    hypothetical_stop_loss_percent NUMERIC(10, 4),
    hypothetical_effective_exposure NUMERIC(20, 10),
    quality_snapshot JSONB NOT NULL,
    strategy_snapshot JSONB NOT NULL,
    target_15m_at TIMESTAMPTZ NOT NULL,
    target_60m_at TIMESTAMPTZ NOT NULL,
    target_180m_at TIMESTAMPTZ NOT NULL,
    actual_15m_at TIMESTAMPTZ,
    actual_15m_price NUMERIC(30, 10),
    actual_60m_at TIMESTAMPTZ,
    actual_60m_price NUMERIC(30, 10),
    actual_180m_at TIMESTAMPTZ,
    actual_180m_price NUMERIC(30, 10),
    max_price_180m NUMERIC(30, 10) NOT NULL,
    min_price_180m NUMERIC(30, 10) NOT NULL,
    last_observed_at TIMESTAMPTZ NOT NULL,
    first_bot_operation_id BIGINT REFERENCES bot_operations(id) ON DELETE SET NULL,
    executed_open_operation_id BIGINT REFERENCES bot_operations(id) ON DELETE SET NULL,
    first_live_operation TEXT,
    last_live_operation TEXT,
    last_live_decision_source TEXT,
    completed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_entry_opportunity_samples_symbol_observed
    ON entry_opportunity_samples(symbol, observed_at);
CREATE INDEX IF NOT EXISTS idx_entry_opportunity_samples_completion
    ON entry_opportunity_samples(completed, target_180m_at);

CREATE TABLE IF NOT EXISTS external_close_reconciliations (
    id BIGSERIAL PRIMARY KEY,
    detected_operation_id BIGINT NOT NULL UNIQUE
        REFERENCES bot_operations(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    detected_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    expected_position_side TEXT,
    expected_size NUMERIC(30, 10),
    open_operation_id BIGINT REFERENCES bot_operations(id) ON DELETE SET NULL,
    expected_stop_order_id TEXT,
    window_start_at TIMESTAMPTZ NOT NULL,
    window_end_at TIMESTAMPTZ NOT NULL,
    reconciliation_status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    matched_at TIMESTAMPTZ,
    match_method TEXT,
    total_filled_size NUMERIC(30, 10),
    avg_fill_price NUMERIC(30, 10),
    total_fee NUMERIC(30, 10),
    fee_token TEXT,
    total_closed_pnl NUMERIC(30, 10),
    first_fill_at TIMESTAMPTZ,
    last_fill_at TIMESTAMPTZ,
    order_ids JSONB,
    fill_hashes JSONB,
    raw_fills JSONB,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_external_close_reconciliations_status
    ON external_close_reconciliations(reconciliation_status, detected_at);
"""


def ensure_performance_observability_schema() -> None:
    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
        connection.commit()


def _as_utc(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"Unsupported datetime value: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _current_price(indicator: Mapping[str, Any]) -> Optional[float]:
    current = indicator.get("current") or {}
    if isinstance(current, Mapping):
        value = _as_float(current.get("price"))
        if value is not None and value > 0:
            return value
    strategy = indicator.get("strategy") or {}
    tactical = strategy.get("tactical_intraday") if isinstance(strategy, Mapping) else {}
    if isinstance(tactical, Mapping):
        value = _as_float(tactical.get("price"))
        if value is not None and value > 0:
            return value
    return None


def build_entry_opportunity_samples(
    indicators: Iterable[Dict[str, Any]],
    entry_quality_summary: Mapping[str, Any],
    *,
    observed_at: datetime | str | None = None,
) -> list[Dict[str, Any]]:
    """Build one non-mutating sample per symbol/completed 15m bar."""
    observation_time = _as_utc(observed_at)
    indicator_map = {
        str(item.get("ticker") or "").upper(): item
        for item in indicators
        if isinstance(item, dict) and item.get("ticker")
    }
    evaluated = entry_quality_summary.get("evaluated") or {}
    if not isinstance(evaluated, Mapping):
        return []

    policy_version = str(
        entry_quality_summary.get("policy_version")
        or ENTRY_OBSERVATION_POLICY_VERSION
    )
    samples: list[Dict[str, Any]] = []

    for raw_symbol, raw_quality in evaluated.items():
        symbol = str(raw_symbol or "").upper()
        indicator = indicator_map.get(symbol)
        if not symbol or not isinstance(indicator, Mapping):
            continue
        quality = dict(raw_quality) if isinstance(raw_quality, Mapping) else {}
        strategy = indicator.get("strategy") or {}
        if not isinstance(strategy, Mapping):
            continue
        tactical = strategy.get("tactical_intraday") or {}
        if not isinstance(tactical, Mapping):
            tactical = {}

        baseline_price = _current_price(indicator)
        completed_bar = (
            tactical.get("completed_bar_close_time")
            or tactical.get("completed_bar_open_time")
        )
        if baseline_price is None or completed_bar is None:
            continue

        block_reasons = [
            str(reason)
            for reason in (quality.get("block_reasons") or [])
            if reason
        ]
        policy_outcome = "blocked" if block_reasons else "allowed"
        original = quality.get("original_candidate") or {}
        if not isinstance(original, Mapping):
            original = {}

        sample_key = "|".join(
            [
                "adverse-entry-observation",
                policy_version,
                symbol,
                str(completed_bar),
            ]
        )
        samples.append(
            {
                "sample_key": sample_key,
                "observed_at": observation_time,
                "symbol": symbol,
                "strategy_version": strategy.get("strategy_version"),
                "entry_policy_version": policy_version,
                "regime": strategy.get("regime"),
                "completed_15m_bar_close_time": str(completed_bar),
                "policy_outcome": policy_outcome,
                "baseline_price": baseline_price,
                "block_reasons": block_reasons,
                "hypothetical_stop_loss_percent": _as_float(
                    strategy.get("recommended_stop_loss_percent")
                ),
                "hypothetical_effective_exposure": _as_float(
                    original.get("recommended_effective_exposure_before_drawdown")
                ),
                "quality_snapshot": deepcopy(quality),
                "strategy_snapshot": deepcopy(dict(strategy)),
                "target_15m_at": observation_time + timedelta(minutes=15),
                "target_60m_at": observation_time + timedelta(minutes=60),
                "target_180m_at": observation_time + timedelta(minutes=180),
            }
        )

    return samples


def persist_entry_opportunity_samples(
    samples: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    sample_list = [dict(item) for item in samples if isinstance(item, Mapping)]
    inserted_keys: list[str] = []
    if not sample_list:
        return {"candidate_samples": 0, "inserted_samples": 0, "sample_keys": []}

    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            for sample in sample_list:
                cursor.execute(
                    """
                    INSERT INTO entry_opportunity_samples (
                        sample_key,
                        observed_at,
                        symbol,
                        strategy_version,
                        entry_policy_version,
                        regime,
                        completed_15m_bar_close_time,
                        policy_outcome,
                        baseline_price,
                        block_reasons,
                        hypothetical_stop_loss_percent,
                        hypothetical_effective_exposure,
                        quality_snapshot,
                        strategy_snapshot,
                        target_15m_at,
                        target_60m_at,
                        target_180m_at,
                        max_price_180m,
                        min_price_180m,
                        last_observed_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    ON CONFLICT (sample_key) DO NOTHING
                    RETURNING sample_key;
                    """,
                    (
                        sample["sample_key"],
                        sample["observed_at"],
                        sample["symbol"],
                        sample.get("strategy_version"),
                        sample["entry_policy_version"],
                        sample.get("regime"),
                        sample.get("completed_15m_bar_close_time"),
                        sample["policy_outcome"],
                        sample["baseline_price"],
                        Json(db_utils._normalize_for_json(sample["block_reasons"])),
                        sample.get("hypothetical_stop_loss_percent"),
                        sample.get("hypothetical_effective_exposure"),
                        Json(db_utils._normalize_for_json(sample["quality_snapshot"])),
                        Json(db_utils._normalize_for_json(sample["strategy_snapshot"])),
                        sample["target_15m_at"],
                        sample["target_60m_at"],
                        sample["target_180m_at"],
                        sample["baseline_price"],
                        sample["baseline_price"],
                        sample["observed_at"],
                    ),
                )
                row = cursor.fetchone()
                if row:
                    inserted_keys.append(str(row[0]))
        connection.commit()

    return {
        "candidate_samples": len(sample_list),
        "inserted_samples": len(inserted_keys),
        "sample_keys": [str(item["sample_key"]) for item in sample_list],
        "inserted_sample_keys": inserted_keys,
    }


def observe_pending_entry_opportunities(
    indicators: Iterable[Dict[str, Any]],
    *,
    observed_at: datetime | str | None = None,
) -> Dict[str, Any]:
    observation_time = _as_utc(observed_at)
    prices = {
        str(item.get("ticker") or "").upper(): _current_price(item)
        for item in indicators
        if isinstance(item, dict) and item.get("ticker")
    }
    prices = {
        symbol: price
        for symbol, price in prices.items()
        if price is not None and price > 0
    }
    updated = 0
    if not prices:
        return {"updated_samples": 0, "observed_symbols": []}

    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            for symbol, price in prices.items():
                cursor.execute(
                    """
                    UPDATE entry_opportunity_samples
                    SET
                        max_price_180m = CASE
                            WHEN %s <= target_180m_at
                            THEN GREATEST(max_price_180m, %s)
                            ELSE max_price_180m
                        END,
                        min_price_180m = CASE
                            WHEN %s <= target_180m_at
                            THEN LEAST(min_price_180m, %s)
                            ELSE min_price_180m
                        END,
                        actual_15m_at = CASE
                            WHEN actual_15m_price IS NULL AND target_15m_at <= %s
                            THEN %s ELSE actual_15m_at
                        END,
                        actual_15m_price = CASE
                            WHEN actual_15m_price IS NULL AND target_15m_at <= %s
                            THEN %s ELSE actual_15m_price
                        END,
                        actual_60m_at = CASE
                            WHEN actual_60m_price IS NULL AND target_60m_at <= %s
                            THEN %s ELSE actual_60m_at
                        END,
                        actual_60m_price = CASE
                            WHEN actual_60m_price IS NULL AND target_60m_at <= %s
                            THEN %s ELSE actual_60m_price
                        END,
                        actual_180m_at = CASE
                            WHEN actual_180m_price IS NULL AND target_180m_at <= %s
                            THEN %s ELSE actual_180m_at
                        END,
                        actual_180m_price = CASE
                            WHEN actual_180m_price IS NULL AND target_180m_at <= %s
                            THEN %s ELSE actual_180m_price
                        END,
                        last_observed_at = GREATEST(last_observed_at, %s),
                        completed = completed OR target_180m_at <= %s
                    WHERE symbol = %s
                      AND completed IS FALSE
                      AND observed_at <= %s;
                    """,
                    (
                        observation_time,
                        price,
                        observation_time,
                        price,
                        observation_time,
                        observation_time,
                        observation_time,
                        price,
                        observation_time,
                        observation_time,
                        observation_time,
                        price,
                        observation_time,
                        observation_time,
                        observation_time,
                        price,
                        observation_time,
                        observation_time,
                        symbol,
                        observation_time,
                    ),
                )
                updated += cursor.rowcount
        connection.commit()

    return {
        "updated_samples": updated,
        "observed_symbols": sorted(prices),
    }


def record_and_observe_entry_opportunities(
    indicators: Iterable[Dict[str, Any]],
    entry_quality_summary: Mapping[str, Any],
    *,
    observed_at: datetime | str | None = None,
) -> Dict[str, Any]:
    observation_time = _as_utc(observed_at)
    observation = observe_pending_entry_opportunities(
        indicators,
        observed_at=observation_time,
    )
    samples = build_entry_opportunity_samples(
        indicators,
        entry_quality_summary,
        observed_at=observation_time,
    )
    persistence = persist_entry_opportunity_samples(samples)
    return {
        "mode": "audit_only",
        "operational": False,
        **observation,
        **persistence,
    }


def link_entry_opportunity_samples(
    sample_keys: Iterable[str],
    *,
    bot_operation_id: int,
    decision: Mapping[str, Any],
) -> int:
    keys = sorted({str(item) for item in sample_keys if item})
    if not keys:
        return 0
    operation = str(decision.get("operation") or "unknown")
    decision_symbol = str(decision.get("symbol") or "").upper()
    decision_source = decision.get("decision_source")

    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE entry_opportunity_samples
                SET
                    first_bot_operation_id = COALESCE(
                        first_bot_operation_id,
                        %s
                    ),
                    first_live_operation = COALESCE(
                        first_live_operation,
                        %s
                    ),
                    last_live_operation = %s,
                    last_live_decision_source = %s,
                    executed_open_operation_id = CASE
                        WHEN %s = 'open' AND symbol = %s
                        THEN %s
                        ELSE executed_open_operation_id
                    END
                WHERE sample_key = ANY(%s);
                """,
                (
                    bot_operation_id,
                    operation,
                    operation,
                    decision_source,
                    operation,
                    decision_symbol,
                    bot_operation_id,
                    keys,
                ),
            )
            updated = cursor.rowcount
        connection.commit()
    return updated


def parse_stop_loss_events(stop_losses: Any) -> list[Dict[str, Any]]:
    if stop_losses is None:
        return []
    raw = stop_losses
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _fill_identity(fill: Mapping[str, Any]) -> tuple:
    return (
        str(fill.get("hash") or ""),
        str(fill.get("oid") or ""),
        str(fill.get("time") or ""),
        str(fill.get("px") or ""),
        str(fill.get("sz") or ""),
    )


def match_external_close_fills(
    fills: Any,
    *,
    symbol: str,
    expected_position_side: Optional[str],
    expected_stop_order_id: Optional[str],
    window_start_ms: int,
    window_end_ms: int,
) -> Dict[str, Any]:
    """Select and aggregate fills for one externally observed position close."""
    if not isinstance(fills, list):
        fills = []

    symbol_upper = str(symbol or "").upper()
    expected_oid = (
        str(expected_stop_order_id)
        if expected_stop_order_id not in (None, "")
        else None
    )
    window_candidates: list[Dict[str, Any]] = []
    for raw_fill in fills:
        if not isinstance(raw_fill, Mapping):
            continue
        fill = dict(raw_fill)
        fill_symbol = str(fill.get("coin") or fill.get("symbol") or "").upper()
        fill_time = _as_int(fill.get("time"))
        if (
            fill_symbol != symbol_upper
            or fill_time is None
            or fill_time < window_start_ms
            or fill_time > window_end_ms
        ):
            continue
        window_candidates.append(fill)

    exact_oid = (
        [
            fill
            for fill in window_candidates
            if str(fill.get("oid") or "") == expected_oid
        ]
        if expected_oid
        else []
    )
    if exact_oid:
        selected = exact_oid
        match_method = "stop_order_id"
    else:
        side = str(expected_position_side or "").lower()
        expected_fill_side = "A" if side == "long" else "B" if side == "short" else None
        selected = []
        for fill in window_candidates:
            fill_side = str(fill.get("side") or "").upper()
            direction = str(fill.get("dir") or "").lower()
            is_closing_direction = "close" in direction
            is_expected_side = (
                expected_fill_side is not None and fill_side == expected_fill_side
            )
            if is_closing_direction or is_expected_side:
                selected.append(fill)
        match_method = "symbol_side_time_window"

    deduplicated: list[Dict[str, Any]] = []
    seen = set()
    for fill in selected:
        identity = _fill_identity(fill)
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(fill)
    selected = deduplicated

    if not selected:
        return {
            "matched": False,
            "match_method": None,
            "fill_count": 0,
            "raw_fills": [],
        }

    sized_fills = []
    for fill in selected:
        size = _as_float(fill.get("sz"))
        price = _as_float(fill.get("px"))
        if size is None or size <= 0 or price is None or price <= 0:
            continue
        sized_fills.append((fill, size, price))
    if not sized_fills:
        return {
            "matched": False,
            "match_method": None,
            "fill_count": 0,
            "raw_fills": [],
        }

    total_size = sum(size for _, size, _ in sized_fills)
    avg_price = (
        sum(size * price for _, size, price in sized_fills) / total_size
        if total_size > 0
        else None
    )
    fees = [
        _as_float(fill.get("fee"))
        for fill, _, _ in sized_fills
        if _as_float(fill.get("fee")) is not None
    ]
    closed_pnls = [
        _as_float(fill.get("closedPnl"))
        for fill, _, _ in sized_fills
        if _as_float(fill.get("closedPnl")) is not None
    ]
    fill_times = [
        _as_int(fill.get("time"))
        for fill, _, _ in sized_fills
        if _as_int(fill.get("time")) is not None
    ]
    fee_tokens = sorted(
        {
            str(fill.get("feeToken"))
            for fill, _, _ in sized_fills
            if fill.get("feeToken")
        }
    )
    order_ids = sorted(
        {
            str(fill.get("oid"))
            for fill, _, _ in sized_fills
            if fill.get("oid") is not None
        }
    )
    hashes = sorted(
        {
            str(fill.get("hash"))
            for fill, _, _ in sized_fills
            if fill.get("hash")
        }
    )

    return {
        "matched": True,
        "match_method": match_method,
        "fill_count": len(sized_fills),
        "total_filled_size": total_size,
        "avg_fill_price": avg_price,
        "total_fee": sum(fees) if fees else None,
        "fee_token": ",".join(fee_tokens) if fee_tokens else None,
        "total_closed_pnl": sum(closed_pnls) if closed_pnls else None,
        "first_fill_time_ms": min(fill_times) if fill_times else None,
        "last_fill_time_ms": max(fill_times) if fill_times else None,
        "order_ids": order_ids,
        "fill_hashes": hashes,
        "raw_fills": [fill for fill, _, _ in sized_fills],
    }


def _latest_open_metadata(
    cursor: Any,
    *,
    symbol: str,
    detected_at: datetime,
) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT
            bo.id,
            er.filled_size,
            er.raw_payload->'execution'->'stop_loss_audit'->>'order_id'
                AS stop_order_id
        FROM bot_operations bo
        JOIN execution_results er ON er.operation_id = bo.id
        WHERE bo.symbol = %s
          AND bo.operation = 'open'
          AND er.execution_status = 'success'
          AND bo.created_at <= %s
        ORDER BY bo.created_at DESC
        LIMIT 1;
        """,
        (symbol, detected_at),
    )
    row = cursor.fetchone()
    if not row:
        return {}
    return {
        "open_operation_id": row[0],
        "filled_size": _as_float(row[1]),
        "expected_stop_order_id": str(row[2]) if row[2] is not None else None,
    }


def register_external_close_events(
    stop_losses: Any,
    *,
    detected_at: datetime | str | None = None,
) -> Dict[str, Any]:
    events = parse_stop_loss_events(stop_losses)
    observation_time = _as_utc(detected_at)
    registered = 0
    if not events:
        return {"events": 0, "registered": 0}

    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            for event in events:
                operation_id = _as_int(event.get("operation_id"))
                symbol = str(event.get("symbol") or "").upper()
                if operation_id is None or not symbol:
                    continue
                metadata = _latest_open_metadata(
                    cursor,
                    symbol=symbol,
                    detected_at=observation_time,
                )
                expected_size = _as_float(event.get("size"))
                if expected_size is None:
                    expected_size = metadata.get("filled_size")
                window_start = observation_time - timedelta(
                    minutes=EXTERNAL_CLOSE_LOOKBACK_MINUTES
                )
                window_end = observation_time + timedelta(
                    minutes=EXTERNAL_CLOSE_LOOKAHEAD_MINUTES
                )
                cursor.execute(
                    """
                    INSERT INTO external_close_reconciliations (
                        detected_operation_id,
                        detected_at,
                        symbol,
                        expected_position_side,
                        expected_size,
                        open_operation_id,
                        expected_stop_order_id,
                        window_start_at,
                        window_end_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (detected_operation_id) DO NOTHING
                    RETURNING id;
                    """,
                    (
                        operation_id,
                        observation_time,
                        symbol,
                        event.get("direction"),
                        expected_size,
                        metadata.get("open_operation_id"),
                        metadata.get("expected_stop_order_id"),
                        window_start,
                        window_end,
                    ),
                )
                if cursor.fetchone():
                    registered += 1
        connection.commit()
    return {"events": len(events), "registered": registered}


def _epoch_ms(value: datetime) -> int:
    return int(_as_utc(value).timestamp() * 1000)


def _ms_to_utc(value: Optional[int]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)


def reconcile_pending_external_closures(
    bot: Any,
    stop_losses: Any = None,
    *,
    observed_at: datetime | str | None = None,
) -> Dict[str, Any]:
    """Reconcile external closes without ever blocking trading on an audit failure."""
    observation_time = _as_utc(observed_at)
    try:
        registration = register_external_close_events(
            stop_losses,
            detected_at=observation_time,
        )
        with db_utils.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        detected_operation_id,
                        detected_at,
                        symbol,
                        expected_position_side,
                        expected_size,
                        expected_stop_order_id,
                        window_start_at,
                        window_end_at
                    FROM external_close_reconciliations
                    WHERE reconciliation_status = 'pending'
                    ORDER BY detected_at;
                    """
                )
                pending = cursor.fetchall()

        matched_count = 0
        expired_count = 0
        attempted_count = 0
        for row in pending:
            (
                reconciliation_id,
                _detected_operation_id,
                detected_at_value,
                symbol,
                expected_side,
                _expected_size,
                expected_stop_order_id,
                window_start,
                window_end,
            ) = row
            attempted_count += 1
            try:
                fills = bot.info.user_fills_by_time(
                    bot.account_address,
                    _epoch_ms(window_start),
                    _epoch_ms(window_end),
                    aggregate_by_time=False,
                )
                matched = match_external_close_fills(
                    fills,
                    symbol=symbol,
                    expected_position_side=expected_side,
                    expected_stop_order_id=expected_stop_order_id,
                    window_start_ms=_epoch_ms(window_start),
                    window_end_ms=_epoch_ms(window_end),
                )
                expired = observation_time >= (
                    _as_utc(detected_at_value)
                    + timedelta(hours=EXTERNAL_CLOSE_RETRY_HOURS)
                )
                status = (
                    "matched"
                    if matched.get("matched")
                    else "unmatched_expired"
                    if expired
                    else "pending"
                )
                if status == "matched":
                    matched_count += 1
                elif status == "unmatched_expired":
                    expired_count += 1

                with db_utils.get_connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE external_close_reconciliations
                            SET
                                reconciliation_status = %s,
                                attempt_count = attempt_count + 1,
                                last_attempt_at = %s,
                                matched_at = CASE
                                    WHEN %s = 'matched' THEN %s
                                    ELSE matched_at
                                END,
                                match_method = %s,
                                total_filled_size = %s,
                                avg_fill_price = %s,
                                total_fee = %s,
                                fee_token = %s,
                                total_closed_pnl = %s,
                                first_fill_at = %s,
                                last_fill_at = %s,
                                order_ids = %s,
                                fill_hashes = %s,
                                raw_fills = %s,
                                error_message = NULL
                            WHERE id = %s;
                            """,
                            (
                                status,
                                observation_time,
                                status,
                                observation_time,
                                matched.get("match_method"),
                                matched.get("total_filled_size"),
                                matched.get("avg_fill_price"),
                                matched.get("total_fee"),
                                matched.get("fee_token"),
                                matched.get("total_closed_pnl"),
                                _ms_to_utc(matched.get("first_fill_time_ms")),
                                _ms_to_utc(matched.get("last_fill_time_ms")),
                                Json(
                                    db_utils._normalize_for_json(
                                        matched.get("order_ids") or []
                                    )
                                ),
                                Json(
                                    db_utils._normalize_for_json(
                                        matched.get("fill_hashes") or []
                                    )
                                ),
                                Json(
                                    db_utils._normalize_for_json(
                                        matched.get("raw_fills") or []
                                    )
                                ),
                                reconciliation_id,
                            ),
                        )
                    connection.commit()
            except Exception as exc:  # noqa: BLE001
                with db_utils.get_connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE external_close_reconciliations
                            SET
                                attempt_count = attempt_count + 1,
                                last_attempt_at = %s,
                                error_message = %s
                            WHERE id = %s;
                            """,
                            (observation_time, str(exc), reconciliation_id),
                        )
                    connection.commit()

        return {
            "mode": "audit_only",
            "operational": False,
            **registration,
            "pending_examined": attempted_count,
            "matched": matched_count,
            "expired": expired_count,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "mode": "audit_only",
            "operational": False,
            "error": str(exc),
            "events": len(parse_stop_loss_events(stop_losses)),
            "registered": 0,
            "pending_examined": 0,
            "matched": 0,
            "expired": 0,
        }

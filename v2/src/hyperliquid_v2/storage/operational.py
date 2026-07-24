from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from hyperliquid_v2.storage.postgres import PostgresRepository, _float, _json


FAILED_BREAKOUT_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS v2_failed_breakout_events (
    event_key TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol TEXT NOT NULL,
    original_direction TEXT NOT NULL,
    reversal_direction TEXT NOT NULL,
    breakout_level NUMERIC(30, 10) NOT NULL,
    breakout_extreme NUMERIC(30, 10) NOT NULL,
    armed_at TIMESTAMPTZ NOT NULL,
    failed_at TIMESTAMPTZ,
    entry_mode TEXT,
    status TEXT NOT NULL,
    decision_id TEXT,
    entry_price NUMERIC(30, 10),
    stop_price NUMERIC(30, 10),
    target_price NUMERIC(30, 10),
    closed_at TIMESTAMPTZ,
    mfe_r NUMERIC(20, 10),
    mae_r NUMERIC(20, 10),
    gross_r NUMERIC(20, 10),
    cost_r NUMERIC(20, 10),
    realized_net_r NUMERIC(20, 10),
    outcome TEXT,
    source_sample_key TEXT,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_failed_breakout_symbol_time
    ON v2_failed_breakout_events(symbol, armed_at DESC);
CREATE INDEX IF NOT EXISTS idx_v2_failed_breakout_status
    ON v2_failed_breakout_events(status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_v2_failed_breakout_source_sample
    ON v2_failed_breakout_events(source_sample_key)
    WHERE source_sample_key IS NOT NULL;
"""


class OperationalPostgresRepository(PostgresRepository):
    """Runtime refinements for net outcomes and failed-breakout research."""

    async def connect(self) -> None:
        await super().connect()
        await self._require_pool().execute(FAILED_BREAKOUT_SCHEMA_SQL)

    async def record_quant_sample(
        self,
        sample_key: str,
        observed_at: datetime,
        symbol: str,
        setup_family: str,
        baseline_price: float,
        stop_distance_pct: float | None,
        decision_id: str | None,
        source: str,
        payload: dict[str, Any],
    ) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO v2_quant_observations(
                sample_key, observed_at, symbol,
                setup_family, baseline_price,
                stop_distance_pct, decision_id,
                source, payload
            ) VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT(sample_key) DO UPDATE SET
                decision_id=COALESCE(EXCLUDED.decision_id, v2_quant_observations.decision_id),
                source=CASE WHEN EXCLUDED.decision_id IS NOT NULL THEN EXCLUDED.source ELSE v2_quant_observations.source END,
                payload=CASE WHEN EXCLUDED.decision_id IS NOT NULL THEN EXCLUDED.payload ELSE v2_quant_observations.payload END;
            """,
            sample_key,
            observed_at,
            symbol,
            setup_family,
            baseline_price,
            stop_distance_pct,
            decision_id,
            source,
            _json(payload),
        )

    async def finalize_quant_sample(
        self,
        sample_key: str,
        realized_net_r: float,
        finished_negative: bool,
    ) -> None:
        await self._require_pool().execute(
            """
            UPDATE v2_quant_observations
            SET realized_net_r=$2,
                finished_negative=$3,
                reached_green=COALESCE(reached_green, mfe_r > 0.10),
                completed=TRUE
            WHERE sample_key=$1;
            """,
            sample_key,
            realized_net_r,
            finished_negative,
        )

    async def mature_quant_samples(
        self,
        observed_at: datetime,
        prices: dict[str, float],
    ) -> int:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            SELECT id, observed_at, symbol, baseline_price,
                   stop_distance_pct, return_15m_pct,
                   return_60m_pct, return_180m_pct,
                   mfe_r, mae_r, payload
            FROM v2_quant_observations
            WHERE observed_at >= NOW() - INTERVAL '24 hours'
              AND completed IS FALSE;
            """
        )
        changed = 0
        for row in rows:
            price = prices.get(row["symbol"])
            if not price:
                continue
            age = (observed_at - row["observed_at"]).total_seconds()
            baseline = float(row["baseline_price"])
            stop_pct = _float(row["stop_distance_pct"]) or 0.0
            payload = row["payload"] if isinstance(row["payload"], dict) else {}
            direction = _quant_direction(payload)
            raw_return_pct = (price / baseline - 1.0) * 100.0
            return_pct = raw_return_pct if direction == "long" else -raw_return_pct
            current_r = return_pct / stop_pct if stop_pct > 0 else None
            return_15m = _float(row["return_15m_pct"])
            return_60m = _float(row["return_60m_pct"])
            return_180m = _float(row["return_180m_pct"])
            if age >= 900 and return_15m is None:
                return_15m = return_pct
            if age >= 3600 and return_60m is None:
                return_60m = return_pct
            if age >= 10800 and return_180m is None:
                return_180m = return_pct
            mfe = _float(row["mfe_r"])
            mae = _float(row["mae_r"])
            if current_r is not None:
                mfe = max(mfe if mfe is not None else current_r, current_r)
                mae = min(mae if mae is not None else current_r, current_r)
            completed = age >= 10800 and return_180m is not None
            realized_net_r = None
            if completed and stop_pct > 0:
                cost_bps = float(payload.get("round_trip_cost_bps") or 10.0)
                cost_r = (cost_bps / 100.0) / stop_pct
                realized_net_r = (
                    -1.0 - cost_r
                    if mae is not None and mae <= -1.0
                    else return_180m / stop_pct - cost_r
                )
            await pool.execute(
                """
                UPDATE v2_quant_observations SET
                    return_15m_pct=$2,
                    return_60m_pct=$3,
                    return_180m_pct=$4,
                    mfe_r=$5,
                    mae_r=$6,
                    realized_net_r=$7,
                    reached_green=$8,
                    finished_negative=$9,
                    completed=$10
                WHERE id=$1;
                """,
                row["id"],
                return_15m,
                return_60m,
                return_180m,
                mfe,
                mae,
                realized_net_r,
                bool(mfe is not None and mfe > 0.10),
                bool(completed and realized_net_r is not None and realized_net_r < 0),
                completed,
            )
            changed += 1
        return changed

    async def failed_breakout_processed_keys(self) -> set[str]:
        rows = await self._require_pool().fetch(
            """
            SELECT event_key
            FROM v2_failed_breakout_events
            WHERE decision_id IS NOT NULL
               OR status IN ('routed', 'replayed');
            """
        )
        return {str(row["event_key"]) for row in rows}

    async def save_failed_breakout_event(
        self,
        record: Mapping[str, Any],
        *,
        status: str | None = None,
        decision_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        event_key = str(record["event_key"])
        await self._require_pool().execute(
            """
            INSERT INTO v2_failed_breakout_events(
                event_key, symbol, original_direction,
                reversal_direction, breakout_level,
                breakout_extreme, armed_at, failed_at,
                entry_mode, status, decision_id,
                entry_price, stop_price, target_price,
                closed_at, mfe_r, mae_r, gross_r,
                cost_r, realized_net_r, outcome,
                source_sample_key, payload
            ) VALUES(
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13, $14, $15,
                $16, $17, $18, $19, $20, $21, $22,
                $23::jsonb
            )
            ON CONFLICT(event_key) DO UPDATE SET
                updated_at=NOW(),
                breakout_extreme=EXCLUDED.breakout_extreme,
                failed_at=COALESCE(EXCLUDED.failed_at, v2_failed_breakout_events.failed_at),
                entry_mode=COALESCE(EXCLUDED.entry_mode, v2_failed_breakout_events.entry_mode),
                status=EXCLUDED.status,
                decision_id=COALESCE(EXCLUDED.decision_id, v2_failed_breakout_events.decision_id),
                entry_price=COALESCE(EXCLUDED.entry_price, v2_failed_breakout_events.entry_price),
                stop_price=COALESCE(EXCLUDED.stop_price, v2_failed_breakout_events.stop_price),
                target_price=COALESCE(EXCLUDED.target_price, v2_failed_breakout_events.target_price),
                closed_at=COALESCE(EXCLUDED.closed_at, v2_failed_breakout_events.closed_at),
                mfe_r=COALESCE(EXCLUDED.mfe_r, v2_failed_breakout_events.mfe_r),
                mae_r=COALESCE(EXCLUDED.mae_r, v2_failed_breakout_events.mae_r),
                gross_r=COALESCE(EXCLUDED.gross_r, v2_failed_breakout_events.gross_r),
                cost_r=COALESCE(EXCLUDED.cost_r, v2_failed_breakout_events.cost_r),
                realized_net_r=COALESCE(EXCLUDED.realized_net_r, v2_failed_breakout_events.realized_net_r),
                outcome=COALESCE(EXCLUDED.outcome, v2_failed_breakout_events.outcome),
                source_sample_key=COALESCE(EXCLUDED.source_sample_key, v2_failed_breakout_events.source_sample_key),
                payload=EXCLUDED.payload;
            """,
            event_key,
            str(record["symbol"]),
            str(record["original_direction"]),
            str(record["reversal_direction"]),
            float(record["breakout_level"]),
            float(record["breakout_extreme"]),
            _datetime(record["armed_at"]),
            _datetime(record.get("failed_at")),
            _optional_text(record.get("entry_mode")),
            status or str(record.get("status") or "observed"),
            decision_id or _optional_text(record.get("decision_id")),
            _float(record.get("entry_price")),
            _float(record.get("stop_price")),
            _float(record.get("target_price")),
            _datetime(record.get("closed_at")),
            _float(record.get("mfe_r")),
            _float(record.get("mae_r")),
            _float(record.get("gross_r")),
            _float(record.get("cost_r")),
            _float(record.get("realized_net_r")),
            _optional_text(record.get("outcome")),
            _optional_text(record.get("source_sample_key")),
            _json(dict(payload or record)),
        )

    async def blocked_samples_for_failed_breakout_replay(
        self,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        rows = await self._require_pool().fetch(
            """
            SELECT q.sample_key, q.observed_at, q.symbol,
                   q.baseline_price, q.payload
            FROM v2_quant_observations q
            WHERE q.source='blocked'
              AND q.completed IS TRUE
              AND q.setup_family IN ('breakout_continuation', 'breakout_retest')
              AND q.payload->'feature'->>'donchian_high_20_15m' IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM v2_failed_breakout_events event
                  WHERE event.source_sample_key=q.sample_key
              )
            ORDER BY q.observed_at DESC
            LIMIT $1;
            """,
            limit,
        )
        return [dict(row) for row in rows]

    async def feature_points_for_replay(
        self,
        symbol: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        rows = await self._require_pool().fetch(
            """
            SELECT observed_at, payload
            FROM v2_market_features
            WHERE symbol=$1
              AND observed_at >= $2
              AND observed_at <= $3
            ORDER BY observed_at;
            """,
            symbol,
            start_at,
            end_at,
        )
        return [dict(row) for row in rows]

    async def supervisor_metrics(self) -> dict[str, Any]:
        metrics = await super().supervisor_metrics()
        summary = await self._require_pool().fetchrow(
            """
            SELECT COUNT(*) AS events,
                   COUNT(*) FILTER (WHERE status='routed') AS routed,
                   COUNT(*) FILTER (WHERE status='replayed') AS replayed,
                   AVG(realized_net_r) FILTER (
                       WHERE status='replayed'
                   ) AS replay_avg_net_r,
                   AVG(CASE WHEN outcome='win' THEN 1.0 ELSE 0.0 END)
                       FILTER (WHERE status='replayed') AS replay_win_rate
            FROM v2_failed_breakout_events;
            """
        )
        metrics["failed_breakout"] = dict(summary or {})
        return metrics


def _quant_direction(payload: Mapping[str, Any]) -> str:
    direction = str(payload.get("direction") or "").lower()
    if direction in {"long", "short"}:
        return direction
    packet = payload.get("packet_preview")
    if isinstance(packet, Mapping):
        thesis = packet.get("trade_thesis")
        if isinstance(thesis, Mapping):
            direction = str(thesis.get("direction") or "").lower()
            if direction in {"long", "short"}:
                return direction
    return "long"


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise TypeError(f"invalid datetime value: {value!r}")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

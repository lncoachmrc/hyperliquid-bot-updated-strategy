from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

from hyperliquid_v2.domain.models import DecisionPacket, ModelDecision
from hyperliquid_v2.quant_expert.evidence import ComparableObservation


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS v2_market_features (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    payload JSONB NOT NULL,
    UNIQUE (symbol, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_v2_market_features_symbol_time
    ON v2_market_features(symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS v2_account_states (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    wallet_address TEXT NOT NULL,
    equity_usd NUMERIC(30, 10),
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_account_states_time
    ON v2_account_states(observed_at DESC);

CREATE TABLE IF NOT EXISTS v2_decision_packets (
    decision_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decision_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    packet JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_model_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES v2_decision_packets(decision_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence NUMERIC(12, 8),
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_model_decisions_decision
    ON v2_model_decisions(decision_id, created_at);

CREATE TABLE IF NOT EXISTS v2_shadow_actions (
    id BIGSERIAL PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES v2_decision_packets(decision_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload JSONB NOT NULL,
    UNIQUE (decision_id)
);

CREATE TABLE IF NOT EXISTS v2_position_state_events (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    phase TEXT NOT NULL,
    current_r NUMERIC(20, 10),
    mfe_r NUMERIC(20, 10),
    mae_r NUMERIC(20, 10),
    profit_floor_r NUMERIC(20, 10),
    close_review BOOLEAN NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_position_events_symbol_time
    ON v2_position_state_events(symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS v2_quant_observations (
    id BIGSERIAL PRIMARY KEY,
    sample_key TEXT NOT NULL UNIQUE,
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    setup_family TEXT NOT NULL,
    baseline_price NUMERIC(30, 10) NOT NULL,
    stop_distance_pct NUMERIC(20, 10),
    decision_id TEXT,
    source TEXT NOT NULL,
    return_15m_pct NUMERIC(20, 10),
    return_60m_pct NUMERIC(20, 10),
    return_180m_pct NUMERIC(20, 10),
    mfe_r NUMERIC(20, 10),
    mae_r NUMERIC(20, 10),
    realized_net_r NUMERIC(20, 10),
    reached_green BOOLEAN,
    finished_negative BOOLEAN,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_quant_observations_family
    ON v2_quant_observations(setup_family, observed_at DESC);

CREATE TABLE IF NOT EXISTS v2_service_heartbeats (
    service_name TEXT PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_supervisor_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL,
    metrics JSONB NOT NULL,
    model_output JSONB,
    policy_output JSONB,
    github_output JSONB,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS v2_model_benchmarks (
    id BIGSERIAL PRIMARY KEY,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    utility_score NUMERIC(20, 10),
    json_valid_rate NUMERIC(12, 8),
    action_consistency NUMERIC(12, 8),
    counterfactual_net_r NUMERIC(20, 10),
    payload JSONB NOT NULL,
    UNIQUE (provider, model, decision_type, evaluated_at)
);
"""


class PostgresRepository:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            self.dsn,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        async with self.pool.acquire() as connection:
            await connection.execute(SCHEMA_SQL)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("PostgresRepository is not connected")
        return self.pool

    async def heartbeat(
        self,
        service_name: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO v2_service_heartbeats(
                service_name, observed_at, status, payload
            )
            VALUES($1, NOW(), $2, $3::jsonb)
            ON CONFLICT(service_name) DO UPDATE SET
                observed_at=EXCLUDED.observed_at,
                status=EXCLUDED.status,
                payload=EXCLUDED.payload;
            """,
            service_name,
            status,
            _json(payload),
        )

    async def save_feature(
        self,
        observed_at: datetime,
        symbol: str,
        payload: dict[str, Any],
    ) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO v2_market_features(
                observed_at, symbol, payload
            )
            VALUES($1, $2, $3::jsonb)
            ON CONFLICT(symbol, observed_at) DO UPDATE SET
                payload=EXCLUDED.payload;
            """,
            observed_at,
            symbol,
            _json(payload),
        )

    async def save_account_state(
        self,
        observed_at: datetime,
        wallet: str,
        equity: float,
        payload: dict[str, Any],
    ) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO v2_account_states(
                observed_at, wallet_address, equity_usd, payload
            ) VALUES($1, $2, $3, $4::jsonb);
            """,
            observed_at,
            wallet,
            equity,
            _json(payload),
        )

    async def save_packet(self, packet: DecisionPacket) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO v2_decision_packets(
                decision_id, decision_type, symbol, packet
            ) VALUES($1, $2, $3, $4::jsonb)
            ON CONFLICT(decision_id) DO NOTHING;
            """,
            packet.decision_id,
            str(packet.decision_type),
            packet.symbol,
            _json(packet.to_dict()),
        )

    async def save_model_decision(
        self,
        decision_id: str,
        role: str,
        decision: ModelDecision,
    ) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO v2_model_decisions(
                decision_id, provider, model, role,
                action, confidence, payload
            ) VALUES($1, $2, $3, $4, $5, $6, $7::jsonb);
            """,
            decision_id,
            decision.provider,
            decision.model,
            role,
            str(decision.action),
            decision.confidence,
            _json(asdict(decision)),
        )

    async def save_shadow_action(
        self,
        decision_id: str,
        symbol: str,
        action: str,
        source: str,
        reason: str,
        payload: dict[str, Any],
    ) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO v2_shadow_actions(
                decision_id, symbol, action,
                source, reason, payload
            ) VALUES($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT(decision_id) DO NOTHING;
            """,
            decision_id,
            symbol,
            action,
            source,
            reason,
            _json(payload),
        )

    async def save_position_event(
        self,
        observed_at: datetime,
        symbol: str,
        phase: str,
        current_r: float,
        mfe_r: float,
        mae_r: float,
        profit_floor_r: float | None,
        close_review: bool,
        payload: dict[str, Any],
    ) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO v2_position_state_events(
                observed_at, symbol, phase, current_r,
                mfe_r, mae_r, profit_floor_r,
                close_review, payload
            ) VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb);
            """,
            observed_at,
            symbol,
            phase,
            current_r,
            mfe_r,
            mae_r,
            profit_floor_r,
            close_review,
            _json(payload),
        )

    async def comparable_observations(
        self,
        setup_family: str,
        limit: int = 500,
    ) -> list[ComparableObservation]:
        rows = await self._require_pool().fetch(
            """
            SELECT setup_family, return_15m_pct,
                   return_60m_pct, return_180m_pct,
                   mfe_r, mae_r, realized_net_r,
                   reached_green, finished_negative
            FROM v2_quant_observations
            WHERE setup_family=$1 AND completed IS TRUE
            ORDER BY observed_at DESC
            LIMIT $2;
            """,
            setup_family,
            limit,
        )
        return [
            ComparableObservation(
                setup_family=row["setup_family"],
                return_15m_pct=_float(row["return_15m_pct"]),
                return_60m_pct=_float(row["return_60m_pct"]),
                return_180m_pct=_float(row["return_180m_pct"]),
                mfe_r=_float(row["mfe_r"]),
                mae_r=_float(row["mae_r"]),
                realized_net_r=_float(row["realized_net_r"]),
                reached_green=bool(row["reached_green"]),
                finished_negative=bool(row["finished_negative"]),
            )
            for row in rows
        ]

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
                decision_id=COALESCE(
                    EXCLUDED.decision_id,
                    v2_quant_observations.decision_id
                ),
                payload=CASE
                    WHEN EXCLUDED.decision_id IS NOT NULL
                    THEN EXCLUDED.payload
                    ELSE v2_quant_observations.payload
                END;
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
                reached_green=COALESCE(
                    reached_green,
                    mfe_r > 0.10
                ),
                completed=CASE
                    WHEN return_180m_pct IS NOT NULL
                    THEN TRUE
                    ELSE completed
                END
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
            SELECT id, observed_at, symbol,
                   baseline_price, stop_distance_pct,
                   return_15m_pct, return_60m_pct,
                   return_180m_pct, mfe_r, mae_r
            FROM v2_quant_observations
            WHERE observed_at >= NOW() - INTERVAL '4 hours'
              AND completed IS FALSE;
            """
        )
        changed = 0
        for row in rows:
            price = prices.get(row["symbol"])
            if not price:
                continue
            age = (
                observed_at - row["observed_at"]
            ).total_seconds()
            baseline = float(row["baseline_price"])
            stop_pct = _float(row["stop_distance_pct"]) or 0.0
            return_pct = (price / baseline - 1.0) * 100.0
            current_r = (
                return_pct / stop_pct
                if stop_pct > 0
                else None
            )
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
                mfe = max(
                    mfe if mfe is not None else current_r,
                    current_r,
                )
                mae = min(
                    mae if mae is not None else current_r,
                    current_r,
                )
            completed = age >= 10800 and return_180m is not None
            await pool.execute(
                """
                UPDATE v2_quant_observations SET
                    return_15m_pct=$2,
                    return_60m_pct=$3,
                    return_180m_pct=$4,
                    mfe_r=$5,
                    mae_r=$6,
                    reached_green=$7,
                    finished_negative=$8,
                    completed=$9
                WHERE id=$1;
                """,
                row["id"],
                return_15m,
                return_60m,
                return_180m,
                mfe,
                mae,
                bool(mfe is not None and mfe > 0.10),
                bool(
                    completed
                    and return_180m is not None
                    and return_180m < 0
                ),
                completed,
            )
            changed += 1
        return changed

    async def supervisor_metrics(self) -> dict[str, Any]:
        pool = self._require_pool()
        summary = await pool.fetchrow(
            """
            SELECT COUNT(*) AS samples,
                   COUNT(*) FILTER (WHERE completed) AS completed,
                   AVG(realized_net_r)
                       FILTER (WHERE completed) AS avg_net_r,
                   AVG(
                       CASE
                           WHEN reached_green AND finished_negative
                           THEN 1 ELSE 0
                       END
                   ) FILTER (WHERE completed) AS green_to_red_rate,
                   COUNT(DISTINCT symbol)
                       FILTER (WHERE completed) AS symbols
            FROM v2_quant_observations;
            """
        )
        exits = await pool.fetchrow(
            """
            SELECT COUNT(*) AS events,
                   AVG(current_r)
                       FILTER (WHERE close_review)
                       AS avg_close_review_r,
                   AVG(mfe_r-current_r)
                       FILTER (WHERE close_review)
                       AS avg_giveback_r
            FROM v2_position_state_events
            WHERE observed_at >= NOW() - INTERVAL '7 days';
            """
        )
        actions = await pool.fetch(
            """
            SELECT action, COUNT(*) AS count
            FROM v2_shadow_actions
            WHERE created_at >= NOW() - INTERVAL '7 days'
            GROUP BY action
            ORDER BY action;
            """
        )
        return {
            "quant": dict(summary or {}),
            "position_exit": dict(exits or {}),
            "shadow_actions": [dict(row) for row in actions],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def save_supervisor_run(
        self,
        run_id: str,
        status: str,
        metrics: dict[str, Any],
        model_output: dict[str, Any] | None = None,
        policy_output: dict[str, Any] | None = None,
        github_output: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO v2_supervisor_runs(
                run_id, status, metrics, model_output,
                policy_output, github_output, error_message
            ) VALUES($1, $2, $3::jsonb, $4::jsonb,
                     $5::jsonb, $6::jsonb, $7)
            ON CONFLICT(run_id) DO UPDATE SET
                status=EXCLUDED.status,
                model_output=EXCLUDED.model_output,
                policy_output=EXCLUDED.policy_output,
                github_output=EXCLUDED.github_output,
                error_message=EXCLUDED.error_message;
            """,
            run_id,
            status,
            _json(metrics),
            _json(model_output)
            if model_output is not None
            else None,
            _json(policy_output)
            if policy_output is not None
            else None,
            _json(github_output)
            if github_output is not None
            else None,
            error_message,
        )

    async def status(self) -> dict[str, Any]:
        rows = await self._require_pool().fetch(
            """
            SELECT service_name, observed_at, status, payload
            FROM v2_service_heartbeats
            ORDER BY service_name;
            """
        )
        return {
            row["service_name"]: {
                "observed_at": row["observed_at"].isoformat(),
                "status": row["status"],
                "payload": row["payload"],
            }
            for row in rows
        }


def _json(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(
        value,
        default=_default,
        separators=(",", ":"),
        allow_nan=False,
    )


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(
        f"not JSON serializable: {type(value)!r}"
    )


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

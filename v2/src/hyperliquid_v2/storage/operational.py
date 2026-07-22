from __future__ import annotations

from datetime import datetime
from typing import Any

from hyperliquid_v2.storage.postgres import PostgresRepository, _float, _json


class OperationalPostgresRepository(PostgresRepository):
    """Runtime refinements for net counterfactual outcomes and closed positions."""

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
            return_pct = (price / baseline - 1.0) * 100.0
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
                payload = row["payload"] if isinstance(row["payload"], dict) else {}
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

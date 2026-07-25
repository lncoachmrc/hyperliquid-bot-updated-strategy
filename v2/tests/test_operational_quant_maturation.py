from datetime import datetime, timedelta, timezone

import pytest

from hyperliquid_v2.storage.operational import (
    OperationalPostgresRepository,
)


class FakePool:
    def __init__(self, row):
        self.row = row
        self.update_args = None

    async def fetch(self, _query):
        return [self.row]

    async def execute(self, _query, *args):
        self.update_args = args


class FakeEventPool:
    def __init__(self):
        self.rows = None

    async def executemany(self, _query, rows):
        self.rows = rows


def row(now, exit_policy):
    return {
        "id": 1,
        "observed_at": now - timedelta(minutes=10),
        "symbol": "BTC",
        "baseline_price": 100.0,
        "stop_distance_pct": 1.0,
        "return_15m_pct": None,
        "return_60m_pct": None,
        "return_180m_pct": None,
        "mfe_r": None,
        "mae_r": None,
        "payload": {
            "direction": "short",
            "horizon_minutes": 180,
            "exit_policy": exit_policy,
            "round_trip_cost_bps": 10.0,
        },
    }


async def mature_once(exit_policy):
    now = datetime.now(timezone.utc)
    repository = OperationalPostgresRepository("postgresql://unused")
    pool = FakePool(row(now, exit_policy))
    repository.pool = pool

    await repository.mature_quant_samples(now, {"BTC": 102.0})

    return pool.update_args


@pytest.mark.asyncio
async def test_base_rate_ignores_stop_and_keeps_full_horizon_denominator():
    update = await mature_once("horizon_only")

    assert update is not None
    assert update[-1] is False
    assert update[6] is None


@pytest.mark.asyncio
async def test_portfolio_sample_closes_immediately_when_stop_is_observed():
    update = await mature_once("stop_or_horizon")

    assert update is not None
    assert update[-1] is True
    assert update[6] == -1.1


@pytest.mark.asyncio
async def test_public_fill_writer_matches_additive_cost_schema():
    repository = OperationalPostgresRepository("postgresql://unused")
    pool = FakeEventPool()
    repository.pool = pool

    count = await repository.save_observed_fills(
        [
            {
                "coin": "BTC",
                "px": "100",
                "sz": "2",
                "time": 1_800_000_000_000,
                "tid": 1,
                "fee": "0.07",
                "builderFee": "0.01",
            }
        ],
        datetime.now(timezone.utc),
    )

    assert count == 1
    assert pool.rows is not None
    assert len(pool.rows[0]) == 18

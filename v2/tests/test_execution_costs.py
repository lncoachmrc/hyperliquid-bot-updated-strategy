from datetime import datetime, timezone

from hyperliquid_v2.market_data.execution_costs import (
    normalize_fill,
    normalize_funding,
)


NOW = datetime(2026, 7, 25, tzinfo=timezone.utc)


def test_fill_normalization_measures_fee_bps_and_maker_taker():
    fill = normalize_fill(
        {
            "coin": "BTC",
            "px": "100",
            "sz": "10",
            "side": "B",
            "time": 1_800_000_000_000,
            "tid": 123,
            "oid": 456,
            "crossed": True,
            "fee": "0.35",
            "builderFee": "0.02",
            "feeToken": "USDC",
            "closedPnl": "0",
        },
        NOW,
    )

    assert fill is not None
    assert fill["fill_key"] == "tid:123"
    assert fill["notional_usd"] == 1_000.0
    assert fill["fee_bps"] == 3.5
    assert fill["builder_fee_usd"] == 0.02
    assert fill["is_maker"] is False
    assert fill["closed_pnl_usd"] == 0.0


def test_fill_fallback_key_is_deterministic():
    raw = {
        "coin": "ETH",
        "px": "2000",
        "sz": "0.5",
        "side": "A",
        "time": 1_800_000_000_000,
        "hash": "0xabc",
        "crossed": False,
        "fee": "0.1",
    }

    first = normalize_fill(raw, NOW)
    second = normalize_fill(raw, NOW)

    assert first is not None and second is not None
    assert first["fill_key"] == second["fill_key"]
    assert first["is_maker"] is True


def test_nested_funding_event_preserves_zero_values():
    funding = normalize_funding(
        {
            "time": 1_800_000_000_000,
            "delta": {
                "coin": "SOL",
                "fundingRate": "0",
                "szi": "5",
                "usdc": "0",
            },
        },
        NOW,
    )

    assert funding is not None
    assert funding["funding_rate"] == 0.0
    assert funding["funding_usd"] == 0.0


def test_malformed_public_events_are_ignored():
    assert normalize_fill(None, NOW) is None
    assert normalize_fill({"coin": "BTC"}, NOW) is None
    assert normalize_funding({"delta": {}}, NOW) is None

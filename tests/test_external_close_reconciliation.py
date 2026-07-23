from external_close_reconciliation import (
    canonical_order_id,
    normalize_fill_order_ids,
)
from performance_observability import match_external_close_fills


def test_numeric_order_ids_are_canonicalized_without_decimal_suffix():
    assert canonical_order_id("500903859646.0") == "500903859646"
    assert canonical_order_id(500903859646.0) == "500903859646"
    assert canonical_order_id(500903859646) == "500903859646"


def test_non_numeric_order_id_is_preserved():
    assert canonical_order_id("client-order-abc") == "client-order-abc"


def test_canonicalized_fill_matches_decimal_suffixed_expected_stop_id():
    fills = normalize_fill_order_ids(
        [
            {
                "coin": "SOL",
                "oid": 500903859646.0,
                "time": 1_100,
                "px": "77.486",
                "sz": "3.67",
                "side": "A",
                "dir": "Close Long",
                "fee": "0.10",
                "feeToken": "USDC",
                "closedPnl": "-1.78",
                "hash": "stop-fill",
            }
        ]
    )
    result = match_external_close_fills(
        fills,
        symbol="SOL",
        expected_position_side="long",
        expected_stop_order_id=canonical_order_id("500903859646.0"),
        window_start_ms=1_000,
        window_end_ms=2_000,
    )
    assert result["matched"] is True
    assert result["match_method"] == "stop_order_id"
    assert result["order_ids"] == ["500903859646"]

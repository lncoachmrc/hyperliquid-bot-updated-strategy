from copy import deepcopy
from datetime import datetime, timezone

import pytest

from performance_observability import (
    build_entry_opportunity_samples,
    match_external_close_fills,
    parse_stop_loss_events,
)


def _indicator(symbol="ETH", price=100.0):
    return {
        "ticker": symbol,
        "current": {"price": price},
        "strategy": {
            "strategy_version": "1.6.0",
            "regime": "adverse",
            "recommended_action": "hold_or_flat",
            "recommended_stop_loss_percent": 0.5,
            "tactical_intraday": {
                "price": price,
                "completed_bar_close_time": "2026-07-22T14:15:00+00:00",
            },
        },
    }


def _summary(block_reasons):
    return {
        "policy_version": "1.0",
        "evaluated": {
            "ETH": {
                "passed": not block_reasons,
                "block_reasons": block_reasons,
                "original_candidate": {
                    "recommended_action": "tactical_long_candidate",
                    "recommended_effective_exposure_before_drawdown": 0.25,
                    "execution_feasible": True,
                },
            }
        },
    }


def test_entry_sample_is_stable_non_mutating_and_has_exact_horizons():
    indicators = [_indicator()]
    summary = _summary(["distance_from_ema20_passed"])
    original_indicators = deepcopy(indicators)
    original_summary = deepcopy(summary)
    observed_at = datetime(2026, 7, 22, 14, 22, tzinfo=timezone.utc)

    samples = build_entry_opportunity_samples(
        indicators,
        summary,
        observed_at=observed_at,
    )

    assert indicators == original_indicators
    assert summary == original_summary
    assert len(samples) == 1
    sample = samples[0]
    assert sample["policy_outcome"] == "blocked"
    assert sample["baseline_price"] == 100.0
    assert sample["hypothetical_stop_loss_percent"] == 0.5
    assert sample["hypothetical_effective_exposure"] == 0.25
    assert sample["sample_key"].endswith(
        "ETH|2026-07-22T14:15:00+00:00"
    )
    assert (sample["target_15m_at"] - observed_at).total_seconds() == 900
    assert (sample["target_60m_at"] - observed_at).total_seconds() == 3600
    assert (sample["target_180m_at"] - observed_at).total_seconds() == 10800


def test_same_completed_bar_produces_same_sample_key():
    observed_at = datetime(2026, 7, 22, 14, 22, tzinfo=timezone.utc)
    first = build_entry_opportunity_samples(
        [_indicator(price=100.0)],
        _summary(["one_hour_extension_passed"]),
        observed_at=observed_at,
    )[0]
    second = build_entry_opportunity_samples(
        [_indicator(price=101.0)],
        _summary(["one_hour_extension_passed"]),
        observed_at=observed_at,
    )[0]
    assert first["sample_key"] == second["sample_key"]


def test_allowed_candidate_is_recorded_without_block_reasons():
    sample = build_entry_opportunity_samples(
        [_indicator()],
        _summary([]),
        observed_at=datetime(2026, 7, 22, 14, 22, tzinfo=timezone.utc),
    )[0]
    assert sample["policy_outcome"] == "allowed"
    assert sample["block_reasons"] == []


def test_sample_requires_completed_bar_and_price():
    indicator = _indicator()
    del indicator["strategy"]["tactical_intraday"]["completed_bar_close_time"]
    assert build_entry_opportunity_samples(
        [indicator],
        _summary(["distance_from_ema20_passed"]),
    ) == []


def test_stop_order_id_has_priority_and_partial_fills_are_aggregated():
    fills = [
        {
            "coin": "ETH",
            "oid": 999,
            "time": 1_010,
            "px": "1900",
            "sz": "0.10",
            "side": "A",
            "dir": "Close Long",
            "fee": "0.01",
            "feeToken": "USDC",
            "closedPnl": "-1.0",
            "hash": "a",
        },
        {
            "coin": "ETH",
            "oid": 999,
            "time": 1_020,
            "px": "1890",
            "sz": "0.20",
            "side": "A",
            "dir": "Close Long",
            "fee": "0.02",
            "feeToken": "USDC",
            "closedPnl": "-2.0",
            "hash": "b",
        },
        {
            "coin": "ETH",
            "oid": 111,
            "time": 1_015,
            "px": "1950",
            "sz": "0.30",
            "side": "A",
            "dir": "Close Long",
            "fee": "0.03",
            "closedPnl": "2.0",
            "hash": "other",
        },
    ]

    result = match_external_close_fills(
        fills,
        symbol="ETH",
        expected_position_side="long",
        expected_stop_order_id="999",
        window_start_ms=1_000,
        window_end_ms=2_000,
    )

    assert result["matched"] is True
    assert result["match_method"] == "stop_order_id"
    assert result["fill_count"] == 2
    assert result["total_filled_size"] == pytest.approx(0.30)
    assert result["avg_fill_price"] == pytest.approx(
        (1900 * 0.10 + 1890 * 0.20) / 0.30
    )
    assert result["total_fee"] == pytest.approx(0.03)
    assert result["total_closed_pnl"] == pytest.approx(-3.0)
    assert result["order_ids"] == ["999"]


def test_fallback_matches_closing_side_inside_window_only():
    fills = [
        {
            "coin": "BTC",
            "oid": 1,
            "time": 1_100,
            "px": "66000",
            "sz": "0.01",
            "side": "A",
            "dir": "Close Long",
            "hash": "good",
        },
        {
            "coin": "BTC",
            "oid": 2,
            "time": 900,
            "px": "65900",
            "sz": "0.01",
            "side": "A",
            "dir": "Close Long",
            "hash": "old",
        },
        {
            "coin": "SOL",
            "oid": 3,
            "time": 1_100,
            "px": "80",
            "sz": "1",
            "side": "A",
            "dir": "Close Long",
            "hash": "wrong-symbol",
        },
    ]
    result = match_external_close_fills(
        fills,
        symbol="BTC",
        expected_position_side="long",
        expected_stop_order_id=None,
        window_start_ms=1_000,
        window_end_ms=2_000,
    )
    assert result["matched"] is True
    assert result["match_method"] == "symbol_side_time_window"
    assert result["fill_count"] == 1
    assert result["fill_hashes"] == ["good"]


def test_duplicate_fill_payload_is_counted_once():
    fill = {
        "coin": "ETH",
        "oid": 999,
        "time": 1_010,
        "px": "1900",
        "sz": "0.10",
        "side": "A",
        "dir": "Close Long",
        "hash": "a",
    }
    result = match_external_close_fills(
        [fill, dict(fill)],
        symbol="ETH",
        expected_position_side="long",
        expected_stop_order_id="999",
        window_start_ms=1_000,
        window_end_ms=2_000,
    )
    assert result["fill_count"] == 1
    assert result["total_filled_size"] == pytest.approx(0.10)


def test_invalid_stop_payload_returns_empty_events():
    assert parse_stop_loss_events("not-json") == []
    assert parse_stop_loss_events("[]") == []

from datetime import datetime, timedelta, timezone

from hyperliquid_v2.market_data.features import Candle, FeatureSnapshot
from hyperliquid_v2.market_data.momentum import PumpMomentum
from hyperliquid_v2.opportunity_engine.failed_breakout import (
    FailedBreakoutEngine,
    ReplayPoint,
    replay_blocked_upside_breakout,
)


def _history(start_ms: int) -> list[Candle]:
    candles = []
    for index in range(20):
        price = 100.0 + index * 0.01
        candles.append(
            Candle(
                open_time_ms=start_ms + index * 900_000,
                close_time_ms=start_ms + (index + 1) * 900_000,
                interval="15m",
                open=price,
                high=price + 0.20,
                low=price - 0.20,
                close=price,
                volume=100.0,
                trades=10,
            )
        )
    return candles


def _feature(observed_at_ms: int, **changes) -> FeatureSnapshot:
    values = dict(
        symbol="BTC",
        observed_at_ms=observed_at_ms,
        mid_price=100.05,
        spread_bps=1.0,
        book_imbalance=-0.40,
        buy_aggression=0.30,
        sell_aggression=0.70,
        trade_notional_30s=1_000_000.0,
        price_velocity_bps_15s=-3.0,
        price_velocity_bps_60s=-8.0,
        price_acceleration_bps=-2.0,
        realized_vol_bps_60s=2.0,
        open_interest=1_000.0,
        open_interest_change_pct=-0.10,
        funding_rate=0.0,
        ema20_15m=100.0,
        ema50_15m=99.5,
        atr14_15m=1.0,
        rsi14_15m=55.0,
        volume_ratio_15m=1.0,
        donchian_high_20_15m=100.39,
        momentum_1h_pct=0.1,
        data_quality_score=1.0,
        data_quality_flags=(),
    )
    values.update(changes)
    return FeatureSnapshot(**values)


def _pump(**changes) -> PumpMomentum:
    values = dict(
        phase="reversal",
        continuation_probability=0.30,
        reversal_probability=0.75,
        price_velocity=-0.40,
        price_acceleration=-0.20,
        buy_aggression=0.30,
        sell_aggression=0.70,
        book_imbalance=-0.40,
        open_interest_confirmation=True,
        volume_climax_probability=0.40,
    )
    values.update(changes)
    return PumpMomentum(**values)


def test_upside_breakout_failure_creates_short_thesis():
    start = 1_800_000_000_000
    candles = _history(start)
    candles.extend(
        [
            Candle(
                start + 20 * 900_000,
                start + 21 * 900_000,
                "15m",
                100.20,
                101.20,
                100.10,
                100.80,
                150.0,
                20,
            ),
            Candle(
                start + 21 * 900_000,
                start + 22 * 900_000,
                "15m",
                100.80,
                100.90,
                99.90,
                100.10,
                160.0,
                20,
            ),
        ]
    )

    assessments = FailedBreakoutEngine().scan(
        _feature(candles[-1].close_time_ms + 1_000),
        _pump(),
        candles,
    )

    assert len(assessments) == 1
    assessment = assessments[0]
    assert assessment.candidate is True
    assert assessment.thesis is not None
    assert assessment.thesis.direction == "short"
    assert assessment.thesis.setup_family == "failed_breakout_reversal_short"
    assert assessment.event.entry_mode in {
        "retest_rejection",
        "failure_continuation",
    }
    assert assessment.event.confirmation_count >= 2


def test_downside_breakout_failure_creates_long_thesis():
    start = 1_800_000_000_000
    candles = _history(start)
    candles.extend(
        [
            Candle(
                start + 20 * 900_000,
                start + 21 * 900_000,
                "15m",
                100.00,
                100.10,
                99.00,
                99.50,
                150.0,
                20,
            ),
            Candle(
                start + 21 * 900_000,
                start + 22 * 900_000,
                "15m",
                99.50,
                100.00,
                99.40,
                99.90,
                160.0,
                20,
            ),
        ]
    )
    feature = _feature(
        candles[-1].close_time_ms + 1_000,
        mid_price=99.95,
        book_imbalance=0.40,
        buy_aggression=0.70,
        sell_aggression=0.30,
        price_velocity_bps_15s=3.0,
        price_velocity_bps_60s=8.0,
        price_acceleration_bps=2.0,
    )
    pump = _pump(
        phase="expansion",
        continuation_probability=0.75,
        reversal_probability=0.25,
        price_velocity=0.40,
        price_acceleration=0.20,
        buy_aggression=0.70,
        sell_aggression=0.30,
        book_imbalance=0.40,
    )

    assessments = FailedBreakoutEngine().scan(feature, pump, candles)

    assert len(assessments) == 1
    assessment = assessments[0]
    assert assessment.candidate is True
    assert assessment.thesis is not None
    assert assessment.thesis.direction == "long"
    assert assessment.thesis.setup_family == "failed_breakout_reversal_long"


def test_breakout_is_armed_until_a_completed_candle_proves_failure():
    start = 1_800_000_000_000
    candles = _history(start)
    candles.extend(
        [
            Candle(
                start + 20 * 900_000,
                start + 21 * 900_000,
                "15m",
                100.20,
                101.20,
                100.10,
                100.80,
                150.0,
                20,
            ),
            Candle(
                start + 21 * 900_000,
                start + 22 * 900_000,
                "15m",
                100.80,
                101.00,
                100.50,
                100.70,
                140.0,
                18,
            ),
        ]
    )

    assessments = FailedBreakoutEngine().scan(
        _feature(candles[-1].close_time_ms + 1_000),
        _pump(),
        candles,
    )

    assert len(assessments) == 1
    assert assessments[0].candidate is False
    assert assessments[0].event.status == "armed"
    assert assessments[0].event.failed_at is None


def test_replay_waits_for_failure_close_then_prices_short_path_net_of_costs():
    observed_at = datetime(2026, 7, 24, 7, 0, tzinfo=timezone.utc)
    sample = {
        "sample_key": "entry|BTC|breakout_continuation|example",
        "symbol": "BTC",
        "observed_at": observed_at,
        "baseline_price": 101.0,
        "payload": {
            "feature": {
                "donchian_high_20_15m": 100.5,
                "atr14_15m": 1.0,
            }
        },
    }
    points = []
    for index in range(13):
        timestamp = observed_at + timedelta(minutes=15 * index + 14)
        price = 101.0 if index == 0 else 100.4 - (index - 1) * 0.15
        points.append(
            ReplayPoint(
                observed_at=timestamp,
                price=price,
                payload={
                    "price_velocity_bps_60s": -5.0,
                    "price_acceleration_bps": -2.0,
                    "book_imbalance": -0.30,
                    "sell_aggression": 0.70,
                    "buy_aggression": 0.30,
                },
            )
        )

    result = replay_blocked_upside_breakout(sample, points)

    assert result is not None
    assert result.reversal_direction == "short"
    assert result.failed_at > observed_at
    assert result.cost_r > 0
    assert result.realized_net_r < result.gross_r
    assert result.exit_reason in {"target_reached", "time_stop_180m"}

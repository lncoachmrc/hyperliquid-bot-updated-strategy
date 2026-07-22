from hyperliquid_v2.domain.models import PositionPhase
from hyperliquid_v2.market_data.features import FeatureSnapshot
from hyperliquid_v2.market_data.momentum import PumpMomentum
from hyperliquid_v2.position_guardian.tracker import PositionTracker


def feature(mark=101.0):
    return FeatureSnapshot(
        symbol="BTC",
        observed_at_ms=1_800_000_000_000,
        mid_price=mark,
        spread_bps=1.0,
        book_imbalance=-0.3,
        buy_aggression=0.3,
        sell_aggression=0.7,
        trade_notional_30s=100000,
        price_velocity_bps_15s=3.0,
        price_velocity_bps_60s=10.0,
        price_acceleration_bps=-5.0,
        realized_vol_bps_60s=2.0,
        open_interest=1000,
        open_interest_change_pct=-0.1,
        funding_rate=0.0001,
        ema20_15m=100.0,
        ema50_15m=99.0,
        atr14_15m=1.0,
        rsi14_15m=62.0,
        volume_ratio_15m=1.0,
        donchian_high_20_15m=100.5,
        momentum_1h_pct=0.3,
        data_quality_score=1.0,
        data_quality_flags=(),
    )


def pump():
    return PumpMomentum(
        phase="exhaustion",
        continuation_probability=0.28,
        reversal_probability=0.72,
        price_velocity=0.2,
        price_acceleration=-0.5,
        buy_aggression=0.3,
        sell_aggression=0.7,
        book_imbalance=-0.3,
        open_interest_confirmation=False,
        volume_climax_probability=0.8,
    )


def test_tracker_detects_exhaustion_and_protects_green_position():
    tracker = PositionTracker()
    result = tracker.observe(
        {
            "symbol": "BTC",
            "side": "long",
            "entry_price": 100.0,
            "mark_price": 101.0,
            "size": 1.0,
        },
        feature(),
        pump(),
        stop_price=99.0,
        default_stop_pct=0.6,
        round_trip_cost_bps=10,
    )

    assert result.position_state.current_r == 1.0
    assert result.position_state.phase is PositionPhase.EXHAUSTION
    assert result.exit_assessment.close_review is True
    assert result.exit_assessment.dynamic_profit_floor_r is not None
    assert result.exit_assessment.ev_close_r > result.exit_assessment.ev_hold_r

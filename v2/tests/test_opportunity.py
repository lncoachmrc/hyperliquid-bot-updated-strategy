from hyperliquid_v2.market_data.features import FeatureSnapshot
from hyperliquid_v2.market_data.momentum import PumpMomentum
from hyperliquid_v2.opportunity_engine.engine import OpportunityEngine


def feature(**changes):
    values = dict(
        symbol="ETH",
        observed_at_ms=1_800_000_000_000,
        mid_price=101.0,
        spread_bps=1.0,
        book_imbalance=0.2,
        buy_aggression=0.65,
        sell_aggression=0.35,
        trade_notional_30s=100000,
        price_velocity_bps_15s=4.0,
        price_velocity_bps_60s=8.0,
        price_acceleration_bps=2.0,
        realized_vol_bps_60s=2.0,
        open_interest=1000,
        open_interest_change_pct=0.1,
        funding_rate=0.0001,
        ema20_15m=100.0,
        ema50_15m=99.0,
        atr14_15m=1.0,
        rsi14_15m=60.0,
        volume_ratio_15m=1.1,
        donchian_high_20_15m=100.8,
        momentum_1h_pct=0.4,
        data_quality_score=1.0,
        data_quality_flags=(),
    )
    values.update(changes)
    return FeatureSnapshot(**values)


def pump(**changes):
    values = dict(
        phase="expansion",
        continuation_probability=0.72,
        reversal_probability=0.28,
        price_velocity=0.4,
        price_acceleration=0.3,
        buy_aggression=0.65,
        sell_aggression=0.35,
        book_imbalance=0.2,
        open_interest_confirmation=True,
        volume_climax_probability=0.2,
    )
    values.update(changes)
    return PumpMomentum(**values)


def test_opportunity_engine_creates_explicit_thesis_without_chasing():
    assessment = OpportunityEngine().assess(feature(), pump(), (99.7, 99.8, 99.9))

    assert assessment.candidate is True
    assert assessment.thesis is not None
    assert assessment.thesis.setup_family == "breakout_continuation"
    assert assessment.stop_distance_pct > 0
    assert assessment.reward_risk >= 1.5


def test_opportunity_engine_blocks_exhaustion_even_when_trend_is_positive():
    assessment = OpportunityEngine().assess(
        feature(),
        pump(phase="exhaustion", continuation_probability=0.3, reversal_probability=0.7),
        (99.7, 99.8, 99.9),
    )

    assert assessment.candidate is False
    assert "pump_exhaustion_or_reversal_risk" in assessment.reasons

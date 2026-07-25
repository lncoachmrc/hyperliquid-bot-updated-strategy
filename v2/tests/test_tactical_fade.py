from hyperliquid_v2.market_data.features import FeatureSnapshot
from hyperliquid_v2.opportunity_engine.tactical_fade import (
    TacticalFadeShadow,
    constant_risk_model,
)


def feature(symbol="BTC", **changes):
    values = dict(
        symbol=symbol,
        observed_at_ms=1_800_000_000_000,
        mid_price=101.0,
        spread_bps=2.0,
        book_imbalance=0.2,
        buy_aggression=0.65,
        sell_aggression=0.35,
        trade_notional_30s=100_000.0,
        price_velocity_bps_15s=4.0,
        price_velocity_bps_60s=8.0,
        price_acceleration_bps=2.0,
        realized_vol_bps_60s=2.0,
        open_interest=1_000.0,
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
        completed_15m_open_time_ms=1_799_999_100_000,
        completed_15m_close_time_ms=1_800_000_000_000,
        completed_15m_close=101.0,
        macd_15m=0.8,
        previous_macd_15m=0.7,
        tactical_momentum_1h_pct=0.4,
        tactical_ema20_15m=100.0,
        tactical_ema50_15m=99.0,
        tactical_rsi14_15m=60.0,
        tactical_volume_ratio_15m=1.1,
        ma100_1d=95.0,
        ma200_1d=100.0,
        regime_1d="adverse",
        mark_price=101.0,
        oracle_price=100.95,
        mark_oracle_dislocation_bps=4.95,
        book_age_ms=1_000,
        asset_context_age_ms=1_000,
    )
    values.update(changes)
    return FeatureSnapshot(**values)


def test_fixed_fade_matches_unfiltered_adverse_tactical_candidate():
    assessment = TacticalFadeShadow().assess(feature())

    assert assessment.structural_candidate is True
    assert assessment.portfolio_eligible is True
    assert assessment.confirmations == 7
    assert assessment.stop_price > assessment.entry_price
    assert assessment.stop_distance_pct >= 0.5


def test_daily_regime_is_part_of_the_predeclared_hypothesis():
    assessment = TacticalFadeShadow().assess(
        feature(regime_1d="neutral")
    )

    assert assessment.structural_candidate is False
    assert "daily_regime_not_adverse" in assessment.reasons


def test_market_quality_does_not_hide_base_rate_but_blocks_portfolio():
    assessment = TacticalFadeShadow().assess(
        feature(spread_bps=25.0)
    )

    assert assessment.structural_candidate is True
    assert assessment.portfolio_eligible is False
    assert "spread_above_hard_limit" in assessment.reasons


def test_original_funding_and_dislocation_halts_remain_fail_closed():
    funding = TacticalFadeShadow().assess(
        feature(funding_rate=0.004)
    )
    dislocation = TacticalFadeShadow().assess(
        feature(mark_oracle_dislocation_bps=60.0)
    )

    assert funding.structural_candidate is True
    assert funding.portfolio_eligible is False
    assert "funding_above_hard_limit" in funding.reasons
    assert dislocation.portfolio_eligible is False
    assert (
        "mark_oracle_dislocation_above_hard_limit"
        in dislocation.reasons
    )


def test_cost_first_portfolio_tie_break_is_deterministic():
    engine = TacticalFadeShadow()
    btc = engine.assess(feature("BTC", spread_bps=3.0))
    eth = engine.assess(feature("ETH", spread_bps=1.0))
    sol = engine.assess(feature("SOL", spread_bps=2.0))

    selected = engine.select_portfolio_candidate((btc, eth, sol))

    assert selected is not None
    assert selected.symbol == "ETH"


def test_no_execution_contract_is_exposed():
    engine = TacticalFadeShadow()

    assert not hasattr(engine, "packet")
    assert not hasattr(engine, "execute")


def test_constant_risk_size_is_capped_by_existing_exposure_limit():
    assessment = TacticalFadeShadow().assess(feature())

    model = constant_risk_model(
        assessment,
        equity_usd=3_000.0,
        available_usd=2_900.0,
        equity_source="spot",
        target_risk_fraction=0.005,
        maximum_effective_exposure=0.50,
    )

    assert model["target_risk_usd"] == 14.5
    assert model["modeled_notional_usd"] <= 1_450.0
    assert model["modeled_risk_at_stop_usd"] <= 14.5

import pytest

from leverage_policy import (
    build_leverage_recommendation,
    select_exchange_leverage,
    tactical_risk_profile,
)
from strategy_config import DEFAULT_STRATEGY_CONFIG


CFG = DEFAULT_STRATEGY_CONFIG


def test_weak_adverse_tactical_setup_stays_at_one_x():
    profile = tactical_risk_profile(
        symbol="BTC",
        regime="adverse",
        donchian_positive_votes=1,
        tactical_confirmations=5,
        cfg=CFG,
    )
    assert profile["recommended_exchange_leverage"] == 1
    assert profile["effective_exposure_cap"] == pytest.approx(0.15)
    assert profile["risk_multiplier"] == pytest.approx(0.25)


def test_strong_eth_adverse_setup_can_use_three_x_with_half_equity_exposure():
    profile = tactical_risk_profile(
        symbol="ETH",
        regime="adverse",
        donchian_positive_votes=2,
        tactical_confirmations=7,
        cfg=CFG,
    )
    assert profile["recommended_exchange_leverage"] == 3
    assert profile["effective_exposure_cap"] == pytest.approx(0.50)

    recommendation = build_leverage_recommendation(
        action="tactical_long_candidate",
        symbol="ETH",
        regime="adverse",
        donchian_positive_votes=2,
        tactical_confirmations=7,
        effective_exposure=0.50,
        stop_loss_percent=1.0,
        tactical_profile=profile,
        live_max_leverage=50,
        cfg=CFG,
    )
    assert recommendation["exchange_leverage"] == 3
    assert recommendation["target_portion_of_balance"] == pytest.approx(1 / 6)
    assert recommendation["represented_effective_exposure"] == pytest.approx(0.50)
    assert recommendation["estimated_account_risk_at_stop"] == pytest.approx(0.005)
    assert recommendation["risk_budget_respected"] is True


def test_stop_risk_cap_reduces_exposure_before_leverage_representation():
    profile = tactical_risk_profile(
        symbol="ETH",
        regime="adverse",
        donchian_positive_votes=2,
        tactical_confirmations=7,
        cfg=CFG,
    )
    recommendation = build_leverage_recommendation(
        action="tactical_long_candidate",
        symbol="ETH",
        regime="adverse",
        donchian_positive_votes=2,
        tactical_confirmations=7,
        effective_exposure=1.0,
        stop_loss_percent=2.0,
        tactical_profile=profile,
        cfg=CFG,
    )
    assert recommendation["represented_effective_exposure"] == pytest.approx(0.25)
    assert recommendation["target_portion_of_balance"] == pytest.approx(1 / 12)
    assert recommendation["estimated_account_risk_at_stop"] == pytest.approx(0.005)


def test_sol_quality_cap_is_lower_than_eth():
    profile = tactical_risk_profile(
        symbol="SOL",
        regime="adverse",
        donchian_positive_votes=2,
        tactical_confirmations=7,
        cfg=CFG,
    )
    assert profile["effective_exposure_cap"] == pytest.approx(0.40)


def test_favorable_three_of_three_daily_setup_selects_five_x():
    leverage = select_exchange_leverage(
        action="long_candidate",
        regime="favorable",
        donchian_positive_votes=3,
        tactical_confirmations=7,
        cfg=CFG,
    )
    assert leverage == 5


def test_live_asset_max_leverage_always_prevails():
    profile = tactical_risk_profile(
        symbol="ETH",
        regime="adverse",
        donchian_positive_votes=2,
        tactical_confirmations=7,
        cfg=CFG,
    )
    recommendation = build_leverage_recommendation(
        action="tactical_long_candidate",
        symbol="ETH",
        regime="adverse",
        donchian_positive_votes=2,
        tactical_confirmations=7,
        effective_exposure=0.30,
        stop_loss_percent=1.0,
        tactical_profile=profile,
        live_max_leverage=2,
        cfg=CFG,
    )
    assert recommendation["exchange_leverage"] == 2
    assert recommendation["represented_effective_exposure"] == pytest.approx(0.30)
    assert recommendation["target_portion_of_balance"] == pytest.approx(0.15)

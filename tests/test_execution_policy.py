from execution_policy import (
    annotate_execution_feasibility,
    enrich_constraints_with_live_leverage,
)


def _indicator(recommended_effective_exposure):
    return {
        "ticker": "BTC",
        "strategy": {
            "recommended_action": "tactical_long_candidate",
            "represented_effective_exposure_before_drawdown": recommended_effective_exposure,
            "recommended_stop_loss_percent": 1.0,
            "regime": "adverse",
            "donchian_positive_votes": 1,
            "tactical_intraday": {"confirmations": 5},
            "tactical_risk_profile": {
                "recommended_exchange_leverage": 1,
            },
        },
    }


def _constraints():
    return {
        "BTC": {
            "available": True,
            "available_balance_usd": 3000.0,
            "minimum_executable_notional_usd": 65.5,
            "minimum_executable_effective_exposure": 65.5 / 3000.0,
            "minimum_executable_size": 0.001,
            "size_decimals": 3,
            "live_max_leverage": 50,
        }
    }


def test_subminimum_candidate_is_marked_non_executable():
    indicators = [_indicator(0.0113)]
    annotate_execution_feasibility(indicators, _constraints())
    strategy = indicators[0]["strategy"]
    assert strategy["execution_feasible"] is False
    assert (
        strategy["execution_feasibility"]["reason"]
        == "final_order_below_exchange_minimum"
    )


def test_candidate_above_minimum_is_executable():
    indicators = [_indicator(0.03)]
    annotate_execution_feasibility(indicators, _constraints())
    strategy = indicators[0]["strategy"]
    assert strategy["execution_feasible"] is True
    assert strategy["execution_feasibility"]["reason"] == "executable"
    assert strategy["execution_feasibility"]["final_exchange_leverage"] == 1
    assert strategy["execution_feasibility"]["risk_budget_respected"] is True


def test_drawdown_can_make_an_otherwise_valid_order_non_executable():
    indicators = [_indicator(0.03)]
    annotate_execution_feasibility(
        indicators,
        _constraints(),
        portfolio_drawdown_factor=0.5,
    )
    strategy = indicators[0]["strategy"]
    assert strategy["execution_feasible"] is False
    assert strategy["execution_feasibility"]["final_effective_exposure"] == 0.015


def test_live_max_leverage_is_attached_from_hyperliquid_meta():
    constraints = _constraints()
    constraints["BTC"].pop("live_max_leverage")
    enrich_constraints_with_live_leverage(
        constraints,
        {"universe": [{"name": "BTC", "maxLeverage": 50}]},
    )
    assert constraints["BTC"]["live_max_leverage"] == 50
    assert constraints["BTC"]["bot_absolute_max_leverage"] == 10

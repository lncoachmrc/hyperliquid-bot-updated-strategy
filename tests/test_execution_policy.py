from execution_policy import annotate_execution_feasibility


def _indicator(recommended_effective_exposure):
    return {
        "ticker": "BTC",
        "strategy": {
            "recommended_action": "tactical_long_candidate",
            "represented_effective_exposure_before_drawdown": recommended_effective_exposure,
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

import json
from datetime import datetime, timezone

from prophet_shadow import attach_prophet_shadow_evaluations


def _indicator():
    return {
        "ticker": "ETH",
        "strategy": {
            "strategy_version": "1.6.0",
            "recommended_action": "tactical_long_candidate",
            "execution_feasible": True,
            "recommended_effective_exposure_before_drawdown": 0.2,
            "recommended_exchange_leverage_before_drawdown": 3,
            "tactical_intraday": {
                "completed_bar_close_time": "2026-07-22T10:00:00+00:00"
            },
        },
    }


def _forecasts(change_15m, change_1h):
    return json.dumps(
        [
            {
                "Ticker": "ETH",
                "Timeframe": "Prossimi 15 Minuti",
                "Horizon Minutes": 15,
                "Variazione %": change_15m,
                "Forecast Generated At": 1784714400000,
                "Timestamp Previsione": 1784715300000,
                "Minutes To Target": 15,
            },
            {
                "Ticker": "ETH",
                "Timeframe": "Prossima Ora",
                "Horizon Minutes": 60,
                "Variazione %": change_1h,
                "Forecast Generated At": 1784714400000,
                "Timestamp Previsione": 1784718000000,
                "Minutes To Target": 60,
            },
        ]
    )


def test_negative_1h_is_recorded_as_shadow_veto_without_live_mutation():
    indicators = [_indicator()]
    original = dict(indicators[0]["strategy"])
    summary = attach_prophet_shadow_evaluations(
        indicators,
        _forecasts(0.1, -0.2),
        evaluated_at=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
    )
    strategy = indicators[0]["strategy"]
    shadow = strategy["prophet_shadow"]
    assert shadow["hypothetical_policy"]["would_veto_entry"] is True
    assert shadow["operational"] is False
    assert strategy["recommended_action"] == original["recommended_action"]
    assert strategy["recommended_effective_exposure_before_drawdown"] == 0.2
    assert strategy["recommended_exchange_leverage_before_drawdown"] == 3
    assert summary["observation_count"] == 1


def test_positive_1h_negative_15m_is_shadow_timing_delay():
    indicators = [_indicator()]
    attach_prophet_shadow_evaluations(indicators, _forecasts(-0.08, 0.25))
    policy = indicators[0]["strategy"]["prophet_shadow"]["hypothetical_policy"]
    assert policy["would_delay_entry"] is True
    assert policy["would_veto_entry"] is False


def test_positive_both_is_shadow_allow():
    indicators = [_indicator()]
    attach_prophet_shadow_evaluations(indicators, _forecasts(0.05, 0.25))
    policy = indicators[0]["strategy"]["prophet_shadow"]["hypothetical_policy"]
    assert policy["verdict"] == "would_allow"

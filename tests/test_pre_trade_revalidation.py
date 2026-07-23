from copy import deepcopy

from pre_trade_revalidation import apply_live_breakout_revalidation


def _decision(operation="open"):
    return {
        "operation": operation,
        "symbol": "SOL",
        "direction": "long",
        "target_portion_of_balance": 0.048,
        "leverage": 2,
        "stop_loss_percent": 0.601,
        "reason": "test",
        "decision_source": "llm",
    }


def _indicator(vote_class="weak_1of3", regime="adverse", passed=True):
    return {
        "ticker": "SOL",
        "strategy": {
            "regime": regime,
            "recommended_action": "tactical_long_candidate",
            "execution_feasible": True,
            "adverse_entry_quality": {
                "vote_class": vote_class,
                "passed": passed,
                "previous_1h_high": 78.038,
            },
        },
    }


def test_weak_adverse_open_is_blocked_when_live_mid_falls_below_breakout():
    decision = _decision()
    indicators = [_indicator()]
    original_decision = deepcopy(decision)
    original_indicators = deepcopy(indicators)

    result = apply_live_breakout_revalidation(
        decision,
        indicators,
        {"SOL": "77.9695"},
    )

    assert decision == original_decision
    assert indicators == original_indicators
    assert result["operation"] == "hold"
    assert result["target_portion_of_balance"] == 0.0
    assert result["pre_trade_revalidation_adjusted"] is True
    assert result["pre_trade_revalidation"]["passed"] is False
    assert result["pre_trade_revalidation"]["block_reason"] == (
        "pre_trade_breakout_revalidation_failed"
    )
    assert result["pre_trade_original_decision"]["operation"] == "open"


def test_weak_adverse_open_passes_only_when_live_mid_is_strictly_above_high():
    result = apply_live_breakout_revalidation(
        _decision(),
        [_indicator()],
        {"SOL": "78.039"},
    )
    assert result["operation"] == "open"
    assert result["pre_trade_revalidation"]["passed"] is True
    assert result["pre_trade_revalidation"]["live_mid"] == 78.039


def test_equal_live_mid_fails_closed():
    result = apply_live_breakout_revalidation(
        _decision(),
        [_indicator()],
        {"SOL": "78.038"},
    )
    assert result["operation"] == "hold"


def test_missing_live_mid_fails_closed_for_applicable_setup():
    result = apply_live_breakout_revalidation(
        _decision(),
        [_indicator()],
        {},
        live_mid_error="temporary api error",
    )
    assert result["operation"] == "hold"
    assert result["pre_trade_revalidation"]["live_mid_error"] == (
        "temporary api error"
    )


def test_aligned_adverse_candidate_is_not_changed():
    result = apply_live_breakout_revalidation(
        _decision(),
        [_indicator(vote_class="aligned_2of3_or_3of3")],
        {},
    )
    assert result["operation"] == "open"
    assert result["pre_trade_revalidation"]["applicable"] is False


def test_hold_is_never_changed():
    result = apply_live_breakout_revalidation(
        _decision(operation="hold"),
        [_indicator()],
        {},
    )
    assert result["operation"] == "hold"
    assert "pre_trade_revalidation_adjusted" not in result

from decision_gate import deterministic_hold, should_invoke_llm


def _indicator(action):
    return {"ticker": "ETH", "strategy": {"recommended_action": action}}


def test_flat_account_without_candidate_skips_llm():
    invoke, reason = should_invoke_llm(
        [_indicator("close_if_open_otherwise_hold")],
        {"open_positions": []},
        "[]",
    )
    assert invoke is False
    assert reason == "flat_account_and_no_actionable_candidate"


def test_tactical_candidate_invokes_llm():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate")],
        {"open_positions": []},
        "[]",
    )
    assert invoke is True
    assert "actionable_candidates:ETH" == reason


def test_open_position_always_invokes_llm_for_management():
    invoke, reason = should_invoke_llm(
        [_indicator("close_if_open_otherwise_hold")],
        {"open_positions": [{"symbol": "BTC", "side": "long"}]},
        "[]",
    )
    assert invoke is True
    assert reason == "open_position_requires_management"


def test_serialized_stop_loss_event_invokes_llm():
    invoke, reason = should_invoke_llm(
        [_indicator("hold_or_flat")],
        {"open_positions": []},
        '[{"symbol":"SOL"}]',
    )
    assert invoke is True
    assert reason == "recent_stop_loss_requires_review"


def test_deterministic_prefilter_can_only_hold():
    decision = deterministic_hold("test")
    assert decision["operation"] == "hold"
    assert decision["target_portion_of_balance"] == 0.0
    assert decision["leverage"] == 1

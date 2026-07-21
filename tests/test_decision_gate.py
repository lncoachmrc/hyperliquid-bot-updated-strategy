from decision_gate import deterministic_hold, should_invoke_llm


def _indicator(action, *, feasible=True):
    return {
        "ticker": "ETH",
        "strategy": {
            "recommended_action": action,
            "execution_feasible": feasible,
        },
    }


def test_flat_account_without_candidate_skips_llm():
    invoke, reason = should_invoke_llm(
        [_indicator("close_if_open_otherwise_hold")],
        {"open_positions": []},
        "[]",
        {},
    )
    assert invoke is False
    assert reason == "flat_account_and_no_executable_candidate"


def test_executable_tactical_candidate_invokes_llm_without_management_state():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate")],
        {"open_positions": []},
        "[]",
        {},
    )
    assert invoke is True
    assert reason == "actionable_candidates:ETH"


def test_new_flat_candidate_invokes_llm_immediately():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate")],
        {"open_positions": []},
        "[]",
        {
            "new_candidate_symbols": ["ETH"],
            "llm_review_due": False,
        },
    )
    assert invoke is True
    assert reason == "new_actionable_candidates:ETH"


def test_reentry_cooldown_blocks_candidate_before_llm_call():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate")],
        {"open_positions": []},
        "[]",
        {
            "new_candidate_symbols": [],
            "reentry_blocked_symbols": ["ETH"],
            "llm_review_due": True,
        },
    )
    assert invoke is False
    assert reason == "flat_account_and_no_executable_candidate"


def test_persistent_flat_candidate_waits_for_scheduled_review():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate")],
        {"open_positions": []},
        "[]",
        {
            "new_candidate_symbols": [],
            "llm_review_due": False,
        },
    )
    assert invoke is False
    assert reason == "persistent_candidate_review_not_due"


def test_persistent_flat_candidate_invokes_when_review_is_due():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate")],
        {"open_positions": []},
        "[]",
        {
            "new_candidate_symbols": [],
            "llm_review_due": True,
        },
    )
    assert invoke is True
    assert reason == "persistent_candidate_scheduled_review"


def test_non_executable_candidate_skips_llm():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate", feasible=False)],
        {"open_positions": []},
        "[]",
        {},
    )
    assert invoke is False
    assert reason == "flat_account_and_no_executable_candidate"


def test_stable_open_position_skips_llm_until_review_due():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate")],
        {"open_positions": [{"symbol": "ETH", "side": "long"}]},
        "[]",
        {
            "immediate_llm_reasons": [],
            "llm_review_due": False,
            "preferred_hold_symbol": "ETH",
        },
    )
    assert invoke is False
    assert reason == "stable_open_position_review_not_due"


def test_position_exit_event_invokes_llm_immediately():
    invoke, reason = should_invoke_llm(
        [_indicator("close_if_open_otherwise_hold")],
        {"open_positions": [{"symbol": "ETH", "side": "long"}]},
        "[]",
        {
            "immediate_llm_reasons": ["ETH:exit_hysteresis_confirmed"],
            "llm_review_due": False,
        },
    )
    assert invoke is True
    assert reason.startswith("position_event:")


def test_profit_protection_event_invokes_llm_immediately():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate")],
        {"open_positions": [{"symbol": "ETH", "side": "long"}]},
        "[]",
        {
            "immediate_llm_reasons": ["ETH:profit_protection_exit"],
            "llm_review_due": False,
        },
    )
    assert invoke is True
    assert "profit_protection_exit" in reason


def test_scheduled_position_review_invokes_llm():
    invoke, reason = should_invoke_llm(
        [_indicator("tactical_long_candidate")],
        {"open_positions": [{"symbol": "ETH", "side": "long"}]},
        "[]",
        {"immediate_llm_reasons": [], "llm_review_due": True},
    )
    assert invoke is True
    assert reason == "stable_position_scheduled_review"


def test_serialized_stop_loss_event_invokes_llm():
    invoke, reason = should_invoke_llm(
        [_indicator("hold_or_flat")],
        {"open_positions": []},
        '[{"symbol":"SOL"}]',
        {},
    )
    assert invoke is True
    assert reason == "recent_stop_loss_requires_review"


def test_deterministic_prefilter_holds_an_open_symbol():
    decision = deterministic_hold(
        "test",
        management_state={"preferred_hold_symbol": "ETH"},
    )
    assert decision["operation"] == "hold"
    assert decision["symbol"] == "ETH"
    assert decision["target_portion_of_balance"] == 0.0
    assert decision["leverage"] == 1

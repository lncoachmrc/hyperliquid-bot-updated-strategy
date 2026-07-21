from decision_gate import should_invoke_llm


def _indicator():
    return {
        "ticker": "ETH",
        "strategy": {
            "recommended_action": "tactical_long_candidate",
            "execution_feasible": True,
        },
    }


def test_material_candidate_upgrade_bypasses_flat_review_cooldown():
    invoke, reason = should_invoke_llm(
        [_indicator()],
        {"open_positions": []},
        "[]",
        {
            "new_candidate_symbols": [],
            "candidate_upgrade_symbols": ["ETH"],
            "llm_review_due": False,
        },
    )

    assert invoke is True
    assert reason == "candidate_quality_upgrade:ETH"


def test_no_upgrade_keeps_persistent_candidate_on_normal_cadence():
    invoke, reason = should_invoke_llm(
        [_indicator()],
        {"open_positions": []},
        "[]",
        {
            "new_candidate_symbols": [],
            "candidate_upgrade_symbols": [],
            "llm_review_due": False,
        },
    )

    assert invoke is False
    assert reason == "persistent_candidate_review_not_due"

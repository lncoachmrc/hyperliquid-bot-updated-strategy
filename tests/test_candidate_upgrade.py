from candidate_upgrade import annotate_candidate_quality_upgrades


def _strategy(
    *,
    confirmations=5,
    votes=1,
    leverage=1,
    exposure=0.10,
    action="tactical_long_candidate",
    feasible=True,
):
    return {
        "recommended_action": action,
        "execution_feasible": feasible,
        "donchian_positive_votes": votes,
        "tactical_intraday": {"confirmations": confirmations, "candidate": True},
        "tactical_risk_profile": {"quality": f"q-{confirmations}-{votes}-{leverage}"},
        "execution_feasibility": {
            "final_exchange_leverage": leverage,
            "final_effective_exposure": exposure,
        },
    }


def _indicator(**kwargs):
    return {"ticker": "ETH", "strategy": _strategy(**kwargs)}


def _history(**kwargs):
    return {
        "history_by_symbol": {
            "ETH": [
                {
                    "created_at": "2026-07-21T12:00:00+00:00",
                    "strategy": _strategy(**kwargs),
                }
            ]
        }
    }


def _management(*, blocked=None):
    return {
        "reentry_blocked_symbols": list(blocked or []),
        "immediate_llm_reasons": [],
        "llm_review_due": False,
    }


def test_confirmation_upgrade_5_to_6_triggers_immediate_review():
    state = _management()
    annotate_candidate_quality_upgrades(
        [_indicator(confirmations=6, leverage=2, exposure=0.12)],
        {"open_positions": []},
        _history(confirmations=5, leverage=1, exposure=0.10),
        state,
    )

    assert state["candidate_upgrade_symbols"] == ["ETH"]
    reasons = state["candidate_upgrade_state_by_symbol"]["ETH"]["reasons"]
    assert "confirmations:5->6" in reasons
    assert "leverage_tier:1x->2x" in reasons
    assert any("candidate_quality_upgrade" in item for item in state["immediate_llm_reasons"])


def test_confirmation_upgrade_6_to_7_triggers_immediate_review():
    state = _management()
    annotate_candidate_quality_upgrades(
        [_indicator(confirmations=7, leverage=3, exposure=0.20)],
        {"open_positions": []},
        _history(confirmations=6, leverage=2, exposure=0.18),
        state,
    )

    assert state["candidate_upgrade_symbols"] == ["ETH"]
    reasons = state["candidate_upgrade_state_by_symbol"]["ETH"]["reasons"]
    assert "confirmations:6->7" in reasons
    assert "leverage_tier:2x->3x" in reasons


def test_donchian_vote_upgrade_triggers_even_without_confirmation_change():
    state = _management()
    annotate_candidate_quality_upgrades(
        [_indicator(confirmations=6, votes=2, leverage=2, exposure=0.10)],
        {"open_positions": []},
        _history(confirmations=6, votes=1, leverage=2, exposure=0.10),
        state,
    )

    assert state["candidate_upgrade_symbols"] == ["ETH"]
    reasons = state["candidate_upgrade_state_by_symbol"]["ETH"]["reasons"]
    assert "donchian_votes:1->2" in reasons


def test_twenty_percent_exposure_upgrade_triggers_review():
    state = _management()
    annotate_candidate_quality_upgrades(
        [_indicator(confirmations=6, leverage=2, exposure=0.1201)],
        {"open_positions": []},
        _history(confirmations=6, leverage=2, exposure=0.10),
        state,
    )

    assert state["candidate_upgrade_symbols"] == ["ETH"]
    reasons = state["candidate_upgrade_state_by_symbol"]["ETH"]["reasons"]
    assert any(reason.startswith("effective_exposure:") for reason in reasons)


def test_small_persistent_change_does_not_bypass_review_cadence():
    state = _management()
    annotate_candidate_quality_upgrades(
        [_indicator(confirmations=6, leverage=2, exposure=0.115)],
        {"open_positions": []},
        _history(confirmations=6, leverage=2, exposure=0.10),
        state,
    )

    assert state["candidate_upgrade_symbols"] == []
    assert state["immediate_llm_reasons"] == []


def test_reentry_blocked_symbol_does_not_trigger_quality_upgrade():
    state = _management(blocked=["ETH"])
    annotate_candidate_quality_upgrades(
        [_indicator(confirmations=7, votes=2, leverage=3, exposure=0.30)],
        {"open_positions": []},
        _history(confirmations=5, votes=1, leverage=1, exposure=0.10),
        state,
    )

    assert state["candidate_upgrade_symbols"] == []


def test_open_symbol_does_not_trigger_entry_quality_upgrade():
    state = _management()
    annotate_candidate_quality_upgrades(
        [_indicator(confirmations=7, votes=2, leverage=3, exposure=0.30)],
        {"open_positions": [{"symbol": "ETH", "side": "long"}]},
        _history(confirmations=5, votes=1, leverage=1, exposure=0.10),
        state,
    )

    assert state["candidate_upgrade_symbols"] == []

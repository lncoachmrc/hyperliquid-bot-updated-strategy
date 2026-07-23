from copy import deepcopy

from severe_weakness_shadow import build_severe_weakness_exit_shadow


def _management(confirmations=2, current_r=-0.278, age=29.9, weak_bars=1):
    return {
        "positions": {
            "SOL": {
                "side": "long",
                "regime": "adverse",
                "position_mode": "tactical",
                "opened_at": "2026-07-22T22:20:40+00:00",
                "entry_price": 77.97,
                "mark_price": 77.8395,
                "position_age_minutes": age,
                "current_r": current_r,
                "tactical_confirmations": confirmations,
                "consecutive_weak_bars": weak_bars,
                "current_completed_15m_bar": "2026-07-22T22:30:00+00:00",
                "exit_authorized": False,
                "management_status": "minimum_hold_protection",
            }
        }
    }


def test_severe_confirmation_collapse_is_recorded_as_shadow_only():
    management = _management()
    original = deepcopy(management)

    result = build_severe_weakness_exit_shadow(management)

    assert management == original
    assert result["operational"] is False
    assert result["triggered_symbols"] == ["SOL"]
    observation = result["observations"]["SOL"]
    assert observation["triggered"] is True
    assert observation["hypothetical_action"] == "close"
    assert observation["hypothetical_exit_price"] == 77.8395
    assert observation["live_exit_authorized_unchanged"] is False


def test_three_confirmations_do_not_trigger():
    result = build_severe_weakness_exit_shadow(_management(confirmations=3))
    assert result["triggered_symbols"] == []
    assert result["observations"]["SOL"]["triggered"] is False


def test_small_loss_does_not_trigger():
    result = build_severe_weakness_exit_shadow(_management(current_r=-0.10))
    assert result["triggered_symbols"] == []


def test_position_must_have_first_completed_weak_bar():
    result = build_severe_weakness_exit_shadow(_management(weak_bars=0))
    assert result["triggered_symbols"] == []


def test_non_adverse_position_is_not_evaluated():
    management = _management()
    management["positions"]["SOL"]["regime"] = "favorable"
    result = build_severe_weakness_exit_shadow(management)
    assert result["observation_count"] == 0

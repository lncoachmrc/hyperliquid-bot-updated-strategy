from datetime import datetime, timedelta, timezone

from position_management import build_position_management_state


NOW = datetime(2026, 7, 21, 5, 0, tzinfo=timezone.utc)


def _indicator(confirmations, *, candidate, invalidations=None, action=None):
    return {
        "ticker": "ETH",
        "strategy": {
            "regime": "adverse",
            "recommended_action": action
            or (
                "tactical_long_candidate"
                if candidate
                else "close_if_open_otherwise_hold"
            ),
            "invalidations": list(invalidations or []),
            "execution_feasible": True,
            "tactical_intraday": {
                "confirmations": confirmations,
                "candidate": candidate,
            },
        },
    }


def _history(previous_confirmations, *, last_llm_minutes_ago=10, age_minutes=40):
    return {
        "history_by_symbol": {
            "ETH": [
                {
                    "created_at": NOW - timedelta(minutes=10),
                    "strategy": {
                        "regime": "adverse",
                        "recommended_action": "close_if_open_otherwise_hold",
                        "execution_feasible": True,
                        "tactical_intraday": {
                            "confirmations": previous_confirmations,
                            "candidate": previous_confirmations >= 5,
                        },
                    },
                }
            ]
        },
        "opened_at_by_symbol": {"ETH": NOW - timedelta(minutes=age_minutes)},
        "last_llm_at": NOW - timedelta(minutes=last_llm_minutes_ago),
    }


def _account():
    return {
        "open_positions": [
            {"symbol": "ETH", "side": "long", "pnl_usd": 0.1}
        ]
    }


def test_four_confirmations_is_warning_not_exit():
    state = build_position_management_state(
        [_indicator(4, candidate=False)],
        _account(),
        _history(5),
        now=NOW,
    )
    eth = state["positions"]["ETH"]
    assert eth["management_status"] == "warning"
    assert eth["exit_authorized"] is False
    assert state["eligible_close_symbols"] == []


def test_three_confirmations_for_two_cycles_authorizes_exit_after_minimum_hold():
    state = build_position_management_state(
        [_indicator(3, candidate=False)],
        _account(),
        _history(3, age_minutes=40),
        now=NOW,
    )
    eth = state["positions"]["ETH"]
    assert eth["consecutive_weak_cycles"] >= 2
    assert eth["exit_authorized"] is True
    assert "ETH" in state["eligible_close_symbols"]
    assert state["immediate_llm_reasons"] == [
        "ETH:exit_hysteresis_confirmed"
    ]


def test_minimum_hold_blocks_non_hard_exit():
    state = build_position_management_state(
        [_indicator(2, candidate=False)],
        _account(),
        _history(2, age_minutes=20),
        now=NOW,
    )
    eth = state["positions"]["ETH"]
    assert eth["minimum_hold_met"] is False
    assert eth["management_status"] == "minimum_hold_protection"
    assert eth["exit_authorized"] is False


def test_hard_invalidation_bypasses_minimum_hold_and_hysteresis():
    state = build_position_management_state(
        [
            _indicator(
                6,
                candidate=True,
                invalidations=["spread_or_orderbook_filter_halt"],
            )
        ],
        _account(),
        _history(6, age_minutes=5),
        now=NOW,
    )
    eth = state["positions"]["ETH"]
    assert eth["minimum_hold_met"] is False
    assert eth["exit_authorized"] is True
    assert eth["management_status"] == "hard_exit"


def test_stable_position_review_is_due_only_after_30_minutes():
    recent = build_position_management_state(
        [_indicator(7, candidate=True)],
        _account(),
        _history(7, last_llm_minutes_ago=10),
        now=NOW,
    )
    due = build_position_management_state(
        [_indicator(7, candidate=True)],
        _account(),
        _history(7, last_llm_minutes_ago=31),
        now=NOW,
    )
    assert recent["llm_review_due"] is False
    assert due["llm_review_due"] is True

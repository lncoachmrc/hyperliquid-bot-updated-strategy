from datetime import datetime, timedelta, timezone

from position_management import build_position_management_state


NOW = datetime(2026, 7, 21, 5, 0, tzinfo=timezone.utc)
CURRENT_BAR = datetime(2026, 7, 21, 4, 45, tzinfo=timezone.utc).isoformat()
PREVIOUS_BAR = datetime(2026, 7, 21, 4, 30, tzinfo=timezone.utc).isoformat()


def _indicator(
    confirmations,
    *,
    candidate,
    invalidations=None,
    action=None,
    bar_id=CURRENT_BAR,
    volume_ratio=1.0,
    breakout=False,
    bar_high=101.0,
):
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
                "completed_bar_open_time": bar_id,
                "volume_ratio": volume_ratio,
                "breakout_above_previous_1h_high": breakout,
                "bar_high": bar_high,
            },
        },
    }


def _history(
    previous_confirmations,
    *,
    previous_bar_id=PREVIOUS_BAR,
    last_llm_minutes_ago=10,
    age_minutes=40,
    last_close_minutes_ago=None,
    stop_loss_percent=None,
    max_observed_price=None,
):
    context = {
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
                            "completed_bar_open_time": previous_bar_id,
                        },
                    },
                }
            ]
        },
        "opened_at_by_symbol": {"ETH": NOW - timedelta(minutes=age_minutes)},
        "open_stop_loss_percent_by_symbol": {},
        "max_observed_price_by_symbol": {},
        "last_close_at_by_symbol": {},
        "last_close_price_by_symbol": {},
        "last_llm_at": NOW - timedelta(minutes=last_llm_minutes_ago),
    }
    if last_close_minutes_ago is not None:
        context["last_close_at_by_symbol"]["ETH"] = NOW - timedelta(
            minutes=last_close_minutes_ago
        )
    if stop_loss_percent is not None:
        context["open_stop_loss_percent_by_symbol"]["ETH"] = stop_loss_percent
    if max_observed_price is not None:
        context["max_observed_price_by_symbol"]["ETH"] = max_observed_price
    return context


def _account(*, open_position=True, entry=100.0, mark=100.2):
    if not open_position:
        return {"open_positions": []}
    return {
        "open_positions": [
            {
                "symbol": "ETH",
                "side": "long",
                "entry_price": entry,
                "mark_price": mark,
                "pnl_usd": mark - entry,
            }
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


def test_same_completed_bar_cannot_be_counted_twice_for_exit():
    state = build_position_management_state(
        [_indicator(3, candidate=False, bar_id=CURRENT_BAR)],
        _account(),
        _history(3, previous_bar_id=CURRENT_BAR, age_minutes=40),
        now=NOW,
    )
    eth = state["positions"]["ETH"]
    assert eth["consecutive_weak_bars"] == 1
    assert eth["exit_authorized"] is False


def test_two_distinct_weak_15m_bars_authorize_exit_after_minimum_hold():
    state = build_position_management_state(
        [_indicator(3, candidate=False, bar_id=CURRENT_BAR)],
        _account(),
        _history(3, previous_bar_id=PREVIOUS_BAR, age_minutes=40),
        now=NOW,
    )
    eth = state["positions"]["ETH"]
    assert eth["consecutive_weak_bars"] == 2
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


def test_profit_protection_authorizes_exit_after_large_mfe_giveback():
    state = build_position_management_state(
        [_indicator(6, candidate=True, bar_high=101.8)],
        _account(entry=100.0, mark=100.3),
        _history(
            6,
            age_minutes=10,
            stop_loss_percent=1.0,
            max_observed_price=102.0,
        ),
        now=NOW,
    )
    eth = state["positions"]["ETH"]
    assert eth["maximum_favorable_excursion_r"] == 2.0
    assert round(eth["current_r"], 6) == 0.3
    assert eth["profit_protection_exit_ready"] is True
    assert eth["exit_authorized"] is True
    assert eth["management_status"] == "profit_protection_exit"
    assert state["immediate_llm_reasons"] == ["ETH:profit_protection_exit"]


def test_recent_close_blocks_normal_reentry_for_30_minutes():
    state = build_position_management_state(
        [_indicator(6, candidate=True)],
        _account(open_position=False),
        _history(3, last_close_minutes_ago=10),
        now=NOW,
    )
    assert state["reentry_blocked_symbols"] == ["ETH"]
    assert state["new_candidate_symbols"] == []
    assert state["reentry_state_by_symbol"]["ETH"]["cooldown_active"] is True


def test_exceptional_7of7_volume_breakout_can_override_reentry_cooldown():
    state = build_position_management_state(
        [
            _indicator(
                7,
                candidate=True,
                volume_ratio=1.3,
                breakout=True,
            )
        ],
        _account(open_position=False),
        _history(3, last_close_minutes_ago=10),
        now=NOW,
    )
    assert state["reentry_blocked_symbols"] == []
    assert state["reentry_override_symbols"] == ["ETH"]
    assert state["new_candidate_symbols"] == ["ETH"]
    assert "ETH:breakout_reentry_override" in state["immediate_llm_reasons"]


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

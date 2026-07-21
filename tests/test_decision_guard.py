from decision_guard import apply_decision_guard


def _account():
    return {
        "open_positions": [
            {"symbol": "ETH", "side": "long", "pnl_usd": 0.1}
        ]
    }


def _indicators(*, executable=True):
    return [
        {
            "ticker": "BTC",
            "strategy": {
                "recommended_action": "tactical_long_candidate",
                "execution_feasible": executable,
            },
        },
        {
            "ticker": "ETH",
            "strategy": {
                "recommended_action": "close_if_open_otherwise_hold",
                "execution_feasible": False,
            },
        },
    ]


def _management(*, eligible=None):
    return {
        "preferred_hold_symbol": "ETH",
        "eligible_close_symbols": list(eligible or []),
    }


def test_hold_on_flat_asset_is_rebound_to_open_position():
    decision = {
        "operation": "hold",
        "symbol": "SOL",
        "direction": "long",
        "target_portion_of_balance": 0.0,
        "leverage": 1,
        "stop_loss_percent": 1.0,
        "reason": "hold",
    }
    guarded = apply_decision_guard(
        decision, _account(), _indicators(), _management()
    )
    assert guarded["operation"] == "hold"
    assert guarded["symbol"] == "ETH"
    assert guarded["decision_guard_adjusted"] is True


def test_close_is_blocked_until_hysteresis_authorizes_it():
    decision = {
        "operation": "close",
        "symbol": "ETH",
        "direction": "long",
        "target_portion_of_balance": 1.0,
        "leverage": 1,
        "stop_loss_percent": 1.0,
        "reason": "close now",
    }
    guarded = apply_decision_guard(
        decision, _account(), _indicators(), _management(eligible=[])
    )
    assert guarded["operation"] == "hold"
    assert guarded["symbol"] == "ETH"
    assert guarded["llm_original_decision"]["operation"] == "close"


def test_authorized_close_is_preserved():
    decision = {
        "operation": "close",
        "symbol": "ETH",
        "direction": "long",
        "target_portion_of_balance": 1.0,
        "leverage": 1,
        "stop_loss_percent": 1.0,
        "reason": "confirmed exit",
    }
    guarded = apply_decision_guard(
        decision, _account(), _indicators(), _management(eligible=["ETH"])
    )
    assert guarded["operation"] == "close"
    assert "decision_guard_adjusted" not in guarded


def test_non_executable_open_is_blocked():
    decision = {
        "operation": "open",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.01,
        "leverage": 1,
        "stop_loss_percent": 1.0,
        "reason": "open",
    }
    guarded = apply_decision_guard(
        decision,
        {"open_positions": []},
        _indicators(executable=False),
        {"preferred_hold_symbol": "BTC", "eligible_close_symbols": []},
    )
    assert guarded["operation"] == "hold"
    assert guarded["decision_guard_adjusted"] is True

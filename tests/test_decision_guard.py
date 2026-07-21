import pytest

from decision_guard import apply_decision_guard


def _account():
    return {
        "balance_usd": 3000.0,
        "open_positions": [
            {
                "symbol": "ETH",
                "side": "long",
                "size": 0.1,
                "mark_price": 1900.0,
                "pnl_usd": 0.1,
            }
        ],
    }


def _btc_strategy(*, executable=True, final_exposure=0.5, leverage=3):
    return {
        "recommended_action": "tactical_long_candidate",
        "execution_feasible": executable,
        "recommended_stop_loss_percent": 1.0,
        "execution_feasibility": {
            "final_effective_exposure": final_exposure,
            "final_exchange_leverage": leverage,
            "live_max_leverage": 50,
            "bot_absolute_max_leverage": 10,
        },
    }


def _indicators(*, executable=True, final_exposure=0.5, leverage=3):
    return [
        {
            "ticker": "BTC",
            "strategy": _btc_strategy(
                executable=executable,
                final_exposure=final_exposure,
                leverage=leverage,
            ),
        },
        {
            "ticker": "ETH",
            "strategy": {
                "recommended_action": "close_if_open_otherwise_hold",
                "execution_feasible": False,
            },
        },
    ]


def _management(*, eligible=None, blocked=None):
    return {
        "preferred_hold_symbol": "ETH",
        "eligible_close_symbols": list(eligible or []),
        "reentry_blocked_symbols": list(blocked or []),
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
        {"balance_usd": 3000.0, "open_positions": []},
        _indicators(executable=False),
        {"preferred_hold_symbol": "BTC", "eligible_close_symbols": []},
    )
    assert guarded["operation"] == "hold"
    assert guarded["decision_guard_adjusted"] is True


def test_recently_closed_symbol_open_is_blocked_even_if_llm_requests_it():
    decision = {
        "operation": "open",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.1,
        "leverage": 3,
        "stop_loss_percent": 1.0,
        "reason": "reenter immediately",
    }
    guarded = apply_decision_guard(
        decision,
        {"balance_usd": 3000.0, "open_positions": []},
        _indicators(final_exposure=0.3, leverage=3),
        {
            "preferred_hold_symbol": "BTC",
            "eligible_close_symbols": [],
            "reentry_blocked_symbols": ["BTC"],
        },
    )
    assert guarded["operation"] == "hold"
    assert guarded["symbol"] == "BTC"
    assert "re-entry cooldown" in guarded["decision_guard_reason"]


def test_open_is_represented_with_policy_leverage_without_changing_exposure():
    decision = {
        "operation": "open",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.5,
        "leverage": 1,
        "stop_loss_percent": 1.0,
        "reason": "open strong setup",
    }
    guarded = apply_decision_guard(
        decision,
        {"balance_usd": 3000.0, "open_positions": []},
        _indicators(final_exposure=0.5, leverage=3),
        {"preferred_hold_symbol": "BTC", "eligible_close_symbols": []},
    )
    assert guarded["operation"] == "open"
    assert guarded["leverage"] == 3
    assert guarded["target_portion_of_balance"] == pytest.approx(1 / 6)
    assert (
        guarded["target_portion_of_balance"] * guarded["leverage"]
        == pytest.approx(0.5)
    )
    assert guarded["dynamic_leverage_execution"]["estimated_account_risk_at_stop"] == pytest.approx(0.005)


def test_excessive_llm_leverage_and_exposure_are_clamped():
    decision = {
        "operation": "open",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.1,
        "leverage": 10,
        "stop_loss_percent": 2.0,
        "reason": "too aggressive",
    }
    guarded = apply_decision_guard(
        decision,
        {"balance_usd": 3000.0, "open_positions": []},
        _indicators(final_exposure=0.5, leverage=3),
        {"preferred_hold_symbol": "BTC", "eligible_close_symbols": []},
    )
    assert guarded["leverage"] == 3
    assert guarded["stop_loss_percent"] == 1.0
    assert guarded["target_portion_of_balance"] == pytest.approx(1 / 6)
    assert guarded["decision_guard_adjusted"] is True


def test_portfolio_gross_cap_reduces_new_effective_exposure():
    account = {
        "balance_usd": 3000.0,
        "open_positions": [
            {
                "symbol": "ETH",
                "size": 2.0,
                "mark_price": 2100.0,
                "entry_price": 2000.0,
                "side": "long",
            }
        ],
    }
    # Existing gross exposure is 1.4x, leaving only 0.1x under the 1.5x cap.
    decision = {
        "operation": "open",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.5,
        "leverage": 1,
        "stop_loss_percent": 1.0,
        "reason": "open",
    }
    guarded = apply_decision_guard(
        decision,
        account,
        _indicators(final_exposure=0.5, leverage=3),
        {"preferred_hold_symbol": "ETH", "eligible_close_symbols": []},
    )
    assert guarded["leverage"] == 3
    assert guarded["target_portion_of_balance"] == pytest.approx(0.1 / 3)
    assert guarded["dynamic_leverage_execution"]["final_effective_exposure"] == pytest.approx(0.1)

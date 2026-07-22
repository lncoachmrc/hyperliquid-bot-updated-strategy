from profit_protection_overlay import apply_adverse_profit_protection


def test_adverse_position_gets_early_fee_adjusted_protection():
    state = {
        "positions": {
            "ETH": {
                "regime": "adverse",
                "position_mode": "tactical",
                "initial_stop_loss_percent": 0.5,
                "maximum_favorable_excursion_r": 0.8,
                "current_r": 0.15,
                "profit_protection_exit_ready": False,
                "exit_authorized": False,
                "hard_invalidations": [],
            }
        },
        "eligible_close_symbols": [],
        "immediate_llm_reasons": [],
        "rules": {},
    }
    updated = apply_adverse_profit_protection(state)
    eth = updated["positions"]["ETH"]
    assert eth["exit_authorized"] is True
    assert eth["profit_protection_exit_ready"] is True
    assert eth["profit_protection_floor_r"] == 0.2
    assert "ETH" in updated["eligible_close_symbols"]
    assert "ETH:adverse_profit_protection_exit" in updated["immediate_llm_reasons"]


def test_favorable_daily_position_keeps_base_policy_untouched():
    state = {
        "positions": {
            "BTC": {
                "regime": "favorable",
                "position_mode": "daily",
                "initial_stop_loss_percent": 1.0,
                "maximum_favorable_excursion_r": 0.9,
                "current_r": 0.1,
                "profit_protection_exit_ready": False,
                "exit_authorized": False,
                "hard_invalidations": [],
            }
        },
        "eligible_close_symbols": [],
        "immediate_llm_reasons": [],
        "rules": {},
    }
    updated = apply_adverse_profit_protection(state)
    assert updated["positions"]["BTC"]["exit_authorized"] is False
    assert updated["eligible_close_symbols"] == []

from entry_quality_policy import apply_strict_adverse_entry_policy


def _indicator(
    *,
    confirmations=7,
    votes=1,
    volume=1.3,
    price=100.0,
    ema20=99.5,
    atr=1.0,
    previous_high=99.0,
    breakout=True,
):
    return {
        "ticker": "BTC",
        "strategy": {
            "strategy_version": "1.6.0",
            "regime": "adverse",
            "recommended_action": "tactical_long_candidate",
            "recommended_stop_loss_percent": 0.5,
            "recommended_effective_exposure_before_drawdown": 0.2,
            "represented_effective_exposure_before_drawdown": 0.2,
            "recommended_exchange_leverage_before_drawdown": 2,
            "recommended_balance_portion_before_drawdown": 0.1,
            "estimated_account_risk_at_stop_before_drawdown": 0.001,
            "donchian_positive_votes": votes,
            "execution_feasible": True,
            "execution_feasibility": {
                "candidate_action": True,
                "final_effective_exposure": 0.2,
                "final_exchange_leverage": 2,
                "final_target_portion_of_balance": 0.1,
                "estimated_account_risk_at_stop": 0.001,
                "recommended_order_notional_usd": 600,
                "reason": "executable",
            },
            "tactical_intraday": {
                "confirmations": confirmations,
                "volume_ratio": volume,
                "price": price,
                "ema20": ema20,
                "atr14": atr,
                "momentum_1h_pct": 1.0,
                "bar_high": price + 0.4,
                "bar_low": price - 0.4,
                "previous_1h_high": previous_high,
                "breakout_above_previous_1h_high": breakout,
            },
        },
    }


def test_weak_adverse_6of7_is_blocked():
    indicators = [_indicator(confirmations=6)]
    summary = apply_strict_adverse_entry_policy(indicators, {"open_positions": []})
    strategy = indicators[0]["strategy"]
    assert summary["blocked_symbols"] == ["BTC"]
    assert strategy["recommended_action"] == "hold_or_flat"
    assert strategy["execution_feasible"] is False
    assert "confirmations_passed" in strategy["adverse_entry_quality"]["block_reasons"]


def test_weak_adverse_7of7_volume_breakout_can_pass():
    indicators = [_indicator()]
    summary = apply_strict_adverse_entry_policy(indicators, {"open_positions": []})
    strategy = indicators[0]["strategy"]
    assert summary["allowed_symbols"] == ["BTC"]
    assert strategy["recommended_action"] == "tactical_long_candidate"
    assert strategy["execution_feasible"] is True
    assert strategy["adverse_entry_quality"]["passed"] is True


def test_excessive_distance_from_ema20_blocks_chasing():
    indicators = [
        _indicator(price=102.0, ema20=99.5, atr=1.0, previous_high=101.0)
    ]
    apply_strict_adverse_entry_policy(indicators, {"open_positions": []})
    quality = indicators[0]["strategy"]["adverse_entry_quality"]
    assert quality["anti_chase_passed"] is False
    assert "distance_from_ema20_passed" in quality["block_reasons"]


def test_existing_correlated_long_blocks_new_adverse_entry():
    indicators = [_indicator()]
    account = {
        "open_positions": [
            {"symbol": "ETH", "side": "long", "size": 0.2, "mark_price": 1900}
        ]
    }
    summary = apply_strict_adverse_entry_policy(indicators, account)
    quality = indicators[0]["strategy"]["adverse_entry_quality"]
    assert summary["blocked_symbols"] == ["BTC"]
    assert quality["correlated_position_limit_reached"] is True
    assert (
        "maximum_correlated_adverse_long_positions_reached"
        in quality["block_reasons"]
    )

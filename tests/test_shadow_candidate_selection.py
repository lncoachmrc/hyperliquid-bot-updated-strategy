from shadow_candidate_selection import flat_account_shadow_candidates


def _indicators():
    return [
        {
            "ticker": "ETH",
            "strategy": {
                "recommended_action": "tactical_long_candidate",
                "execution_feasible": True,
            },
        }
    ]


def test_flat_account_candidate_is_sampled():
    assert flat_account_shadow_candidates(
        _indicators(),
        {"open_positions": []},
    ) == ["ETH"]


def test_any_open_position_disables_shadow_forecasting():
    account = {
        "open_positions": [
            {"symbol": "BTC", "side": "long", "size": 0.01}
        ]
    }
    assert flat_account_shadow_candidates(_indicators(), account) == []


def test_zero_size_position_does_not_block_flat_sampling():
    account = {
        "open_positions": [
            {"symbol": "BTC", "side": "long", "size": 0.0}
        ]
    }
    assert flat_account_shadow_candidates(_indicators(), account) == ["ETH"]

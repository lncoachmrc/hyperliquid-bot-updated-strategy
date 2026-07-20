from hyperliquid_trader import HyperLiquidTrader, _extract_spot_usdc_balance


class FakeInfo:
    def __init__(self, mode, spot_state):
        self.mode = mode
        self.spot_state = spot_state

    def post(self, path, payload):
        assert path == "/info"
        assert payload["type"] == "userAbstraction"
        return self.mode

    def spot_user_state(self, address):
        return self.spot_state


def make_trader(mode, spot_state):
    trader = object.__new__(HyperLiquidTrader)
    trader.account_address = "0x0000000000000000000000000000000000000001"
    trader.info = FakeInfo(mode, spot_state)
    return trader


def test_extract_spot_usdc_balance_uses_total_minus_hold_for_available():
    found, total, hold, available = _extract_spot_usdc_balance(
        {"balances": [{"coin": "USDC", "token": 0, "total": "3002.06", "hold": "12.06"}]}
    )

    assert found is True
    assert float(total) == 3002.06
    assert float(hold) == 12.06
    assert float(available) == 2990.0


def test_unified_account_uses_spot_usdc_instead_of_perp_margin_summary():
    trader = make_trader(
        "unifiedAccount",
        {"balances": [{"coin": "USDC", "token": 0, "total": "3002.06", "hold": "2.06"}]},
    )
    perp_state = {
        "marginSummary": {"accountValue": "10.120071"},
        "withdrawable": "5.0",
    }

    details = trader._get_account_balance_details(perp_state=perp_state)

    assert details["account_mode"] == "unifiedAccount"
    assert details["balance_usd"] == 3002.06
    assert details["available_balance_usd"] == 3000.0
    assert details["perp_account_value"] == 10.120071
    assert details["balance_source"] == "spotClearinghouseState.USDC.total"


def test_portfolio_margin_uses_spot_usdc_conservatively():
    trader = make_trader(
        "portfolioMargin",
        {"balances": [{"coin": "USDC", "token": 0, "total": "1500", "hold": "100"}]},
    )
    perp_state = {
        "marginSummary": {"accountValue": "20"},
        "withdrawable": "10",
    }

    details = trader._get_account_balance_details(perp_state=perp_state)

    assert details["balance_usd"] == 1500.0
    assert details["available_balance_usd"] == 1400.0


def test_standard_account_keeps_perp_margin_summary_as_source():
    trader = make_trader(
        "disabled",
        {"balances": [{"coin": "USDC", "token": 0, "total": "3002.06", "hold": "0"}]},
    )
    perp_state = {
        "marginSummary": {"accountValue": "1250"},
        "withdrawable": "1000",
    }

    details = trader._get_account_balance_details(perp_state=perp_state)

    assert details["balance_usd"] == 1250.0
    assert details["available_balance_usd"] == 1000.0
    assert details["balance_source"] == "clearinghouseState.marginSummary.accountValue"


def test_unified_account_falls_back_safely_when_usdc_is_missing():
    trader = make_trader("unifiedAccount", {"balances": []})
    perp_state = {
        "marginSummary": {"accountValue": "10.12"},
        "withdrawable": "8.0",
    }

    details = trader._get_account_balance_details(perp_state=perp_state)

    assert details["balance_usd"] == 10.12
    assert details["available_balance_usd"] == 8.0
    assert "fallback_missing_spot_usdc" in details["balance_source"]

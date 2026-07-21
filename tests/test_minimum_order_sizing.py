from decimal import Decimal

import pytest

from hyperliquid_trader import HyperLiquidTrader


class _Info:
    def all_mids(self):
        return {"BTC": "65500"}


class _Exchange:
    def market_open(self, *args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("market_open must not be called for a sub-minimum order")


def _trader():
    trader = HyperLiquidTrader.__new__(HyperLiquidTrader)
    trader.meta = {"universe": [{"name": "BTC", "szDecimals": 3}]}
    trader.info = _Info()
    trader.exchange = _Exchange()
    trader._get_account_balance_details = lambda: {
        "available_balance_usd": 3000.0,
        "available_balance_source": "test",
        "account_mode": "unifiedAccount",
    }
    trader.set_leverage_for_symbol = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("leverage must not change for a skipped order")
    )
    return trader


def test_btc_minimum_size_respects_precision_and_ten_dollar_notional():
    minimum, decimals, increment = HyperLiquidTrader._minimum_executable_size(
        {"name": "BTC", "szDecimals": 3},
        Decimal("65500"),
    )
    assert decimals == 3
    assert increment == Decimal("0.001")
    assert minimum == Decimal("0.001")


def test_subminimum_order_is_skipped_not_upsized():
    decision = {
        "operation": "open",
        "symbol": "BTC",
        "direction": "long",
        "target_portion_of_balance": 0.0113,
        "leverage": 1,
        "stop_loss_percent": 1.0,
        "reason": "test",
    }

    result = _trader().execute_signal(decision)

    assert result["status"] == "skipped"
    assert result["requested_size"] < result["minimum_executable_size"]
    assert result["minimum_executable_size"] == pytest.approx(0.001)
    assert result["requested_notional_usd"] == pytest.approx(33.9)

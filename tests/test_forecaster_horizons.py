from datetime import datetime, timezone

from forecaster import HyperliquidForecaster, normalized_target_time


def test_normalized_targets_are_exact_offsets_not_clock_boundaries():
    generated = datetime(2026, 7, 22, 10, 7, 31, tzinfo=timezone.utc)
    assert (normalized_target_time(generated, 15) - generated).total_seconds() == 900
    assert (normalized_target_time(generated, 60) - generated).total_seconds() == 3600
    assert normalized_target_time(generated, 15).minute == 22
    assert normalized_target_time(generated, 60).minute == 7


class _LiveInfo:
    def all_mids(self):
        return {"ETH": "1913.75"}


class _BrokenInfo:
    def all_mids(self):
        raise RuntimeError("temporary endpoint failure")


def _forecaster_with(info):
    forecaster = HyperliquidForecaster.__new__(HyperliquidForecaster)
    forecaster.info = info
    forecaster.last_prices = {}
    return forecaster


def test_live_mid_is_used_as_return_baseline():
    forecaster = _forecaster_with(_LiveInfo())
    price, source = forecaster._current_mid("ETH", 1900.0)
    assert price == 1913.75
    assert source == "live_mid"


def test_completed_close_is_safe_fallback_when_live_mid_fails():
    forecaster = _forecaster_with(_BrokenInfo())
    price, source = forecaster._current_mid("ETH", 1900.0)
    assert price == 1900.0
    assert source == "last_completed_candle_close"

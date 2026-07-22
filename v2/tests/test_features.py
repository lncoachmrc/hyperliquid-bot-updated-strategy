from hyperliquid_v2.market_data.features import FeatureEngine


def candle(symbol, interval, index, close, volume=100):
    start = index * 900_000
    return {
        "s": symbol,
        "i": interval,
        "t": start,
        "T": start + 899_999,
        "o": close - 0.2,
        "h": close + 0.4,
        "l": close - 0.5,
        "c": close,
        "v": volume,
        "n": 10,
    }


def test_feature_engine_builds_market_microstructure_and_completed_candle_features():
    engine = FeatureEngine(("BTC",))
    for index in range(60):
        engine.update_candle(candle("BTC", "15m", index, 100 + index * 0.1, 100 + index))
    for index in range(4):
        row = candle("BTC", "1h", index, 100 + index)
        row["i"] = "1h"
        engine.update_candle(row)
    now = 60 * 900_000
    engine.update_mid(now - 60_000, {"BTC": 105.5})
    engine.update_mid(now, {"BTC": 106.0})
    engine.update_book({
        "coin": "BTC",
        "time": now,
        "levels": [
            [{"px": "105.9", "sz": "4"}],
            [{"px": "106.1", "sz": "2"}],
        ],
    })
    engine.update_trades([
        {"coin": "BTC", "time": now - 1000, "side": "B", "px": "106", "sz": "2"},
        {"coin": "BTC", "time": now - 500, "side": "A", "px": "106", "sz": "1"},
    ])
    engine.update_asset_context({"coin": "BTC", "ctx": {"openInterest": "1000", "funding": "0.0001"}}, now)

    snapshot = engine.snapshot("BTC", now)

    assert snapshot is not None
    assert snapshot.ema20_15m is not None
    assert snapshot.ema50_15m is not None
    assert snapshot.atr14_15m is not None
    assert snapshot.rsi14_15m is not None
    assert snapshot.buy_aggression == 2 / 3
    assert snapshot.book_imbalance > 0
    assert snapshot.spread_bps > 0
    assert snapshot.open_interest == 1000
    assert snapshot.data_quality_score > 0.8

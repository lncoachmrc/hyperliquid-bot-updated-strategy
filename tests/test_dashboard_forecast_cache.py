from dashboard_forecast_cache import merge_dashboard_forecasts


def _forecast(symbol: str, timeframe: str, prediction: float):
    return {
        "Ticker": symbol,
        "Timeframe": timeframe,
        "Ultimo Prezzo": prediction - 1,
        "Previsione": prediction,
        "Limite Inferiore": prediction - 2,
        "Limite Superiore": prediction + 2,
        "Variazione %": 0.1,
    }


def test_cached_six_cards_are_kept_when_current_is_missing():
    cached = [
        _forecast("BTC", "Prossimi 15 Minuti", 101),
        _forecast("BTC", "Prossima Ora", 102),
        _forecast("ETH", "Prossimi 15 Minuti", 201),
        _forecast("ETH", "Prossima Ora", 202),
        _forecast("SOL", "Prossimi 15 Minuti", 301),
        _forecast("SOL", "Prossima Ora", 302),
    ]

    merged = merge_dashboard_forecasts(None, cached)

    assert [(item["Ticker"], item["Timeframe"]) for item in merged] == [
        ("BTC", "Prossimi 15 Minuti"),
        ("ETH", "Prossimi 15 Minuti"),
        ("SOL", "Prossimi 15 Minuti"),
        ("BTC", "Prossima Ora"),
        ("ETH", "Prossima Ora"),
        ("SOL", "Prossima Ora"),
    ]


def test_current_forecast_overrides_only_matching_cached_card():
    cached = [
        _forecast("BTC", "Prossimi 15 Minuti", 101),
        _forecast("BTC", "Prossima Ora", 102),
        _forecast("ETH", "Prossimi 15 Minuti", 201),
        _forecast("ETH", "Prossima Ora", 202),
        _forecast("SOL", "Prossimi 15 Minuti", 301),
        _forecast("SOL", "Prossima Ora", 302),
    ]
    current = [_forecast("ETH", "Prossimi 15 Minuti", 999)]

    merged = merge_dashboard_forecasts(current, cached)

    assert len(merged) == 6
    eth_15m = next(
        item
        for item in merged
        if item["Ticker"] == "ETH" and "15" in item["Timeframe"]
    )
    assert eth_15m["Previsione"] == 999


def test_unknown_timeframe_is_not_filtered_out():
    unknown = _forecast("BTC", "Prossime 4 Ore", 123)

    merged = merge_dashboard_forecasts([unknown], [])

    assert merged == [unknown]

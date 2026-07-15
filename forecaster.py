import warnings
from datetime import datetime, timezone

import pandas as pd
from hyperliquid.info import Info
from hyperliquid.utils import constants
from prophet import Prophet

warnings.filterwarnings("ignore")


class HyperliquidForecaster:
    def __init__(self, testnet: bool = True):
        base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.info = Info(base_url, skip_ws=True)
        self.last_prices = {}

    def _fetch_candles(self, coin: str, interval: str, limit: int) -> pd.DataFrame:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        interval_ms = {"15m": 15 * 60_000, "1h": 60 * 60_000}[interval]
        start_ms = now_ms - limit * interval_ms
        data = self.info.candles_snapshot(
            name=coin,
            interval=interval,
            startTime=start_ms,
            endTime=now_ms,
        )
        if not data:
            raise RuntimeError(f"No candles for {coin} {interval}")
        frame = pd.DataFrame(data)
        frame["ds"] = pd.to_datetime(frame["t"], unit="ms", utc=True).dt.tz_convert(None)
        frame["y"] = frame["c"].astype(float)
        return frame[["ds", "y"]].sort_values("ds").reset_index(drop=True)

    def forecast(self, coin: str, interval: str) -> tuple:
        if interval == "15m":
            frame = self._fetch_candles(coin, "15m", limit=300)
            frequency = "15min"
        else:
            frame = self._fetch_candles(coin, "1h", limit=500)
            frequency = "h"
        last_price = frame["y"].iloc[-1]
        model = Prophet(daily_seasonality=True, weekly_seasonality=True)
        model.fit(frame)
        future = model.make_future_dataframe(periods=1, freq=frequency)
        forecast = model.predict(future)
        return forecast.tail(1)[["ds", "yhat", "yhat_lower", "yhat_upper"]], last_price

    def forecast_many(self, tickers: list, intervals=("15m", "1h")):
        results = []
        for coin in tickers:
            for interval in intervals:
                try:
                    forecast_data, last_price = self.forecast(coin, interval)
                    forecast = forecast_data.iloc[0]
                    variation = ((forecast["yhat"] - last_price) / last_price) * 100
                    timeframe = (
                        "Prossimi 15 Minuti" if interval == "15m" else "Prossima Ora"
                    )
                    results.append(
                        {
                            "Ticker": coin,
                            "Timeframe": timeframe,
                            "Ultimo Prezzo": round(last_price, 2),
                            "Previsione": round(forecast["yhat"], 2),
                            "Limite Inferiore": round(forecast["yhat_lower"], 2),
                            "Limite Superiore": round(forecast["yhat_upper"], 2),
                            "Variazione %": round(variation, 2),
                            "Timestamp Previsione": forecast["ds"],
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append(
                        {
                            "Ticker": coin,
                            "Timeframe": (
                                "Prossimi 15 Minuti"
                                if interval == "15m"
                                else "Prossima Ora"
                            ),
                            "Ultimo Prezzo": None,
                            "Previsione": None,
                            "Limite Inferiore": None,
                            "Limite Superiore": None,
                            "Variazione %": None,
                            "Timestamp Previsione": None,
                            "error": str(exc),
                        }
                    )
        return results

    def get_predictions_summary(self) -> pd.DataFrame:
        if not hasattr(self, "_last_results"):
            return pd.DataFrame()
        return pd.DataFrame(self._last_results)

    def get_crypto_forecasts(self, tickers: list):
        self._last_results = self.forecast_many(tickers, intervals=("15m", "1h"))
        frame = pd.DataFrame(self._last_results)
        if "error" in frame.columns:
            frame = frame.drop("error", axis=1)
        return frame.to_string(index=False)


def get_hyperliquid_forecasts(tickers=["BTC", "ETH", "SOL"], testnet=True):
    return HyperliquidForecaster(testnet=testnet).get_crypto_forecasts(tickers)


def get_crypto_forecasts(tickers=["BTC", "ETH", "SOL"], testnet=True):
    try:
        forecaster = HyperliquidForecaster(testnet=testnet)
        results = forecaster.forecast_many(tickers)
        frame = pd.DataFrame(results)
        return frame.to_string(index=False), frame.to_json(orient="records")
    except Exception:  # noqa: BLE001
        return None, None

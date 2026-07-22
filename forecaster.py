import warnings
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

warnings.filterwarnings("ignore")


def normalized_target_time(
    generated_at: datetime,
    horizon_minutes: int,
) -> datetime:
    """Return an exact UTC +15m/+60m target, never a calendar boundary."""
    if horizon_minutes not in {15, 60}:
        raise ValueError("horizon_minutes must be 15 or 60")
    value = generated_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc) + timedelta(minutes=horizon_minutes)


def _epoch_ms(value: datetime | pd.Timestamp) -> int:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return int(timestamp.timestamp() * 1000)


class HyperliquidForecaster:
    def __init__(self, testnet: bool = True):
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.info = Info(base_url, skip_ws=True)
        self.last_prices = {}

    def _fetch_candles(self, coin: str, interval: str, limit: int) -> pd.DataFrame:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        interval_ms = {"15m": 15 * 60_000, "1h": 60 * 60_000}[interval]
        start_ms = now_ms - (limit + 2) * interval_ms
        data = self.info.candles_snapshot(
            name=coin,
            interval=interval,
            startTime=start_ms,
            endTime=now_ms,
        )
        if not data:
            raise RuntimeError(f"No candles for {coin} {interval}")
        frame = pd.DataFrame(data)
        frame["open_time_ms"] = frame["t"].astype("int64")
        frame["close_time_ms"] = frame["open_time_ms"] + interval_ms
        frame = frame.loc[frame["close_time_ms"] <= now_ms].copy()
        if frame.empty:
            raise RuntimeError(f"No completed candles for {coin} {interval}")
        frame["ds"] = pd.to_datetime(
            frame["open_time_ms"], unit="ms", utc=True
        ).dt.tz_convert(None)
        frame["y"] = frame["c"].astype(float)
        return (
            frame[["ds", "y", "open_time_ms", "close_time_ms"]]
            .sort_values("ds")
            .tail(limit)
            .reset_index(drop=True)
        )

    def _current_mid(self, coin: str, fallback: float) -> tuple[float, str]:
        """Use the live Hyperliquid mid as the forecast-return baseline.

        The completed candle remains a safe fallback if the live-mid endpoint is
        temporarily unavailable. A per-batch cache prevents duplicate calls for
        the 15m and 1h models of the same coin.
        """
        cached = self.last_prices.get(coin)
        if isinstance(cached, tuple) and len(cached) == 2:
            return float(cached[0]), str(cached[1])

        try:
            mids = self.info.all_mids()
            live_mid = float((mids or {}).get(coin))
            if live_mid > 0:
                result = (live_mid, "live_mid")
                self.last_prices[coin] = result
                return result
        except Exception:  # noqa: BLE001
            pass

        result = (float(fallback), "last_completed_candle_close")
        self.last_prices[coin] = result
        return result

    def forecast(
        self,
        coin: str,
        interval: str,
        *,
        generated_at: Optional[datetime] = None,
    ) -> tuple[pd.DataFrame, float, dict]:
        # Lazy import keeps pure horizon tests lightweight while production still
        # uses the installed Prophet package.
        from prophet import Prophet

        horizon_minutes = 15 if interval == "15m" else 60
        limit = 300 if interval == "15m" else 500
        frame = self._fetch_candles(coin, interval, limit=limit)
        completed_close_price = float(frame["y"].iloc[-1])
        completed_close_timestamp_ms = int(frame["close_time_ms"].iloc[-1])

        generated = generated_at or datetime.now(timezone.utc)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        generated = generated.astimezone(timezone.utc)
        target = normalized_target_time(generated, horizon_minutes)
        target_naive = pd.Timestamp(target).tz_convert(None)

        current_price, source_price_kind = self._current_mid(
            coin,
            completed_close_price,
        )
        source_price_timestamp_ms = (
            _epoch_ms(generated)
            if source_price_kind == "live_mid"
            else completed_close_timestamp_ms
        )

        model = Prophet(daily_seasonality=True, weekly_seasonality=True)
        model.fit(frame[["ds", "y"]])
        forecast = model.predict(pd.DataFrame({"ds": [target_naive]}))
        metadata = {
            "generated_at": generated,
            "target_at": target,
            "horizon_minutes": horizon_minutes,
            "source_price_timestamp_ms": source_price_timestamp_ms,
            "source_price_kind": source_price_kind,
            "completed_candle_close_price": completed_close_price,
            "completed_candle_close_timestamp_ms": completed_close_timestamp_ms,
        }
        return (
            forecast.tail(1)[["ds", "yhat", "yhat_lower", "yhat_upper"]],
            current_price,
            metadata,
        )

    def forecast_many(
        self,
        tickers: list,
        intervals=("15m", "1h"),
        *,
        generated_at: Optional[datetime] = None,
    ):
        generated = generated_at or datetime.now(timezone.utc)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        generated = generated.astimezone(timezone.utc)
        # Refresh live mids once per forecast batch, while sharing the same mid
        # between the 15m and 1h horizon for each coin.
        self.last_prices = {}

        results = []
        for coin in tickers:
            for interval in intervals:
                horizon_minutes = 15 if interval == "15m" else 60
                target = normalized_target_time(generated, horizon_minutes)
                timeframe = (
                    "Prossimi 15 Minuti" if interval == "15m" else "Prossima Ora"
                )
                try:
                    forecast_data, last_price, metadata = self.forecast(
                        coin,
                        interval,
                        generated_at=generated,
                    )
                    forecast = forecast_data.iloc[0]
                    variation = ((forecast["yhat"] - last_price) / last_price) * 100
                    results.append(
                        {
                            "Ticker": coin,
                            "Timeframe": timeframe,
                            "Horizon Minutes": horizon_minutes,
                            "Ultimo Prezzo": round(last_price, 8),
                            "Previsione": round(float(forecast["yhat"]), 8),
                            "Limite Inferiore": round(
                                float(forecast["yhat_lower"]), 8
                            ),
                            "Limite Superiore": round(
                                float(forecast["yhat_upper"]), 8
                            ),
                            "Variazione %": round(float(variation), 4),
                            "Forecast Generated At": _epoch_ms(
                                metadata["generated_at"]
                            ),
                            "Timestamp Previsione": _epoch_ms(metadata["target_at"]),
                            "Minutes To Target": horizon_minutes,
                            "Source Price Timestamp": metadata[
                                "source_price_timestamp_ms"
                            ],
                            "Source Price Kind": metadata["source_price_kind"],
                            "Completed Candle Close Price": metadata[
                                "completed_candle_close_price"
                            ],
                            "Completed Candle Close Timestamp": metadata[
                                "completed_candle_close_timestamp_ms"
                            ],
                            "Target Normalized": True,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append(
                        {
                            "Ticker": coin,
                            "Timeframe": timeframe,
                            "Horizon Minutes": horizon_minutes,
                            "Ultimo Prezzo": None,
                            "Previsione": None,
                            "Limite Inferiore": None,
                            "Limite Superiore": None,
                            "Variazione %": None,
                            "Forecast Generated At": _epoch_ms(generated),
                            "Timestamp Previsione": _epoch_ms(target),
                            "Minutes To Target": horizon_minutes,
                            "Source Price Timestamp": None,
                            "Source Price Kind": None,
                            "Completed Candle Close Price": None,
                            "Completed Candle Close Timestamp": None,
                            "Target Normalized": True,
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

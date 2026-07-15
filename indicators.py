from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from hyperliquid.info import Info
from hyperliquid.utils import constants

from strategy_config import DEFAULT_STRATEGY_CONFIG, StrategyConfig
from strategy_core import (
    atr,
    average_pairwise_correlation,
    build_strategy_snapshot,
    portfolio_correlation_factor,
)

# Costante Fee Taker standard utilizzata dal progetto originale.
TAKER_FEE_RATE = 0.00035

INTERVAL_TO_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}


class CryptoTechnicalAnalysisHL:
    """Hyperliquid market-data adapter plus strategy feature calculation.

    The class and its public methods remain in the same place as the original
    project.  Only the strategic indicators supplied to the LLM are replaced.
    """

    def __init__(
        self,
        testnet: bool = True,
        strategy_config: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
    ):
        base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.info = Info(base_url, skip_ws=True)
        self.strategy_config = strategy_config
        self._market_state_cache = None
        self._market_state_timestamp = 0.0

    def _get_global_state(self):
        now = datetime.now().timestamp()
        if self._market_state_cache and now - self._market_state_timestamp < 2:
            return self._market_state_cache
        try:
            self._market_state_cache = self.info.meta_and_asset_ctxs()
            self._market_state_timestamp = now
            return self._market_state_cache
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: Impossibile recuperare stato globale: {exc}")
            return None

    def get_market_details(self, coin: str) -> Dict[str, float]:
        state = self._get_global_state()
        empty = {
            "funding": float("nan"),
            "oi": float("nan"),
            "mark_px": float("nan"),
            "oracle_px": float("nan"),
            "mark_oracle_dislocation_bps": float("nan"),
        }
        if not state:
            return empty

        universe_dict, contexts_list = state
        coin_index = -1
        try:
            for index, asset in enumerate(universe_dict["universe"]):
                if asset["name"] == coin:
                    coin_index = index
                    break
        except Exception:  # noqa: BLE001
            return empty

        if coin_index < 0 or coin_index >= len(contexts_list):
            return empty

        ctx = contexts_list[coin_index]
        funding = _safe_float(ctx.get("funding"))
        oi = _safe_float(ctx.get("openInterest"))
        mark = _safe_float(ctx.get("markPx"))
        oracle = _safe_float(ctx.get("oraclePx"))
        dislocation = float("nan")
        if np.isfinite(mark) and np.isfinite(oracle) and oracle != 0:
            dislocation = (mark / oracle - 1.0) * 10_000.0
        return {
            "funding": funding,
            "oi": oi,
            "mark_px": mark,
            "oracle_px": oracle,
            "mark_oracle_dislocation_bps": dislocation,
        }

    def get_orderbook_metrics(self, ticker: str) -> Dict[str, float]:
        coin = ticker.split("-")[0].upper()
        empty = {
            "bid_volume": float("nan"),
            "ask_volume": float("nan"),
            "best_bid": float("nan"),
            "best_ask": float("nan"),
            "spread_bps": float("nan"),
            "depth_usd": float("nan"),
        }
        try:
            orderbook = self.info.l2_snapshot(coin)
            levels = orderbook.get("levels", []) if orderbook else []
            if len(levels) < 2 or not levels[0] or not levels[1]:
                return empty
            bids, asks = levels[0], levels[1]
            bid_volume = sum(_safe_float(level.get("sz"), 0.0) for level in bids)
            ask_volume = sum(_safe_float(level.get("sz"), 0.0) for level in asks)
            best_bid = _safe_float(bids[0].get("px"))
            best_ask = _safe_float(asks[0].get("px"))
            midpoint = (best_bid + best_ask) / 2.0
            spread_bps = (
                (best_ask - best_bid) / midpoint * 10_000.0
                if midpoint > 0 and np.isfinite(midpoint)
                else float("nan")
            )
            bid_depth = sum(
                _safe_float(level.get("px"), 0.0)
                * _safe_float(level.get("sz"), 0.0)
                for level in bids
            )
            ask_depth = sum(
                _safe_float(level.get("px"), 0.0)
                * _safe_float(level.get("sz"), 0.0)
                for level in asks
            )
            return {
                "bid_volume": float(bid_volume),
                "ask_volume": float(ask_volume),
                "best_bid": float(best_bid),
                "best_ask": float(best_ask),
                "spread_bps": float(spread_bps),
                "depth_usd": float(bid_depth + ask_depth),
            }
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: Impossibile recuperare orderbook {coin}: {exc}")
            return empty

    def get_orderbook_volume(self, ticker: str) -> str:
        metrics = self.get_orderbook_metrics(ticker)
        return (
            f"Bid Vol: {metrics['bid_volume']:.2f}, "
            f"Ask Vol: {metrics['ask_volume']:.2f}"
        )

    def fetch_ohlcv(self, coin: str, interval: str, limit: int = 500) -> pd.DataFrame:
        if interval not in INTERVAL_TO_MS:
            raise ValueError(f"Interval '{interval}' non supportato")

        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        step_ms = INTERVAL_TO_MS[interval]
        start_ms = now_ms - (limit + 2) * step_ms
        raw = self.info.candles_snapshot(
            name=coin,
            interval=interval,
            startTime=start_ms,
            endTime=now_ms,
        )
        if not raw:
            raise RuntimeError(f"Nessuna candela ricevuta per {coin} ({interval})")

        frame = pd.DataFrame(raw)
        frame["timestamp"] = pd.to_datetime(frame["t"], unit="ms", utc=True)
        frame = frame[["timestamp", "o", "h", "l", "c", "v"]].copy()
        frame.rename(
            columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"},
            inplace=True,
        )
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame.dropna(subset=["open", "high", "low", "close", "volume"], inplace=True)
        frame.sort_values("timestamp", inplace=True)
        frame.drop_duplicates("timestamp", keep="last", inplace=True)

        # A signal may use only fully completed bars.  This prevents the current
        # daily candle from defining its own Donchian or volatility observation.
        cutoff = pd.Timestamp(now) - pd.to_timedelta(step_ms, unit="ms")
        frame = frame[frame["timestamp"] <= cutoff].tail(limit).reset_index(drop=True)
        if frame.empty:
            raise RuntimeError(f"Nessuna candela completata per {coin} ({interval})")
        return frame

    @staticmethod
    def calculate_ema(data: pd.Series, period: int) -> pd.Series:
        return data.ewm(span=period, adjust=False, min_periods=period).mean()

    @staticmethod
    def calculate_macd(data: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
        fast = data.ewm(span=12, adjust=False, min_periods=12).mean()
        slow = data.ewm(span=26, adjust=False, min_periods=26).mean()
        line = fast - slow
        signal = line.ewm(span=9, adjust=False, min_periods=9).mean()
        return line, signal, line - signal

    @staticmethod
    def calculate_rsi(data: pd.Series, period: int) -> pd.Series:
        delta = data.diff()
        gain = delta.clip(lower=0.0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        loss = (-delta.clip(upper=0.0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        rs = gain / loss.replace(0.0, np.nan)
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def calculate_atr(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int
    ) -> pd.Series:
        frame = pd.DataFrame({"high": high, "low": low, "close": close})
        return atr(frame, period)

    @staticmethod
    def calculate_pivot_points(high: float, low: float, close: float) -> Dict[str, float]:
        pp = (high + low + close) / 3.0
        return {
            "pp": pp,
            "s1": (2 * pp) - high,
            "s2": pp - (high - low),
            "r1": (2 * pp) - low,
            "r2": pp + (high - low),
        }

    def get_complete_analysis(
        self,
        ticker: str,
        daily_frame: Optional[pd.DataFrame] = None,
        correlation_multiplier: float = 1.0,
        average_correlation: Optional[float] = None,
    ) -> Dict:
        coin = ticker.upper()
        frame_15m = self.fetch_ohlcv(coin, "15m", limit=200)
        frame_daily = daily_frame if daily_frame is not None else self.fetch_ohlcv(coin, "1d", limit=300)

        frame_15m = frame_15m.copy()
        frame_15m["ema_20"] = self.calculate_ema(frame_15m["close"], 20)
        _, _, macd_diff = self.calculate_macd(frame_15m["close"])
        frame_15m["macd"] = macd_diff
        frame_15m["rsi_7"] = self.calculate_rsi(frame_15m["close"], 7)
        frame_15m["rsi_14"] = self.calculate_rsi(frame_15m["close"], 14)
        frame_15m["ema_50"] = self.calculate_ema(frame_15m["close"], 50)
        frame_15m["atr_3"] = self.calculate_atr(
            frame_15m["high"], frame_15m["low"], frame_15m["close"], 3
        )
        frame_15m["atr_14"] = self.calculate_atr(
            frame_15m["high"], frame_15m["low"], frame_15m["close"], 14
        )

        previous_day = frame_daily.iloc[-1]
        pivots = self.calculate_pivot_points(
            float(previous_day["high"]),
            float(previous_day["low"]),
            float(previous_day["close"]),
        )
        market = self.get_market_details(coin)
        orderbook = self.get_orderbook_metrics(coin)
        mark = market["mark_px"]
        if not np.isfinite(mark):
            mark = float(frame_15m["close"].iloc[-1])
        market["mark_px"] = mark

        strategy = build_strategy_snapshot(
            coin,
            frame_daily.set_index("timestamp"),
            market,
            orderbook,
            correlation_multiplier=correlation_multiplier,
            average_correlation=average_correlation,
            cfg=self.strategy_config,
        )

        last_daily = pd.Timestamp(frame_daily["timestamp"].iloc[-1])
        age_hours = (pd.Timestamp.now(tz="UTC") - last_daily).total_seconds() / 3600.0
        strategy["completed_daily_candle_age_hours"] = age_hours
        if age_hours > self.strategy_config.maximum_daily_candle_age_hours:
            strategy["status"] = "suspended"
            strategy["recommended_action"] = "close_if_open_otherwise_hold"
            strategy["recommended_effective_exposure_before_drawdown"] = 0.0
            strategy["represented_effective_exposure_before_drawdown"] = 0.0
            strategy["recommended_balance_portion_before_drawdown"] = 0.0
            strategy.setdefault("invalidations", []).append("stale_daily_market_data")

        current = frame_15m.iloc[-1]
        tail = frame_15m.tail(10)
        average_volume = float(frame_15m["volume"].tail(20).mean())
        result = {
            "ticker": ticker,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "current": {
                "price": float(current["close"]),
                "ema20": _finite_or_none(current["ema_20"]),
                "macd": _finite_or_none(current["macd"]),
                "rsi_7": _finite_or_none(current["rsi_7"]),
            },
            "volume": (
                f"Bid Vol: {orderbook['bid_volume']:.2f}, "
                f"Ask Vol: {orderbook['ask_volume']:.2f}"
            ),
            "orderbook": orderbook,
            "pivot_points": pivots,
            "derivatives": {
                "open_interest_latest": market["oi"],
                "open_interest_average": market["oi"],
                "funding_rate": market["funding"],
                "mark_price": market["mark_px"],
                "oracle_price": market["oracle_px"],
                "mark_oracle_dislocation_bps": market[
                    "mark_oracle_dislocation_bps"
                ],
                "estimated_fee_cost": float(mark) * TAKER_FEE_RATE,
            },
            "intraday": {
                "mid_prices": _finite_list(tail["close"]),
                "ema_20": _finite_list(tail["ema_20"]),
                "macd": _finite_list(tail["macd"]),
                "rsi_7": _finite_list(tail["rsi_7"]),
                "rsi_14": _finite_list(tail["rsi_14"]),
            },
            "longer_term_15m": {
                "ema_20_current": _finite_or_none(current["ema_20"]),
                "ema_50_current": _finite_or_none(current["ema_50"]),
                "atr_3_current": _finite_or_none(current["atr_3"]),
                "atr_14_current": _finite_or_none(current["atr_14"]),
                "volume_current": float(current["volume"]),
                "volume_average": average_volume,
                "macd_series": _finite_list(tail["macd"]),
                "rsi_14_series": _finite_list(tail["rsi_14"]),
            },
            "strategy": strategy,
        }
        return result

    def format_output(self, data: Dict) -> str:
        strategy = data["strategy"]
        current = data["current"]
        derivatives = data["derivatives"]
        orderbook = data["orderbook"]
        lines = [
            f"\n<{data['ticker']}_data>",
            f"Timestamp: {data['timestamp']} UTC",
            "",
            "DEEP-RESEARCH STRATEGY SNAPSHOT (primary decision evidence):",
            json.dumps(strategy, indent=2, ensure_ascii=False, default=str),
            "",
            "Execution/liquidity context:",
            f"current_price={current['price']}",
            f"mark_price={derivatives['mark_price']}",
            f"oracle_price={derivatives['oracle_price']}",
            f"funding_rate={derivatives['funding_rate']}",
            f"open_interest={derivatives['open_interest_latest']}",
            f"spread_bps={orderbook['spread_bps']}",
            f"depth_usd={orderbook['depth_usd']}",
            f"estimated_taker_fee_usd_per_coin={derivatives['estimated_fee_cost']}",
            "",
            "Legacy 15m context (secondary only; it must not override hard strategy filters):",
            f"ema20={current['ema20']}, macd={current['macd']}, rsi7={current['rsi_7']}",
            f"</{data['ticker']}_data>",
        ]
        return "\n".join(lines) + "\n"


def analyze_multiple_tickers(
    tickers: List[str],
    testnet: bool = True,
    strategy_config: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Tuple[str, List[Dict]]:
    analyzer = CryptoTechnicalAnalysisHL(
        testnet=testnet, strategy_config=strategy_config
    )
    daily_frames: Dict[str, pd.DataFrame] = {}
    errors: Dict[str, str] = {}
    for ticker in tickers:
        try:
            daily_frames[ticker.upper()] = analyzer.fetch_ohlcv(
                ticker.upper(), "1d", limit=300
            ).set_index("timestamp")
        except Exception as exc:  # noqa: BLE001
            errors[ticker.upper()] = str(exc)

    average_correlation = average_pairwise_correlation(
        daily_frames, strategy_config.correlation_window
    )
    correlation_multiplier = portfolio_correlation_factor(
        average_correlation, strategy_config
    )

    output = ""
    data_items: List[Dict] = []
    for ticker in tickers:
        coin = ticker.upper()
        try:
            if coin not in daily_frames:
                raise RuntimeError(errors.get(coin, "daily data unavailable"))
            data = analyzer.get_complete_analysis(
                coin,
                daily_frame=daily_frames[coin].reset_index(),
                correlation_multiplier=correlation_multiplier,
                average_correlation=average_correlation,
            )
            data_items.append(data)
            output += analyzer.format_output(data)
        except Exception as exc:  # noqa: BLE001
            error_data = {
                "ticker": coin,
                "timestamp": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "strategy": {
                    "strategy_name": strategy_config.name,
                    "status": "suspended",
                    "recommended_action": "hold",
                    "recommended_effective_exposure_before_drawdown": 0.0,
                    "invalidations": ["market_data_error", str(exc)],
                },
            }
            data_items.append(error_data)
            output += (
                f"\n<{coin}_data>\nSTRATEGY SUSPENDED: {exc}\n"
                f"Recommended action: HOLD; do not invent missing data.\n"
                f"</{coin}_data>\n"
            )
    return output, data_items


def _safe_float(value, default=float("nan")) -> float:
    try:
        result = float(value)
        return result if np.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _finite_or_none(value):
    try:
        numeric = float(value)
        return numeric if np.isfinite(numeric) else None
    except (TypeError, ValueError):
        return None


def _finite_list(series: pd.Series) -> List[Optional[float]]:
    return [_finite_or_none(value) for value in series.tolist()]

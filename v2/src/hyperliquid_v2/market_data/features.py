from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Any, Deque, Iterable


@dataclass(frozen=True)
class Candle:
    open_time_ms: int
    close_time_ms: int
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int


@dataclass(frozen=True)
class FeatureSnapshot:
    symbol: str
    observed_at_ms: int
    mid_price: float
    spread_bps: float | None
    book_imbalance: float | None
    buy_aggression: float | None
    sell_aggression: float | None
    trade_notional_30s: float
    price_velocity_bps_15s: float
    price_velocity_bps_60s: float
    price_acceleration_bps: float
    realized_vol_bps_60s: float
    open_interest: float | None
    open_interest_change_pct: float | None
    funding_rate: float | None
    ema20_15m: float | None
    ema50_15m: float | None
    atr14_15m: float | None
    rsi14_15m: float | None
    volume_ratio_15m: float | None
    donchian_high_20_15m: float | None
    momentum_1h_pct: float | None
    data_quality_score: float
    data_quality_flags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class _SymbolBuffer:
    mids: Deque[tuple[int, float]] = field(default_factory=lambda: deque(maxlen=2400))
    trades: Deque[tuple[int, str, float, float]] = field(default_factory=lambda: deque(maxlen=12000))
    books: Deque[tuple[int, list[tuple[float, float]], list[tuple[float, float]]]] = field(default_factory=lambda: deque(maxlen=120))
    candles: dict[str, Deque[Candle]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=500)))
    open_interest: Deque[tuple[int, float]] = field(default_factory=lambda: deque(maxlen=500))
    funding_rate: float | None = None


class FeatureEngine:
    def __init__(self, symbols: Iterable[str]) -> None:
        self.symbols = tuple(str(s).upper() for s in symbols)
        self._buffers = {symbol: _SymbolBuffer() for symbol in self.symbols}

    def update_mid(self, observed_at_ms: int, mids: dict[str, Any]) -> None:
        for symbol in self.symbols:
            raw = mids.get(symbol)
            price = _float(raw)
            if price and price > 0:
                self._buffers[symbol].mids.append((observed_at_ms, price))

    def update_trades(self, trades: Any) -> None:
        if not isinstance(trades, list):
            trades = [trades]
        for raw in trades:
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("coin") or raw.get("symbol") or "").upper()
            if symbol not in self._buffers:
                continue
            ts = int(raw.get("time") or 0)
            price = _float(raw.get("px"))
            size = _float(raw.get("sz"))
            if ts > 0 and price and size:
                self._buffers[symbol].trades.append((ts, str(raw.get("side") or ""), price, size))
                self._buffers[symbol].mids.append((ts, price))

    def update_book(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        symbol = str(raw.get("coin") or "").upper()
        if symbol not in self._buffers:
            return
        levels = raw.get("levels") or []
        if not isinstance(levels, list) or len(levels) < 2:
            return
        bids = _levels(levels[0])
        asks = _levels(levels[1])
        if bids and asks:
            self._buffers[symbol].books.append((int(raw.get("time") or 0), bids, asks))

    def update_candle(self, raw: Any) -> None:
        if isinstance(raw, list):
            for item in raw:
                self.update_candle(item)
            return
        if not isinstance(raw, dict):
            return
        symbol = str(raw.get("s") or raw.get("coin") or "").upper()
        interval = str(raw.get("i") or raw.get("interval") or "")
        if symbol not in self._buffers or not interval:
            return
        candle = Candle(
            open_time_ms=int(raw.get("t") or raw.get("openTime") or 0),
            close_time_ms=int(raw.get("T") or raw.get("closeTime") or 0),
            interval=interval,
            open=float(raw.get("o")),
            high=float(raw.get("h")),
            low=float(raw.get("l")),
            close=float(raw.get("c")),
            volume=float(raw.get("v") or 0),
            trades=int(raw.get("n") or 0),
        )
        series = self._buffers[symbol].candles[interval]
        if series and series[-1].open_time_ms == candle.open_time_ms:
            series[-1] = candle
        else:
            series.append(candle)
        self._buffers[symbol].mids.append((candle.close_time_ms, candle.close))

    def bootstrap_candles(self, symbol: str, interval: str, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.update_candle(row)

    def update_asset_context(self, raw: Any, observed_at_ms: int) -> None:
        if not isinstance(raw, dict):
            return
        symbol = str(raw.get("coin") or raw.get("symbol") or "").upper()
        ctx = raw.get("ctx") if isinstance(raw.get("ctx"), dict) else raw
        if symbol not in self._buffers:
            return
        oi = _float(ctx.get("openInterest") or ctx.get("open_interest"))
        if oi is not None:
            self._buffers[symbol].open_interest.append((observed_at_ms, oi))
        funding = _float(ctx.get("funding") or ctx.get("fundingRate") or ctx.get("funding_rate"))
        if funding is not None:
            self._buffers[symbol].funding_rate = funding
        mark = _float(ctx.get("markPx") or ctx.get("mark_price"))
        if mark and mark > 0:
            self._buffers[symbol].mids.append((observed_at_ms, mark))

    def latest_mid(self, symbol: str) -> float | None:
        buf = self._buffers[str(symbol).upper()]
        return buf.mids[-1][1] if buf.mids else None

    def snapshot(self, symbol: str, observed_at_ms: int) -> FeatureSnapshot | None:
        symbol = symbol.upper()
        buf = self._buffers[symbol]
        if not buf.mids:
            return None
        mid = buf.mids[-1][1]
        spread, imbalance = _book_features(buf.books[-1] if buf.books else None)
        buy_aggr, sell_aggr, notional = _trade_features(buf.trades, observed_at_ms)
        vel15 = _return_bps(buf.mids, observed_at_ms, 15_000)
        vel60 = _return_bps(buf.mids, observed_at_ms, 60_000)
        accel = vel15 - vel60 / 4.0
        realized = _realized_vol_bps(buf.mids, observed_at_ms, 60_000)
        oi, oi_change = _oi_features(buf.open_interest, observed_at_ms)
        c15 = list(buf.candles.get("15m", ()))
        c1h = list(buf.candles.get("1h", ()))
        closes15 = [c.close for c in c15]
        flags: list[str] = []
        if spread is None:
            flags.append("missing_book")
        if buy_aggr is None:
            flags.append("missing_recent_trades")
        if len(c15) < 50:
            flags.append("insufficient_15m_history")
        if len(c1h) < 3:
            flags.append("insufficient_1h_history")
        age_ms = max(0, observed_at_ms - buf.mids[-1][0])
        if age_ms > 30_000:
            flags.append("stale_mid")
        score = max(0.0, 1.0 - 0.18 * len(flags))
        return FeatureSnapshot(
            symbol=symbol,
            observed_at_ms=observed_at_ms,
            mid_price=mid,
            spread_bps=spread,
            book_imbalance=imbalance,
            buy_aggression=buy_aggr,
            sell_aggression=sell_aggr,
            trade_notional_30s=notional,
            price_velocity_bps_15s=vel15,
            price_velocity_bps_60s=vel60,
            price_acceleration_bps=accel,
            realized_vol_bps_60s=realized,
            open_interest=oi,
            open_interest_change_pct=oi_change,
            funding_rate=buf.funding_rate,
            ema20_15m=_ema(closes15, 20),
            ema50_15m=_ema(closes15, 50),
            atr14_15m=_atr(c15, 14),
            rsi14_15m=_rsi(closes15, 14),
            volume_ratio_15m=_volume_ratio(c15, 20),
            donchian_high_20_15m=max((c.high for c in c15[-21:-1]), default=None),
            momentum_1h_pct=_momentum(c1h),
            data_quality_score=score,
            data_quality_flags=tuple(flags),
        )

    def candles(self, symbol: str, interval: str) -> tuple[Candle, ...]:
        return tuple(self._buffers[symbol.upper()].candles.get(interval, ()))


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _levels(raw: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            price = _float(item.get("px"))
            size = _float(item.get("sz"))
            if price and size:
                out.append((price, size))
    return out


def _book_features(book: tuple[int, list[tuple[float, float]], list[tuple[float, float]]] | None) -> tuple[float | None, float | None]:
    if not book:
        return None, None
    _, bids, asks = book
    if not bids or not asks:
        return None, None
    best_bid = max(price for price, _ in bids)
    best_ask = min(price for price, _ in asks)
    mid = (best_bid + best_ask) / 2
    spread = (best_ask - best_bid) / mid * 10_000 if mid else None
    bid_size = sum(size for _, size in bids[:5])
    ask_size = sum(size for _, size in asks[:5])
    total = bid_size + ask_size
    return spread, ((bid_size - ask_size) / total if total else 0.0)


def _trade_features(trades: Deque[tuple[int, str, float, float]], now: int) -> tuple[float | None, float | None, float]:
    buy = 0.0
    sell = 0.0
    for ts, side, price, size in reversed(trades):
        if ts < now - 30_000:
            break
        notional = price * size
        if side.upper() in {"B", "BUY"}:
            buy += notional
        elif side.upper() in {"A", "SELL"}:
            sell += notional
    total = buy + sell
    return (buy / total, sell / total, total) if total else (None, None, 0.0)


def _price_at_or_before(points: Deque[tuple[int, float]], target: int) -> float | None:
    for ts, price in reversed(points):
        if ts <= target:
            return price
    return points[0][1] if points else None


def _return_bps(points: Deque[tuple[int, float]], now: int, window: int) -> float:
    if not points:
        return 0.0
    old = _price_at_or_before(points, now - window)
    new = points[-1][1]
    return math.log(new / old) * 10_000 if old and new > 0 else 0.0


def _realized_vol_bps(points: Deque[tuple[int, float]], now: int, window: int) -> float:
    values = [price for ts, price in points if ts >= now - window]
    if len(values) < 3:
        return 0.0
    returns = [
        math.log(values[index] / values[index - 1]) * 10_000
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]
    return pstdev(returns) if len(returns) > 1 else 0.0


def _oi_features(points: Deque[tuple[int, float]], now: int) -> tuple[float | None, float | None]:
    if not points:
        return None, None
    current = points[-1][1]
    old = None
    for ts, value in reversed(points):
        if ts <= now - 60_000:
            old = value
            break
    return current, ((current / old - 1) * 100 if old and old != 0 else None)


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = mean(values[:period])
    for value in values[period:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _atr(candles: list[Candle], period: int) -> float | None:
    if len(candles) < period + 1:
        return None
    ranges = []
    for previous, current in zip(candles[-period - 1:-1], candles[-period:]):
        ranges.append(max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close)))
    return mean(ranges)


def _rsi(values: list[float], period: int) -> float | None:
    if len(values) < period + 1:
        return None
    differences = [values[index] - values[index - 1] for index in range(len(values) - period, len(values))]
    gains = [max(0, value) for value in differences]
    losses = [max(0, -value) for value in differences]
    average_gain = mean(gains)
    average_loss = mean(losses)
    if average_loss == 0:
        return 100.0
    return 100 - 100 / (1 + average_gain / average_loss)


def _volume_ratio(candles: list[Candle], period: int) -> float | None:
    if len(candles) < period + 1:
        return None
    average = mean(candle.volume for candle in candles[-period - 1:-1])
    return candles[-1].volume / average if average else None


def _momentum(candles: list[Candle]) -> float | None:
    if len(candles) < 2 or candles[-2].close <= 0:
        return None
    return (candles[-1].close / candles[-2].close - 1) * 100

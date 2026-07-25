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
    completed_15m_open_time_ms: int | None = None
    completed_15m_close_time_ms: int | None = None
    completed_15m_close: float | None = None
    macd_15m: float | None = None
    previous_macd_15m: float | None = None
    tactical_momentum_1h_pct: float | None = None
    tactical_ema20_15m: float | None = None
    tactical_ema50_15m: float | None = None
    tactical_rsi14_15m: float | None = None
    tactical_volume_ratio_15m: float | None = None
    ma100_1d: float | None = None
    ma200_1d: float | None = None
    regime_1d: str | None = None
    mark_price: float | None = None
    oracle_price: float | None = None
    mark_oracle_dislocation_bps: float | None = None
    book_age_ms: int | None = None
    asset_context_age_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class _SymbolBuffer:
    mids: Deque[tuple[int, float]] = field(default_factory=lambda: deque(maxlen=2400))
    trades: Deque[tuple[int, str, float, float]] = field(default_factory=lambda: deque(maxlen=12000))
    books: Deque[tuple[int, list[tuple[float, float]], list[tuple[float, float]]]] = field(default_factory=lambda: deque(maxlen=120))
    candles: dict[str, Deque[Candle]] = field(default_factory=lambda: defaultdict(lambda: deque(maxlen=500)))
    open_interest: Deque[tuple[int, float]] = field(default_factory=lambda: deque(maxlen=500))
    funding_rates: Deque[tuple[int, float]] = field(
        default_factory=lambda: deque(maxlen=500)
    )
    mark_prices: Deque[tuple[int, float]] = field(
        default_factory=lambda: deque(maxlen=500)
    )
    oracle_prices: Deque[tuple[int, float]] = field(
        default_factory=lambda: deque(maxlen=500)
    )


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
            self._buffers[symbol].funding_rates.append(
                (observed_at_ms, funding)
            )
        mark = _float(ctx.get("markPx") or ctx.get("mark_price"))
        if mark and mark > 0:
            self._buffers[symbol].mids.append((observed_at_ms, mark))
            self._buffers[symbol].mark_prices.append(
                (observed_at_ms, mark)
            )
        oracle = _float(ctx.get("oraclePx") or ctx.get("oracle_price"))
        if oracle and oracle > 0:
            self._buffers[symbol].oracle_prices.append(
                (observed_at_ms, oracle)
            )

    def latest_mid(self, symbol: str) -> float | None:
        buf = self._buffers[str(symbol).upper()]
        return buf.mids[-1][1] if buf.mids else None

    def snapshot(self, symbol: str, observed_at_ms: int) -> FeatureSnapshot | None:
        symbol = symbol.upper()
        buf = self._buffers[symbol]
        mid_point = _point_at_or_before(buf.mids, observed_at_ms)
        if mid_point is None:
            return None
        mid_timestamp, mid = mid_point
        book = _book_at_or_before(buf.books, observed_at_ms)
        spread, imbalance = _book_features(book)
        buy_aggr, sell_aggr, notional = _trade_features(buf.trades, observed_at_ms)
        vel15 = _return_bps(buf.mids, observed_at_ms, 15_000)
        vel60 = _return_bps(buf.mids, observed_at_ms, 60_000)
        accel = vel15 - vel60 / 4.0
        realized = _realized_vol_bps(buf.mids, observed_at_ms, 60_000)
        oi, oi_change = _oi_features(buf.open_interest, observed_at_ms)
        c15 = _completed_candles(
            buf.candles.get("15m", ()),
            observed_at_ms,
        )
        c1h = _completed_candles(
            buf.candles.get("1h", ()),
            observed_at_ms,
        )
        c1d = _completed_candles(
            buf.candles.get("1d", ()),
            observed_at_ms,
        )
        closes15 = [c.close for c in c15]
        macd, previous_macd = _macd_pair(closes15)
        ma100, ma200, daily_regime = _daily_regime(c1d)
        mark_point = _point_at_or_before(
            buf.mark_prices,
            observed_at_ms,
        )
        oracle_point = _point_at_or_before(
            buf.oracle_prices,
            observed_at_ms,
        )
        funding_point = _point_at_or_before(
            buf.funding_rates,
            observed_at_ms,
        )
        mark_price = mark_point[1] if mark_point else None
        oracle_price = oracle_point[1] if oracle_point else None
        funding_rate = funding_point[1] if funding_point else None
        asset_timestamps = [
            point[0]
            for point in (mark_point, oracle_point, funding_point)
            if point is not None
        ]
        asset_context_age_ms = (
            max(observed_at_ms - timestamp for timestamp in asset_timestamps)
            if asset_timestamps
            else None
        )
        book_age_ms = (
            max(0, observed_at_ms - book[0])
            if book is not None
            else None
        )
        dislocation = (
            (mark_price / oracle_price - 1.0) * 10_000
            if mark_price is not None
            and oracle_price is not None
            and oracle_price > 0
            else None
        )
        flags: list[str] = []
        if spread is None:
            flags.append("missing_book")
        elif book_age_ms is not None and book_age_ms > 30_000:
            flags.append("stale_book")
        if buy_aggr is None:
            flags.append("missing_recent_trades")
        if len(c15) < 50:
            flags.append("insufficient_15m_history")
        if len(c1h) < 3:
            flags.append("insufficient_1h_history")
        age_ms = max(0, observed_at_ms - mid_timestamp)
        if age_ms > 30_000:
            flags.append("stale_mid")
        score = max(0.0, 1.0 - 0.18 * len(flags))
        completed_15m = c15[-1] if c15 else None
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
            funding_rate=funding_rate,
            ema20_15m=_ema(closes15, 20),
            ema50_15m=_ema(closes15, 50),
            atr14_15m=_atr(c15, 14),
            rsi14_15m=_rsi(closes15, 14),
            volume_ratio_15m=_volume_ratio(c15, 20),
            donchian_high_20_15m=max((c.high for c in c15[-21:-1]), default=None),
            momentum_1h_pct=_momentum(c1h),
            data_quality_score=score,
            data_quality_flags=tuple(flags),
            completed_15m_open_time_ms=(
                completed_15m.open_time_ms if completed_15m else None
            ),
            completed_15m_close_time_ms=(
                completed_15m.close_time_ms if completed_15m else None
            ),
            completed_15m_close=(
                completed_15m.close if completed_15m else None
            ),
            macd_15m=macd,
            previous_macd_15m=previous_macd,
            tactical_momentum_1h_pct=_bar_momentum(closes15, 4),
            tactical_ema20_15m=_pandas_ema(closes15, 20),
            tactical_ema50_15m=_pandas_ema(closes15, 50),
            tactical_rsi14_15m=_wilder_rsi(closes15, 14),
            tactical_volume_ratio_15m=_v1_volume_ratio(c15, 20),
            ma100_1d=ma100,
            ma200_1d=ma200,
            regime_1d=daily_regime,
            mark_price=mark_price,
            oracle_price=oracle_price,
            mark_oracle_dislocation_bps=dislocation,
            book_age_ms=book_age_ms,
            asset_context_age_ms=asset_context_age_ms,
        )

    def candles(self, symbol: str, interval: str) -> tuple[Candle, ...]:
        return tuple(self._buffers[symbol.upper()].candles.get(interval, ()))

    def completed_candles(
        self,
        symbol: str,
        interval: str,
        observed_at_ms: int,
    ) -> tuple[Candle, ...]:
        return tuple(
            _completed_candles(
                self._buffers[symbol.upper()].candles.get(interval, ()),
                observed_at_ms,
            )
        )


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
        if ts > now:
            continue
        if ts < now - 30_000:
            continue
        notional = price * size
        if side.upper() in {"B", "BUY"}:
            buy += notional
        elif side.upper() in {"A", "SELL"}:
            sell += notional
    total = buy + sell
    return (buy / total, sell / total, total) if total else (None, None, 0.0)


def _price_at_or_before(points: Deque[tuple[int, float]], target: int) -> float | None:
    point = _point_at_or_before(points, target)
    return point[1] if point else None


def _point_at_or_before(
    points: Deque[tuple[int, float]],
    target: int,
) -> tuple[int, float] | None:
    candidate = max(
        (
            (index, point)
            for index, point in enumerate(points)
            if point[0] <= target
        ),
        key=lambda item: (item[1][0], item[0]),
        default=None,
    )
    return candidate[1] if candidate else None


def _book_at_or_before(
    books: Deque[
        tuple[int, list[tuple[float, float]], list[tuple[float, float]]]
    ],
    target: int,
) -> tuple[
    int,
    list[tuple[float, float]],
    list[tuple[float, float]],
] | None:
    candidate = max(
        (
            (index, book)
            for index, book in enumerate(books)
            if book[0] <= target
        ),
        key=lambda item: (item[1][0], item[0]),
        default=None,
    )
    return candidate[1] if candidate else None


def _completed_candles(
    candles: Iterable[Candle],
    observed_at_ms: int,
) -> list[Candle]:
    by_open_time = {
        candle.open_time_ms: candle
        for candle in candles
        if 0 < candle.close_time_ms <= observed_at_ms
    }
    return [
        by_open_time[open_time]
        for open_time in sorted(by_open_time)
    ]


def _return_bps(points: Deque[tuple[int, float]], now: int, window: int) -> float:
    if not points:
        return 0.0
    old = _price_at_or_before(points, now - window)
    new = _price_at_or_before(points, now)
    return (
        math.log(new / old) * 10_000
        if old and new and new > 0
        else 0.0
    )


def _realized_vol_bps(points: Deque[tuple[int, float]], now: int, window: int) -> float:
    values = [
        price
        for _ts, price in sorted(
            (
                (ts, price)
                for ts, price in points
                if now - window <= ts <= now
            ),
            key=lambda point: point[0],
        )
    ]
    if len(values) < 3:
        return 0.0
    returns = [
        math.log(values[index] / values[index - 1]) * 10_000
        for index in range(1, len(values))
        if values[index - 1] > 0
    ]
    return pstdev(returns) if len(returns) > 1 else 0.0


def _oi_features(points: Deque[tuple[int, float]], now: int) -> tuple[float | None, float | None]:
    current_point = _point_at_or_before(points, now)
    if current_point is None:
        return None, None
    current = current_point[1]
    old = _price_at_or_before(points, now - 60_000)
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
    differences = [
        values[index] - values[index - 1]
        for index in range(len(values) - period, len(values))
    ]
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


def _bar_momentum(
    closes: list[float],
    bars: int,
) -> float | None:
    if len(closes) < bars + 1 or closes[-bars - 1] <= 0:
        return None
    return (closes[-1] / closes[-bars - 1] - 1) * 100


def _macd_pair(
    closes: list[float],
) -> tuple[float | None, float | None]:
    if len(closes) < 26:
        return None, None
    current_fast = _pandas_ema(closes, 12)
    current_slow = _pandas_ema(closes, 26)
    previous_fast = _pandas_ema(closes[:-1], 12)
    previous_slow = _pandas_ema(closes[:-1], 26)
    if (
        current_fast is None
        or current_slow is None
        or previous_fast is None
        or previous_slow is None
    ):
        return None, None
    return current_fast - current_slow, previous_fast - previous_slow


def _pandas_ema(
    values: list[float],
    period: int,
) -> float | None:
    """Match pandas ``ewm(span=period, adjust=False, min_periods=period)``."""
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _wilder_rsi(
    values: list[float],
    period: int,
) -> float | None:
    """Match the V1 exponentially weighted RSI calculation."""
    if len(values) < period + 1:
        return None
    differences = [
        values[index] - values[index - 1]
        for index in range(1, len(values))
    ]
    alpha = 1 / period
    average_gain = max(0.0, differences[0])
    average_loss = max(0.0, -differences[0])
    for difference in differences[1:]:
        average_gain = (
            alpha * max(0.0, difference)
            + (1 - alpha) * average_gain
        )
        average_loss = (
            alpha * max(0.0, -difference)
            + (1 - alpha) * average_loss
        )
    if average_loss == 0:
        return 100.0
    return 100 - 100 / (1 + average_gain / average_loss)


def _v1_volume_ratio(
    candles: list[Candle],
    period: int,
) -> float | None:
    if len(candles) < period:
        return None
    average = mean(candle.volume for candle in candles[-period:])
    return candles[-1].volume / average if average else None


def _daily_regime(
    candles: list[Candle],
) -> tuple[float | None, float | None, str | None]:
    if len(candles) < 221:
        return None, None, "insufficient_data"
    closes = [candle.close for candle in candles]
    ma100 = mean(closes[-100:])
    ma200 = mean(closes[-200:])
    close = closes[-1]
    if close > ma200 and ma100 > ma200:
        regime = "favorable"
    elif close < ma200 and ma100 < ma200:
        regime = "adverse"
    else:
        regime = "neutral"
    return ma100, ma200, regime

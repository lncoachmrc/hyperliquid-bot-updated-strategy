"""Pure deterministic calculations supplied to the existing LLM decision maker.

This module does not place orders and does not decide whether an order is
approved.  It calculates the strategy evidence and prudential upper bounds
that the existing LLM receives in the prompt.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Dict, Iterable, Mapping, Tuple

import numpy as np
import pandas as pd

from strategy_config import DEFAULT_STRATEGY_CONFIG, StrategyConfig


def true_range(frame: pd.DataFrame) -> pd.Series:
    prev_close = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(frame: pd.DataFrame, window: int) -> pd.Series:
    return true_range(frame).rolling(window, min_periods=window).mean()


def realized_volatility(
    close: pd.Series,
    window: int,
    annualization_days: int = 365,
) -> pd.Series:
    returns = close.pct_change()
    return returns.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(
        annualization_days
    )


def donchian_score(frame: pd.DataFrame, lookbacks: Iterable[int]) -> pd.Series:
    """Ensemble score using only channel information available before each bar."""
    scores = []
    for lookback in lookbacks:
        upper = frame["high"].rolling(lookback, min_periods=lookback).max().shift(1)
        lower = frame["low"].rolling(lookback, min_periods=lookback).min().shift(1)
        midpoint = (upper + lower) / 2.0
        score = pd.Series(
            np.where(frame["close"] > midpoint, 1.0, -1.0), index=frame.index
        )
        score[upper.isna() | lower.isna()] = np.nan
        scores.append(score)
    return pd.concat(scores, axis=1).mean(axis=1)


def regime_factor(close: pd.Series, cfg: StrategyConfig) -> Tuple[pd.Series, pd.Series]:
    fast = close.rolling(cfg.regime_fast_ma, min_periods=cfg.regime_fast_ma).mean()
    slow = close.rolling(cfg.regime_slow_ma, min_periods=cfg.regime_slow_ma).mean()
    favorable = (close > slow) & (fast > slow)
    adverse = (close < slow) & (fast < slow)

    factor = pd.Series(cfg.neutral_regime_factor, index=close.index, dtype=float)
    label = pd.Series("neutral", index=close.index, dtype=object)
    factor[favorable] = 1.0
    label[favorable] = "favorable"
    factor[adverse] = 0.0
    label[adverse] = "adverse"
    factor[slow.isna() | fast.isna()] = np.nan
    label[slow.isna() | fast.isna()] = "insufficient_data"
    return factor, label


def drawdown_factor(drawdown: float, soft: float, hard: float) -> float:
    """Linear deleveraging: 1 above -soft, 0 at or below -hard."""
    dd = abs(min(float(drawdown), 0.0))
    if dd <= soft:
        return 1.0
    if dd >= hard:
        return 0.0
    return float(1.0 - (dd - soft) / max(hard - soft, 1e-12))


def spread_factor(spread_bps: float | None, cfg: StrategyConfig) -> float:
    if spread_bps is None or not np.isfinite(spread_bps):
        return 0.0
    if spread_bps <= cfg.spread_reduce_from_bps:
        return 1.0
    if spread_bps >= cfg.maximum_spread_bps:
        return 0.0
    return float(
        1.0
        - (spread_bps - cfg.spread_reduce_from_bps)
        / (cfg.maximum_spread_bps - cfg.spread_reduce_from_bps)
    )


def funding_factor(funding_rate: float | None, cfg: StrategyConfig) -> float:
    if funding_rate is None or not np.isfinite(funding_rate):
        return 0.0
    absolute = abs(float(funding_rate))
    if absolute >= cfg.funding_halt_abs:
        return 0.0
    if absolute >= cfg.funding_reduce_abs:
        return 0.5
    return 1.0


def dislocation_factor(dislocation_bps: float | None, cfg: StrategyConfig) -> float:
    if dislocation_bps is None or not np.isfinite(dislocation_bps):
        return 0.0
    absolute = abs(float(dislocation_bps))
    if absolute >= cfg.dislocation_halt_bps:
        return 0.0
    if absolute >= cfg.dislocation_reduce_bps:
        return 0.5
    return 1.0


def volume_factor(frame: pd.DataFrame, cfg: StrategyConfig) -> Tuple[float, float | None]:
    if "volume" not in frame or frame.empty:
        return 0.0, None
    threshold = (
        frame["volume"]
        .rolling(
            cfg.volume_quantile_window,
            min_periods=max(30, cfg.volume_quantile_window // 3),
        )
        .quantile(cfg.minimum_volume_quantile)
        .iloc[-1]
    )
    current = float(frame["volume"].iloc[-1])
    if not np.isfinite(threshold):
        return 0.0, None
    return (0.5 if current < float(threshold) else 1.0), float(threshold)


def average_pairwise_correlation(
    daily_frames: Mapping[str, pd.DataFrame], window: int
) -> float | None:
    closes = pd.DataFrame(
        {symbol: frame["close"] for symbol, frame in daily_frames.items()}
    ).dropna(how="all")
    returns = closes.tail(window + 1).pct_change().dropna(how="all")
    if returns.shape[0] < max(20, window // 3) or returns.shape[1] < 2:
        return None
    matrix = returns.corr().to_numpy()
    values = matrix[np.triu_indices_from(matrix, k=1)]
    values = values[np.isfinite(values)]
    return float(values.mean()) if values.size else None


def portfolio_correlation_factor(
    average_correlation: float | None, cfg: StrategyConfig
) -> float:
    if average_correlation is None or not np.isfinite(average_correlation):
        return 1.0
    return (
        cfg.correlation_factor
        if average_correlation >= cfg.correlation_reduce_above
        else 1.0
    )


def exposure_to_execution(
    effective_exposure: float,
    symbol: str,
    cfg: StrategyConfig,
) -> Dict[str, float | int]:
    """Represent fractional effective exposure using integer exchange leverage."""
    exposure = max(0.0, min(float(effective_exposure), cfg.asset_effective_exposure_caps[symbol]))
    if exposure <= 1.0:
        exchange_leverage = 1
        portion = exposure
    else:
        exchange_leverage = min(2, cfg.maximum_exchange_leverage)
        portion = exposure / exchange_leverage

    portion = min(portion, cfg.asset_balance_portion_caps[symbol], 1.0)
    represented = portion * exchange_leverage
    return {
        "exchange_leverage": int(exchange_leverage),
        "target_portion_of_balance": float(portion),
        "represented_effective_exposure": float(represented),
    }


def _last_finite(series: pd.Series) -> float | None:
    clean = series.replace([np.inf, -np.inf], np.nan).dropna()
    return float(clean.iloc[-1]) if not clean.empty else None


def build_strategy_snapshot(
    symbol: str,
    daily_frame: pd.DataFrame,
    market_details: Mapping[str, float],
    orderbook_metrics: Mapping[str, float],
    correlation_multiplier: float = 1.0,
    average_correlation: float | None = None,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, object]:
    symbol = symbol.upper()
    invalidations = []
    if symbol not in cfg.symbols:
        return {
            "strategy_name": cfg.name,
            "strategy_version": cfg.version,
            "status": "unauthorized_asset",
            "authorized": False,
            "recommended_action": "hold",
            "recommended_effective_exposure_before_drawdown": 0.0,
            "invalidations": ["asset_not_authorized"],
        }

    frame = daily_frame.sort_index().copy()
    required_columns = {"open", "high", "low", "close", "volume"}
    missing = required_columns.difference(frame.columns)
    if missing or len(frame) < cfg.minimum_completed_daily_bars:
        return {
            "strategy_name": cfg.name,
            "strategy_version": cfg.version,
            "status": "insufficient_data",
            "authorized": True,
            "required_completed_daily_bars": cfg.minimum_completed_daily_bars,
            "available_completed_daily_bars": int(len(frame)),
            "missing_columns": sorted(missing),
            "recommended_action": "hold",
            "recommended_effective_exposure_before_drawdown": 0.0,
            "invalidations": ["insufficient_completed_daily_data"],
        }

    score_series = donchian_score(frame, cfg.donchian_lookbacks)
    vol_series = realized_volatility(
        frame["close"], cfg.volatility_window, cfg.annualization_days
    )
    atr_series = atr(frame, cfg.atr_window)
    regime_series, regime_labels = regime_factor(frame["close"], cfg)

    score = _last_finite(score_series)
    realized_vol = _last_finite(vol_series)
    atr_value = _last_finite(atr_series)
    regime = _last_finite(regime_series)
    regime_label = str(regime_labels.iloc[-1])
    close = float(frame["close"].iloc[-1])

    spread_bps = orderbook_metrics.get("spread_bps")
    funding_rate = market_details.get("funding")
    dislocation_bps = market_details.get("mark_oracle_dislocation_bps")
    vol_liquidity_factor, volume_threshold = volume_factor(frame, cfg)
    factors = {
        "spread": spread_factor(float(spread_bps) if spread_bps is not None else None, cfg),
        "funding": funding_factor(float(funding_rate) if funding_rate is not None else None, cfg),
        "mark_oracle_dislocation": dislocation_factor(
            float(dislocation_bps) if dislocation_bps is not None else None, cfg
        ),
        "volume": vol_liquidity_factor,
        "correlation": float(correlation_multiplier),
    }
    liquidity = float(
        factors["spread"]
        * factors["funding"]
        * factors["mark_oracle_dislocation"]
        * factors["volume"]
    )

    if score is None or realized_vol is None or atr_value is None or regime is None:
        invalidations.append("indicator_not_available")
    if factors["spread"] == 0.0:
        invalidations.append("spread_or_orderbook_filter_halt")
    if factors["funding"] == 0.0:
        invalidations.append("funding_filter_halt")
    if factors["mark_oracle_dislocation"] == 0.0:
        invalidations.append("mark_oracle_dislocation_halt")
    if factors["volume"] == 0.0:
        invalidations.append("volume_history_unavailable")
    if regime == 0.0:
        invalidations.append("adverse_market_regime")

    stop_fraction = None
    stop_percent = None
    risk_exposure_cap = 0.0
    if atr_value is not None and close > 0:
        stop_fraction = cfg.stop_atr_multiple * atr_value / close
        stop_percent = float(
            np.clip(
                stop_fraction * 100.0,
                cfg.minimum_stop_percent,
                cfg.maximum_stop_percent,
            )
        )
        if stop_fraction > 0:
            risk_exposure_cap = cfg.risk_per_trade / stop_fraction

    base_exposure = 0.0
    if realized_vol is not None and realized_vol > 0:
        bounded_vol = float(
            np.clip(
                realized_vol,
                cfg.minimum_realized_volatility,
                cfg.maximum_realized_volatility,
            )
        )
        base_exposure = cfg.volatility_target / bounded_vol

    trend_long = score is not None and score >= cfg.entry_score
    trend_exit = score is None or score <= cfg.exit_score
    raw_cap = min(
        base_exposure,
        risk_exposure_cap,
        cfg.asset_effective_exposure_caps[symbol],
    )
    final_before_drawdown = (
        raw_cap * float(regime or 0.0) * liquidity * float(correlation_multiplier)
        if trend_long
        else 0.0
    )

    execution = exposure_to_execution(final_before_drawdown, symbol, cfg)
    if invalidations or trend_exit:
        action = "close_if_open_otherwise_hold"
    elif trend_long and execution["represented_effective_exposure"] > 0:
        action = "long_candidate"
    else:
        action = "hold_or_flat"

    return {
        "strategy_name": cfg.name,
        "strategy_version": cfg.version,
        "status": "valid" if not invalidations else "suspended",
        "authorized": True,
        "direction_policy": "long_bias_no_new_shorts",
        "timeframe": cfg.timeframe,
        "last_completed_daily_bar": frame.index[-1].isoformat(),
        "close": close,
        "donchian_lookbacks": list(cfg.donchian_lookbacks),
        "donchian_score": score,
        "entry_score": cfg.entry_score,
        "exit_score": cfg.exit_score,
        "trend_long": trend_long,
        "trend_exit": trend_exit,
        "regime": regime_label,
        "regime_factor": regime,
        "realized_volatility_annualized": realized_vol,
        "volatility_target": cfg.volatility_target,
        "volatility_base_exposure": base_exposure,
        "atr": atr_value,
        "stop_atr_multiple": cfg.stop_atr_multiple,
        "recommended_stop_loss_percent": stop_percent,
        "risk_per_trade": cfg.risk_per_trade,
        "risk_based_exposure_cap": risk_exposure_cap,
        "liquidity_factor": liquidity,
        "liquidity_components": factors,
        "spread_bps": spread_bps,
        "funding_rate": funding_rate,
        "mark_oracle_dislocation_bps": dislocation_bps,
        "daily_volume": float(frame["volume"].iloc[-1]),
        "daily_volume_q10_threshold": volume_threshold,
        "average_pairwise_correlation_60d": average_correlation,
        "correlation_factor": correlation_multiplier,
        "asset_effective_exposure_cap": cfg.asset_effective_exposure_caps[symbol],
        "asset_balance_portion_cap": cfg.asset_balance_portion_caps[symbol],
        "recommended_effective_exposure_before_drawdown": float(
            max(0.0, final_before_drawdown)
        ),
        "recommended_exchange_leverage_before_drawdown": execution[
            "exchange_leverage"
        ],
        "recommended_balance_portion_before_drawdown": execution[
            "target_portion_of_balance"
        ],
        "represented_effective_exposure_before_drawdown": execution[
            "represented_effective_exposure"
        ],
        "drawdown_factor_application": (
            "Multiply the recommended effective exposure by the portfolio "
            "drawdown_factor supplied in Portfolio Data, then reconvert with "
            "exposure_to_execution logic. If unavailable, do not invent it."
        ),
        "portfolio_gross_cap": cfg.portfolio_gross_cap,
        "recommended_action": action,
        "invalidations": invalidations,
        "config": {
            key: value
            for key, value in asdict(cfg).items()
            if key
            in {
                "volatility_window",
                "volatility_target",
                "atr_window",
                "stop_atr_multiple",
                "risk_per_trade",
                "maximum_exchange_leverage",
                "portfolio_gross_cap",
            }
        },
    }

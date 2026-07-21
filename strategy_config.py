"""Configuration for the Deep-Research trend-following strategy.

The project did not previously have a strategy configuration system. This
single module is deliberately limited to strategy parameters and does not
alter credentials or exchange signing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "donchian_tsmom_vol_target_long_bias"
    version: str = "1.2.0"
    symbols: Tuple[str, ...] = ("BTC", "ETH", "SOL")
    timeframe: str = "1d"

    # Daily trend classification. entry_score remains for backward-compatible
    # observability, while live eligibility is expressed explicitly as a vote
    # count so that 2 positive Donchian horizons out of 3 are unambiguous.
    donchian_lookbacks: Tuple[int, ...] = (20, 55, 120)
    entry_score: float = 0.34
    exit_score: float = 0.0
    minimum_positive_donchian_votes: int = 2

    regime_fast_ma: int = 100
    regime_slow_ma: int = 200
    neutral_regime_factor: float = 0.50
    adverse_regime_factor: float = 0.25

    volatility_window: int = 30
    volatility_target: float = 0.18
    annualization_days: int = 365
    minimum_realized_volatility: float = 0.10
    maximum_realized_volatility: float = 2.50

    atr_window: int = 20
    stop_atr_multiple: float = 3.0
    risk_per_trade: float = 0.005
    minimum_stop_percent: float = 0.50
    maximum_stop_percent: float = 25.0

    # Tactical 15m overlay used only when the daily regime is adverse. It may
    # authorize a reduced-risk long candidate; it never bypasses hard market-
    # quality invalidations such as stale data, extreme funding, wide spread or
    # mark/oracle dislocation.
    tactical_min_confirmations: int = 5
    tactical_warning_confirmations: int = 4
    tactical_exit_confirmations: int = 3
    tactical_exit_consecutive_cycles: int = 2
    tactical_volume_ratio_min: float = 0.80
    tactical_rsi_min: float = 50.0
    tactical_rsi_max: float = 80.0
    tactical_effective_exposure_cap: float = 0.25
    tactical_stop_atr_multiple: float = 2.0
    tactical_max_stop_percent: float = 5.0

    # Position-management hysteresis. A tactical deterioration does not produce
    # an immediate close: 4 confirmations is a warning, while <=3 must persist
    # for two completed cycles. Hard invalidations and external stops bypass the
    # minimum holding period.
    minimum_position_hold_minutes: int = 30
    stable_position_llm_review_minutes: int = 30

    # Hyperliquid rejects perp orders below $10 notional. The execution adapter
    # also respects each market's size precision and never silently increases a
    # requested order to the exchange minimum.
    minimum_perp_order_notional_usd: float = 10.0

    maximum_exchange_leverage: int = 2
    portfolio_gross_cap: float = 1.50
    asset_effective_exposure_caps: Dict[str, float] = field(
        default_factory=lambda: {"BTC": 2.0, "ETH": 2.0, "SOL": 1.5}
    )
    asset_balance_portion_caps: Dict[str, float] = field(
        default_factory=lambda: {"BTC": 0.85, "ETH": 0.65, "SOL": 0.40}
    )

    drawdown_soft: float = 0.05
    drawdown_hard: float = 0.15

    spread_reduce_from_bps: float = 5.0
    maximum_spread_bps: float = 20.0
    volume_quantile_window: int = 90
    minimum_volume_quantile: float = 0.10
    funding_reduce_abs: float = 0.0015
    funding_halt_abs: float = 0.0030
    dislocation_reduce_bps: float = 20.0
    dislocation_halt_bps: float = 50.0

    correlation_window: int = 60
    correlation_reduce_above: float = 0.75
    correlation_factor: float = 0.75

    minimum_completed_daily_bars: int = 221
    maximum_daily_candle_age_hours: float = 36.0


DEFAULT_STRATEGY_CONFIG = StrategyConfig()

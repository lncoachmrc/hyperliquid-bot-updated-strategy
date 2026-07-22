"""Configuration for the Deep-Research trend-following strategy.

This module contains strategy and risk parameters only. It does not alter
credentials, exchange signing, wallet selection or orchestration authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "donchian_tsmom_vol_target_long_bias"
    version: str = "1.6.0"
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

    # Tactical 15m overlay used when the daily regime is adverse. Exposure is
    # quality-sensitive but remains bounded by the stop-based 0.5% account-risk
    # budget, liquidity/correlation factors and asset caps.
    tactical_min_confirmations: int = 5
    tactical_warning_confirmations: int = 4
    tactical_exit_confirmations: int = 3
    tactical_exit_consecutive_cycles: int = 2  # interpreted as DISTINCT 15m bars
    tactical_volume_ratio_min: float = 0.80
    tactical_rsi_min: float = 50.0
    tactical_rsi_max: float = 80.0
    tactical_effective_exposure_cap: float = 0.25  # legacy/default observability
    tactical_weak_effective_exposure_cap: float = 0.15
    tactical_standard_effective_exposure_cap: float = 0.25
    tactical_moderate_effective_exposure_cap: float = 0.30
    tactical_strong_effective_exposure_cap: float = 0.50
    tactical_symbol_exposure_factors: Dict[str, float] = field(
        default_factory=lambda: {"BTC": 1.0, "ETH": 1.0, "SOL": 0.80}
    )
    tactical_stop_atr_multiple: float = 2.0
    tactical_max_stop_percent: float = 5.0

    # Adverse-regime entries are intentionally much stricter than the generic
    # tactical candidate. A 1/3 Donchian setup must be exceptional; 2/3 or 3/3
    # still requires strong confirmation. Anti-chase controls prevent buying an
    # already extended move or a setup with insufficient room to resistance.
    adverse_weak_required_confirmations: int = 7
    adverse_weak_min_volume_ratio: float = 1.20
    adverse_aligned_required_confirmations: int = 6
    adverse_aligned_min_volume_ratio: float = 1.00
    adverse_max_distance_from_ema20_atr: float = 1.00
    adverse_max_one_hour_extension_atr: float = 2.50
    adverse_max_completed_bar_range_atr: float = 1.80
    adverse_min_reward_to_risk: float = 1.50
    adverse_max_correlated_long_positions: int = 1

    # A persistent executable candidate normally waits for the 30-minute LLM
    # cadence. Material quality upgrades bypass that cooldown: 5->6 / 6->7
    # confirmations, a higher leverage tier, an improved Donchian vote count or
    # >=20% more strategy-approved effective exposure.
    candidate_upgrade_min_confirmations: int = 6
    candidate_upgrade_min_confirmation_gain: int = 1
    candidate_upgrade_effective_exposure_increase_fraction: float = 0.20

    # Position-management hysteresis. Weakness must be confirmed by distinct
    # completed 15m candles, never by repeated worker cycles reading the same bar.
    minimum_position_hold_minutes: int = 30
    stable_position_llm_review_minutes: int = 30

    # Re-entry protection. A recently closed symbol is blocked for 30 minutes.
    # A truly exceptional breakout may bypass the cooldown only with 7/7 tactical
    # confirmations, volume expansion and a close above the previous 1h high.
    post_close_reentry_cooldown_minutes: int = 30
    reentry_breakout_override_confirmations: int = 7
    reentry_breakout_override_volume_ratio: float = 1.20
    reentry_breakout_lookback_bars: int = 4

    # The existing broad protection remains for neutral/favourable daily trades.
    # Adverse tactical trades receive an earlier post-processing overlay: once
    # MFE reaches 0.75R, a give-back to the fee-adjusted floor authorizes review.
    profit_protection_trigger_r: float = 1.50
    profit_protection_floor_r: float = 0.50
    adverse_profit_protection_trigger_r: float = 0.75
    adverse_profit_protection_floor_r: float = 0.20
    adverse_estimated_round_trip_cost_pct: float = 0.10

    # Prophet remains shadow-only until a sufficiently large comparable sample
    # exists. These thresholds describe the hypothetical policy; they never
    # mutate live operation, exposure, leverage or stop values.
    prophet_shadow_policy_version: str = "1.0"
    prophet_shadow_min_sample_size: int = 30
    prophet_shadow_preferred_sample_size: int = 50
    prophet_shadow_1h_veto_threshold_pct: float = -0.10
    prophet_shadow_1h_positive_threshold_pct: float = 0.10
    prophet_shadow_15m_timing_delay_threshold_pct: float = -0.05

    # Hyperliquid rejects perp orders below $10 notional. The execution adapter
    # also respects market size precision and never silently increases a request.
    minimum_perp_order_notional_usd: float = 10.0

    # Dynamic leverage separates collateral efficiency from economic exposure.
    # The normal live selector uses 1x-5x. 10x is an absolute technical ceiling,
    # never a mandate to increase notional or account risk.
    maximum_exchange_leverage: int = 10
    normal_max_exchange_leverage: int = 5
    tactical_weak_leverage: int = 1
    tactical_standard_leverage: int = 2
    tactical_strong_leverage: int = 3
    daily_neutral_leverage: int = 3
    daily_favorable_leverage: int = 5

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

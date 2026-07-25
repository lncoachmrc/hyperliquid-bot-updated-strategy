"""Falsifiable shadow experiment for adverse-regime tactical rallies.

This module cannot create a DecisionPacket and has no execution interface.  It
records the unfiltered base rate of the historical hypothesis and, separately,
the subset that one globally locked BTC/ETH/SOL portfolio could have taken.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from hyperliquid_v2.market_data.features import FeatureSnapshot


POLICY_VERSION = "adverse-tactical-fade-shadow-v1"
SETUP_FAMILY = "adverse_tactical_long_fade_180m"
BASE_RATE_SOURCE = "tactical_fade_base_rate"
PORTFOLIO_SOURCE = "tactical_fade_portfolio_selected"
SYMBOL_PRIORITY = {"BTC": 0, "ETH": 1, "SOL": 2}


@dataclass(frozen=True)
class TacticalFadePolicy:
    direction: str = "short"
    horizon_minutes: int = 180
    minimum_confirmations: int = 5
    rsi_min: float = 50.0
    rsi_max: float = 80.0
    minimum_volume_ratio: float = 0.80
    stop_atr_multiple: float = 2.0
    minimum_stop_pct: float = 0.50
    maximum_stop_pct: float = 5.0
    minimum_data_quality: float = 0.70
    maximum_spread_bps: float = 20.0
    maximum_absolute_funding_rate: float = 0.0030
    maximum_absolute_dislocation_bps: float = 50.0
    maximum_book_age_ms: int = 30_000
    maximum_asset_context_age_ms: int = 300_000
    assumed_round_trip_cost_bps: float = 10.0

    def fingerprint(self) -> str:
        payload = json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class TacticalFadeAssessment:
    symbol: str
    structural_candidate: bool
    portfolio_eligible: bool
    reasons: tuple[str, ...]
    checks: dict[str, bool]
    confirmations: int
    entry_price: float | None
    stop_price: float | None
    stop_distance_pct: float | None
    completed_bar_open_time_ms: int | None
    completed_bar_close_time_ms: int | None
    spread_bps: float | None
    data_quality_score: float
    policy_version: str
    policy_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TacticalFadeShadow:
    """Evaluate and rank the fixed shadow hypothesis."""

    def __init__(
        self,
        policy: TacticalFadePolicy = TacticalFadePolicy(),
    ) -> None:
        self.policy = policy

    def assess(
        self,
        feature: FeatureSnapshot,
    ) -> TacticalFadeAssessment:
        policy = self.policy
        required = {
            "completed_15m_close": feature.completed_15m_close,
            "completed_15m_open_time_ms": (
                feature.completed_15m_open_time_ms
            ),
            "completed_15m_close_time_ms": (
                feature.completed_15m_close_time_ms
            ),
            "tactical_ema20_15m": feature.tactical_ema20_15m,
            "tactical_ema50_15m": feature.tactical_ema50_15m,
            "macd_15m": feature.macd_15m,
            "previous_macd_15m": feature.previous_macd_15m,
            "tactical_rsi14_15m": feature.tactical_rsi14_15m,
            "atr14_15m": feature.atr14_15m,
            "tactical_volume_ratio_15m": (
                feature.tactical_volume_ratio_15m
            ),
            "tactical_momentum_1h_pct": (
                feature.tactical_momentum_1h_pct
            ),
            "regime_1d": feature.regime_1d,
        }
        missing = tuple(
            name for name, value in required.items() if value is None
        )
        fingerprint = policy.fingerprint()
        if missing:
            return TacticalFadeAssessment(
                symbol=feature.symbol,
                structural_candidate=False,
                portfolio_eligible=False,
                reasons=("insufficient_completed_history", *missing),
                checks={},
                confirmations=0,
                entry_price=None,
                stop_price=None,
                stop_distance_pct=None,
                completed_bar_open_time_ms=(
                    feature.completed_15m_open_time_ms
                ),
                completed_bar_close_time_ms=(
                    feature.completed_15m_close_time_ms
                ),
                spread_bps=feature.spread_bps,
                data_quality_score=feature.data_quality_score,
                policy_version=POLICY_VERSION,
                policy_fingerprint=fingerprint,
            )

        close = float(feature.completed_15m_close)
        ema20 = float(feature.tactical_ema20_15m)
        ema50 = float(feature.tactical_ema50_15m)
        macd = float(feature.macd_15m)
        previous_macd = float(feature.previous_macd_15m)
        rsi = float(feature.tactical_rsi14_15m)
        atr = float(feature.atr14_15m)
        volume_ratio = float(feature.tactical_volume_ratio_15m)
        momentum = float(feature.tactical_momentum_1h_pct)
        checks = {
            "price_above_ema20": close > ema20,
            "ema20_above_ema50": ema20 > ema50,
            "macd_positive": macd > 0,
            "macd_rising": macd > previous_macd,
            "rsi14_supportive": policy.rsi_min <= rsi <= policy.rsi_max,
            "volume_confirmed": (
                volume_ratio >= policy.minimum_volume_ratio
            ),
            "momentum_1h_positive": momentum > 0,
        }
        confirmations = sum(checks.values())
        mandatory = (
            checks["price_above_ema20"]
            and checks["momentum_1h_positive"]
        )
        reasons: list[str] = []
        if feature.regime_1d != "adverse":
            reasons.append("daily_regime_not_adverse")
        if not mandatory:
            reasons.append("mandatory_tactical_conditions_not_met")
        if confirmations < policy.minimum_confirmations:
            reasons.append("confirmations_below_five")
        structural_candidate = not reasons

        if feature.data_quality_score < policy.minimum_data_quality:
            reasons.append("data_quality_below_threshold")
        if feature.spread_bps is None:
            reasons.append("spread_unavailable")
        elif feature.spread_bps >= policy.maximum_spread_bps:
            reasons.append("spread_above_hard_limit")
        if feature.book_age_ms is None:
            reasons.append("book_age_unavailable")
        elif feature.book_age_ms > policy.maximum_book_age_ms:
            reasons.append("book_stale")
        if feature.funding_rate is None:
            reasons.append("funding_unavailable")
        elif (
            abs(feature.funding_rate)
            >= policy.maximum_absolute_funding_rate
        ):
            reasons.append("funding_above_hard_limit")
        if feature.mark_oracle_dislocation_bps is None:
            reasons.append("mark_oracle_dislocation_unavailable")
        elif (
            abs(feature.mark_oracle_dislocation_bps)
            >= policy.maximum_absolute_dislocation_bps
        ):
            reasons.append("mark_oracle_dislocation_above_hard_limit")
        if feature.asset_context_age_ms is None:
            reasons.append("asset_context_age_unavailable")
        elif (
            feature.asset_context_age_ms
            > policy.maximum_asset_context_age_ms
        ):
            reasons.append("asset_context_stale")
        portfolio_eligible = structural_candidate and not any(
            reason
            in {
                "data_quality_below_threshold",
                "spread_unavailable",
                "spread_above_hard_limit",
                "book_age_unavailable",
                "book_stale",
                "funding_unavailable",
                "funding_above_hard_limit",
                "mark_oracle_dislocation_unavailable",
                "mark_oracle_dislocation_above_hard_limit",
                "asset_context_age_unavailable",
                "asset_context_stale",
            }
            for reason in reasons
        )

        entry = feature.mid_price if structural_candidate else None
        stop_pct = None
        stop_price = None
        if entry is not None and entry > 0 and atr > 0:
            raw_stop_pct = (
                policy.stop_atr_multiple * atr / close * 100.0
            )
            stop_pct = min(
                policy.maximum_stop_pct,
                max(policy.minimum_stop_pct, raw_stop_pct),
            )
            stop_price = entry * (1 + stop_pct / 100.0)
        elif structural_candidate:
            portfolio_eligible = False
            reasons.append("invalid_stop_distance")

        return TacticalFadeAssessment(
            symbol=feature.symbol,
            structural_candidate=structural_candidate,
            portfolio_eligible=portfolio_eligible,
            reasons=tuple(reasons) or ("fixed_shadow_hypothesis_matched",),
            checks=checks,
            confirmations=confirmations,
            entry_price=entry,
            stop_price=stop_price,
            stop_distance_pct=stop_pct,
            completed_bar_open_time_ms=(
                feature.completed_15m_open_time_ms
            ),
            completed_bar_close_time_ms=(
                feature.completed_15m_close_time_ms
            ),
            spread_bps=feature.spread_bps,
            data_quality_score=feature.data_quality_score,
            policy_version=POLICY_VERSION,
            policy_fingerprint=fingerprint,
        )

    def select_portfolio_candidate(
        self,
        assessments: Iterable[TacticalFadeAssessment],
    ) -> TacticalFadeAssessment | None:
        """Use a predeclared cost-first tie-break, never outcome data."""
        eligible = [
            item
            for item in assessments
            if item.portfolio_eligible
            and item.entry_price is not None
            and item.stop_distance_pct is not None
        ]
        if not eligible:
            return None
        return min(
            eligible,
            key=lambda item: (
                float(item.spread_bps),
                -item.confirmations,
                -item.data_quality_score,
                SYMBOL_PRIORITY.get(item.symbol, 999),
                item.symbol,
            ),
        )


def constant_risk_model(
    assessment: TacticalFadeAssessment,
    *,
    equity_usd: float,
    available_usd: float,
    equity_source: str,
    target_risk_fraction: float,
    maximum_effective_exposure: float,
) -> dict[str, float | str]:
    """Calculate counterfactual size without exposing an execution action."""
    available = available_usd if available_usd > 0 else equity_usd
    risk_capital = max(0.0, min(equity_usd, available))
    target_risk = risk_capital * max(0.0, target_risk_fraction)
    stop_fraction = float(assessment.stop_distance_pct or 0) / 100.0
    risk_sized_notional = (
        target_risk / stop_fraction
        if stop_fraction > 0
        else 0.0
    )
    exposure_capped_notional = (
        risk_capital * max(0.0, maximum_effective_exposure)
    )
    modeled_notional = min(
        risk_sized_notional,
        exposure_capped_notional,
    )
    return {
        "equity_usd": equity_usd,
        "available_usd": available_usd,
        "equity_source": equity_source,
        "target_risk_fraction": target_risk_fraction,
        "target_risk_usd": target_risk,
        "modeled_notional_usd": modeled_notional,
        "modeled_risk_at_stop_usd": modeled_notional * stop_fraction,
        "maximum_effective_exposure": maximum_effective_exposure,
    }

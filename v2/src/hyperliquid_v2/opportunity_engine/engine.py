from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from hyperliquid_v2.domain.models import (
    DecisionAction,
    DecisionPacket,
    DecisionType,
    RiskEnvelope,
    TradeThesis,
)
from hyperliquid_v2.market_data.features import FeatureSnapshot
from hyperliquid_v2.market_data.momentum import PumpMomentum


@dataclass(frozen=True)
class OpportunityPolicy:
    min_data_quality: float = 0.70
    min_volume_ratio: float = 0.90
    min_rsi: float = 48.0
    max_rsi: float = 74.0
    max_distance_ema_atr: float = 1.20
    min_reward_risk: float = 1.50
    max_short_term_velocity_bps: float = 28.0
    thesis_horizon_minutes: int = 90


@dataclass(frozen=True)
class OpportunityAssessment:
    candidate: bool
    setup_family: str | None
    reasons: tuple[str, ...]
    thesis: TradeThesis | None
    stop_distance_pct: float | None
    reward_risk: float | None


class OpportunityEngine:
    """Create shadow entry theses from completed multi-timeframe state.

    It never sends orders and currently supports long theses only.
    """

    def __init__(self, policy: OpportunityPolicy = OpportunityPolicy()) -> None:
        self.policy = policy

    def assess(
        self,
        feature: FeatureSnapshot,
        pump: PumpMomentum,
        recent_15m_lows: tuple[float, ...] = (),
    ) -> OpportunityAssessment:
        policy = self.policy
        failures: list[str] = []
        required = {
            "ema20": feature.ema20_15m,
            "ema50": feature.ema50_15m,
            "atr14": feature.atr14_15m,
            "rsi14": feature.rsi14_15m,
            "volume_ratio": feature.volume_ratio_15m,
            "momentum_1h": feature.momentum_1h_pct,
            "donchian": feature.donchian_high_20_15m,
        }
        if any(value is None for value in required.values()):
            return OpportunityAssessment(
                False,
                None,
                ("insufficient_completed_candle_history",),
                None,
                None,
                None,
            )
        if feature.data_quality_score < policy.min_data_quality:
            failures.append("data_quality_below_threshold")
        if not (
            feature.mid_price
            > float(feature.ema20_15m)
            > float(feature.ema50_15m)
        ):
            failures.append("trend_structure_not_aligned")
        if not policy.min_rsi <= float(feature.rsi14_15m) <= policy.max_rsi:
            failures.append("rsi_outside_entry_band")
        if float(feature.volume_ratio_15m) < policy.min_volume_ratio:
            failures.append("volume_ratio_below_threshold")
        if float(feature.momentum_1h_pct) <= 0:
            failures.append("one_hour_momentum_not_positive")
        distance_atr = (
            feature.mid_price - float(feature.ema20_15m)
        ) / float(feature.atr14_15m)
        if distance_atr > policy.max_distance_ema_atr:
            failures.append("price_too_extended_from_ema20")
        if (
            feature.price_velocity_bps_15s
            > policy.max_short_term_velocity_bps
            and feature.price_acceleration_bps > 0
        ):
            failures.append("short_term_chase_risk")
        if pump.phase == "exhaustion" or pump.reversal_probability > 0.62:
            failures.append("pump_exhaustion_or_reversal_risk")

        prior_high = float(feature.donchian_high_20_15m)
        breakout = feature.mid_price >= prior_high
        near_retest = (
            abs(feature.mid_price - prior_high) / feature.mid_price <= 0.0025
        )
        if not breakout and not near_retest:
            failures.append("no_breakout_or_retest")
        setup_family = (
            "breakout_continuation" if breakout else "breakout_retest"
        )

        swing_low = (
            recent_15m_lows[-1]
            if recent_15m_lows
            else float(feature.ema20_15m)
        )
        invalidation = max(
            0.0000001,
            max(float(feature.ema20_15m), swing_low),
        )
        stop_pct = (
            (feature.mid_price - invalidation)
            / feature.mid_price
            * 100.0
        )
        if stop_pct <= 0.10:
            invalidation = feature.mid_price * 0.997
            stop_pct = 0.30
        if stop_pct > 1.50:
            failures.append("stop_distance_too_wide")
        expected_upside_pct = max(
            stop_pct * policy.min_reward_risk,
            float(feature.atr14_15m)
            / feature.mid_price
            * 100.0
            * 1.5,
        )
        reward_risk = (
            expected_upside_pct / stop_pct
            if stop_pct > 0
            else None
        )
        if (
            reward_risk is None
            or reward_risk < policy.min_reward_risk
        ):
            failures.append("reward_risk_below_threshold")

        if failures:
            return OpportunityAssessment(
                False,
                setup_family,
                tuple(failures),
                None,
                stop_pct,
                reward_risk,
            )
        now = datetime.fromtimestamp(
            feature.observed_at_ms / 1000,
            tz=timezone.utc,
        )
        thesis = TradeThesis(
            thesis_id=str(uuid4()),
            symbol=feature.symbol,
            direction="long",
            setup_family=setup_family,
            regime="intraday_trend_continuation",
            entry_reference_price=feature.mid_price,
            invalidation_price=invalidation,
            expected_horizon_minutes=policy.thesis_horizon_minutes,
            expected_upside_r=reward_risk or policy.min_reward_risk,
            expected_downside_r=1.0,
            expiry_at=now + timedelta(minutes=30),
            required_market_behaviour=(
                "hold_breakout_or_retest",
                "no_momentum_exhaustion",
                "positive_one_hour_momentum",
            ),
        )
        return OpportunityAssessment(
            True,
            setup_family,
            ("entry_thesis_supported",),
            thesis,
            stop_pct,
            reward_risk,
        )

    def packet(
        self,
        assessment: OpportunityAssessment,
        feature: FeatureSnapshot,
        pump: PumpMomentum,
        *,
        equity_usd: float,
        max_risk_fraction: float,
        max_effective_exposure: float,
        quant_evidence,
        execution_cost_bps: float,
    ) -> DecisionPacket:
        if (
            not assessment.candidate
            or assessment.thesis is None
            or assessment.stop_distance_pct is None
        ):
            raise ValueError("assessment must be a candidate")
        stop_fraction = assessment.stop_distance_pct / 100.0
        risk_usd = max(0.0, equity_usd * max_risk_fraction)
        exposure = min(
            max_effective_exposure,
            max_risk_fraction / stop_fraction
            if stop_fraction > 0
            else 0.0,
        )
        envelope = RiskEnvelope(
            maximum_risk_usd=risk_usd,
            maximum_effective_exposure=exposure,
            allowed_leverage=(1, 2, 3),
            maximum_balance_portion=exposure,
            minimum_stop_distance_pct=max(
                0.10,
                assessment.stop_distance_pct * 0.75,
            ),
            maximum_stop_distance_pct=min(
                1.50,
                assessment.stop_distance_pct * 1.25,
            ),
            allowed_actions=(
                DecisionAction.OPEN,
                DecisionAction.HOLD,
                DecisionAction.NO_TRADE,
            ),
        )
        return DecisionPacket(
            decision_id=str(uuid4()),
            decision_type=DecisionType.ENTRY_REVIEW,
            market_timestamp=datetime.fromtimestamp(
                feature.observed_at_ms / 1000,
                tz=timezone.utc,
            ),
            symbol=feature.symbol,
            trade_thesis=assessment.thesis,
            position_state=None,
            market_state=feature.to_dict(),
            pump_momentum=pump.to_dict(),
            quant_evidence=quant_evidence,
            risk_envelope=envelope,
            execution_costs={
                "round_trip_bps": execution_cost_bps,
            },
            data_quality={
                "score": feature.data_quality_score,
                "flags": list(feature.data_quality_flags),
            },
            allowed_actions=envelope.allowed_actions,
        )

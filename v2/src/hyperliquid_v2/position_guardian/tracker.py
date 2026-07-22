from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from hyperliquid_v2.domain.models import (
    DecisionAction,
    DecisionPacket,
    DecisionType,
    PositionPhase,
    PositionState,
    RiskEnvelope,
    TradeThesis,
)
from hyperliquid_v2.market_data.features import FeatureSnapshot
from hyperliquid_v2.market_data.momentum import PumpMomentum
from hyperliquid_v2.position_guardian.optimal_exit import (
    ExitAssessment,
    assess_exit,
)
from hyperliquid_v2.position_guardian.state_machine import (
    GuardianTelemetry,
    transition,
)


@dataclass
class _Tracked:
    phase: PositionPhase
    opened_at: datetime
    mfe_r: float
    mae_r: float
    last_size: float


@dataclass(frozen=True)
class GuardianResult:
    position_state: PositionState
    telemetry: GuardianTelemetry
    exit_assessment: ExitAssessment
    thesis: TradeThesis


class PositionTracker:
    def __init__(self) -> None:
        self._state: dict[str, _Tracked] = {}

    def observe(
        self,
        position: dict,
        feature: FeatureSnapshot,
        pump: PumpMomentum,
        *,
        stop_price: float | None,
        default_stop_pct: float,
        round_trip_cost_bps: float,
    ) -> GuardianResult:
        symbol = str(position["symbol"]).upper()
        side = str(position["side"]).lower()
        entry = float(position["entry_price"])
        mark = float(
            position.get("mark_price")
            or feature.mid_price
        )
        size = float(position["size"])
        if side == "long":
            stop = (
                stop_price
                if stop_price and stop_price < entry
                else entry * (1 - default_stop_pct / 100)
            )
            stop_pct = (entry - stop) / entry * 100
            pnl_pct = (mark / entry - 1) * 100
            thesis_valid = mark > stop
        else:
            stop = (
                stop_price
                if stop_price and stop_price > entry
                else entry * (1 + default_stop_pct / 100)
            )
            stop_pct = (stop - entry) / entry * 100
            pnl_pct = (
                (entry / mark - 1) * 100
                if mark
                else -100
            )
            thesis_valid = mark < stop
        current_r = (
            pnl_pct / stop_pct
            if stop_pct > 0
            else 0.0
        )
        tracked = self._state.get(symbol)
        observed_at = datetime.fromtimestamp(
            feature.observed_at_ms / 1000,
            tz=timezone.utc,
        )
        if tracked is None:
            tracked = _Tracked(
                PositionPhase.NEW,
                observed_at,
                current_r,
                current_r,
                size,
            )
        else:
            tracked.mfe_r = max(tracked.mfe_r, current_r)
            tracked.mae_r = min(tracked.mae_r, current_r)
            tracked.last_size = size
        round_trip_cost_r = (
            (round_trip_cost_bps / 100.0) / stop_pct
            if stop_pct > 0
            else 0.0
        )
        telemetry = GuardianTelemetry(
            current_r=current_r,
            mfe_r=tracked.mfe_r,
            mae_r=tracked.mae_r,
            round_trip_cost_r=round_trip_cost_r,
            continuation_probability=pump.continuation_probability,
            reversal_probability=pump.reversal_probability,
            price_velocity=pump.price_velocity,
            price_acceleration=pump.price_acceleration,
            sell_aggression=pump.sell_aggression,
            thesis_valid=thesis_valid,
            closed=False,
        )
        phase = transition(tracked.phase, telemetry)
        tracked.phase = phase
        self._state[symbol] = tracked
        retention = (
            current_r / tracked.mfe_r
            if tracked.mfe_r > 0
            else None
        )
        giveback = (
            (tracked.mfe_r - current_r) / tracked.mfe_r
            if tracked.mfe_r > 0
            else None
        )
        state = PositionState(
            symbol=symbol,
            side=side,
            phase=phase,
            entry_price=entry,
            mark_price=mark,
            size=size,
            current_r=current_r,
            mfe_r=tracked.mfe_r,
            mae_r=tracked.mae_r,
            profit_retention_ratio=retention,
            giveback_ratio=giveback,
            opened_at=tracked.opened_at,
            observed_at=observed_at,
            thesis_valid=thesis_valid,
        )
        expected_upside = max(
            0.10,
            0.75 * pump.continuation_probability,
        )
        expected_giveback = max(
            0.10,
            tracked.mfe_r - current_r,
            0.50 * pump.reversal_probability,
        )
        exit_assessment = assess_exit(
            phase,
            telemetry,
            expected_remaining_upside_r=expected_upside,
            expected_giveback_r=expected_giveback,
            exit_cost_r=round_trip_cost_r / 2,
        )
        thesis = TradeThesis(
            thesis_id=(
                f"inferred-{symbol}-"
                f"{int(tracked.opened_at.timestamp())}"
            ),
            symbol=symbol,
            direction=side,
            setup_family="inherited_live_position",
            regime="observed_live_position",
            entry_reference_price=entry,
            invalidation_price=stop,
            expected_horizon_minutes=90,
            expected_upside_r=expected_upside,
            expected_downside_r=1.0,
            expiry_at=observed_at + timedelta(days=365),
            required_market_behaviour=(
                "thesis_remains_valid",
                "profit_giveback_controlled",
            ),
        )
        return GuardianResult(
            state,
            telemetry,
            exit_assessment,
            thesis,
        )

    def mark_closed(self, symbol: str) -> None:
        self._state.pop(symbol.upper(), None)

    def packet(
        self,
        result: GuardianResult,
        feature: FeatureSnapshot,
        pump: PumpMomentum,
        *,
        equity_usd: float,
        quant_evidence,
        round_trip_cost_bps: float,
    ) -> DecisionPacket:
        allowed = (
            DecisionAction.HOLD,
            DecisionAction.CLOSE,
            DecisionAction.TAKE_PARTIAL,
        )
        envelope = RiskEnvelope(
            maximum_risk_usd=0.0,
            maximum_effective_exposure=0.0,
            allowed_leverage=(1,),
            maximum_balance_portion=0.0,
            minimum_stop_distance_pct=0.0,
            maximum_stop_distance_pct=0.0,
            allowed_actions=allowed,
        )
        market_state = feature.to_dict()
        market_state["exit_assessment"] = {
            "ev_hold_r": result.exit_assessment.ev_hold_r,
            "ev_close_r": result.exit_assessment.ev_close_r,
            "dynamic_profit_floor_r": (
                result.exit_assessment.dynamic_profit_floor_r
            ),
            "close_review": (
                result.exit_assessment.close_review
            ),
            "hard_exit": result.exit_assessment.hard_exit,
            "reason": result.exit_assessment.reason,
        }
        return DecisionPacket(
            decision_id=str(uuid4()),
            decision_type=(
                DecisionType.EMERGENCY_REVIEW
                if result.exit_assessment.hard_exit
                else DecisionType.POSITION_REVIEW
            ),
            market_timestamp=result.position_state.observed_at,
            symbol=result.position_state.symbol,
            trade_thesis=result.thesis,
            position_state=result.position_state,
            market_state=market_state,
            pump_momentum=pump.to_dict(),
            quant_evidence=quant_evidence,
            risk_envelope=envelope,
            execution_costs={
                "round_trip_bps": round_trip_cost_bps,
            },
            data_quality={
                "score": feature.data_quality_score,
                "flags": list(feature.data_quality_flags),
            },
            allowed_actions=allowed,
        )

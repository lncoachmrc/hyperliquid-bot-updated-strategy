from __future__ import annotations

from dataclasses import dataclass

from hyperliquid_v2.domain.models import PositionPhase
from hyperliquid_v2.position_guardian.state_machine import GuardianTelemetry


@dataclass(frozen=True)
class ExitAssessment:
    ev_hold_r: float
    ev_close_r: float
    dynamic_profit_floor_r: float | None
    close_review: bool
    hard_exit: bool
    reason: str


def assess_exit(
    phase: PositionPhase,
    telemetry: GuardianTelemetry,
    *,
    expected_remaining_upside_r: float,
    expected_giveback_r: float,
    exit_cost_r: float,
) -> ExitAssessment:
    """Compare the expected value of holding with the realizable value of closing."""
    if not telemetry.thesis_valid or phase is PositionPhase.THESIS_INVALIDATED:
        return ExitAssessment(
            ev_hold_r=float("-inf"),
            ev_close_r=telemetry.current_r - exit_cost_r,
            dynamic_profit_floor_r=None,
            close_review=True,
            hard_exit=True,
            reason="trade_thesis_invalidated",
        )

    ev_hold = (
        telemetry.continuation_probability * max(0.0, expected_remaining_upside_r)
        - telemetry.reversal_probability * max(0.0, expected_giveback_r)
        - exit_cost_r
    )
    ev_close = telemetry.current_r - exit_cost_r
    floor = dynamic_profit_floor(telemetry)

    if floor is not None and telemetry.current_r <= floor:
        return ExitAssessment(
            ev_hold_r=ev_hold,
            ev_close_r=ev_close,
            dynamic_profit_floor_r=floor,
            close_review=True,
            hard_exit=False,
            reason="dynamic_profit_floor_breached",
        )

    if phase is PositionPhase.EXHAUSTION and ev_hold <= ev_close:
        return ExitAssessment(
            ev_hold_r=ev_hold,
            ev_close_r=ev_close,
            dynamic_profit_floor_r=floor,
            close_review=True,
            hard_exit=False,
            reason="exhaustion_and_close_value_dominates",
        )

    return ExitAssessment(
        ev_hold_r=ev_hold,
        ev_close_r=ev_close,
        dynamic_profit_floor_r=floor,
        close_review=False,
        hard_exit=False,
        reason="hold_value_not_dominated",
    )


def dynamic_profit_floor(telemetry: GuardianTelemetry) -> float | None:
    """Lock a state-dependent share of MFE after a meaningful green excursion."""
    if telemetry.mfe_r < 0.50:
        return None

    retention = (
        0.25
        + 0.35 * (1.0 - telemetry.continuation_probability)
        + 0.25 * telemetry.reversal_probability
        + 0.15 * max(0.0, -telemetry.price_acceleration)
    )
    retention = min(0.85, max(0.25, retention))
    fee_adjusted_breakeven = telemetry.round_trip_cost_r + 0.05
    return max(fee_adjusted_breakeven, telemetry.mfe_r * retention)

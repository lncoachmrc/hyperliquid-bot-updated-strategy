from __future__ import annotations

from dataclasses import dataclass

from hyperliquid_v2.domain.models import PositionPhase


@dataclass(frozen=True)
class GuardianTelemetry:
    current_r: float
    mfe_r: float
    mae_r: float
    round_trip_cost_r: float
    continuation_probability: float
    reversal_probability: float
    price_velocity: float
    price_acceleration: float
    sell_aggression: float
    thesis_valid: bool
    closed: bool = False

    def __post_init__(self) -> None:
        for name in ("continuation_probability", "reversal_probability", "sell_aggression"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


def transition(previous: PositionPhase, telemetry: GuardianTelemetry) -> PositionPhase:
    """Economic/event-driven state transition. No elapsed-time rule is used."""
    if telemetry.closed:
        return PositionPhase.CLOSED
    if not telemetry.thesis_valid:
        return PositionPhase.THESIS_INVALIDATED

    green_threshold = telemetry.round_trip_cost_r + 0.10
    exhaustion = (
        telemetry.mfe_r >= 0.50
        and telemetry.price_acceleration < 0
        and telemetry.reversal_probability > telemetry.continuation_probability
        and telemetry.sell_aggression >= 0.55
    )
    expansion = (
        telemetry.current_r > green_threshold
        and telemetry.price_velocity > 0
        and telemetry.price_acceleration > 0
        and telemetry.continuation_probability >= 0.60
    )

    if exhaustion:
        return PositionPhase.EXHAUSTION
    if expansion:
        return PositionPhase.EXPANSION
    if telemetry.current_r > green_threshold:
        if previous in {PositionPhase.EXPANSION, PositionPhase.CONTINUATION}:
            return PositionPhase.CONTINUATION
        return PositionPhase.PROFITABLE
    if telemetry.current_r < 0:
        return PositionPhase.UNDERWATER
    if previous is PositionPhase.UNDERWATER and telemetry.current_r >= 0:
        return PositionPhase.RECOVERY
    return PositionPhase.NEW

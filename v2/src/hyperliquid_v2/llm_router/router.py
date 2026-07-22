from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hyperliquid_v2.domain.models import (
    DecisionAction,
    DecisionPacket,
    ModelDecision,
)


class DecisionProvider(Protocol):
    name: str
    model: str

    def decide(self, packet: DecisionPacket) -> ModelDecision: ...


@dataclass(frozen=True)
class RouterPolicy:
    challenger_confidence_threshold: float = 0.68
    high_impact_exposure_threshold: float = 0.25


@dataclass(frozen=True)
class RoutedDecision:
    final_action: DecisionAction
    source: str
    primary: ModelDecision
    challenger: ModelDecision | None
    reason: str


class ModelRouter:
    """Route by benchmarked task capability; resolve disagreement conservatively."""

    def __init__(
        self,
        primary: DecisionProvider,
        challenger: DecisionProvider | None,
        policy: RouterPolicy = RouterPolicy(),
    ) -> None:
        self.primary = primary
        self.challenger = challenger
        self.policy = policy

    def decide(self, packet: DecisionPacket) -> RoutedDecision:
        primary_decision = self.primary.decide(packet)
        needs_challenger = self._needs_challenger(packet, primary_decision)
        challenger_decision = (
            self.challenger.decide(packet)
            if needs_challenger and self.challenger is not None
            else None
        )
        return resolve(packet, primary_decision, challenger_decision)

    def _needs_challenger(
        self,
        packet: DecisionPacket,
        primary: ModelDecision,
    ) -> bool:
        high_impact = (
            packet.risk_envelope.maximum_effective_exposure
            >= self.policy.high_impact_exposure_threshold
        )
        ambiguous_value = (
            primary.expected_value_hold_r is not None
            and primary.expected_value_close_r is not None
            and abs(primary.expected_value_hold_r - primary.expected_value_close_r) < 0.10
        )
        return (
            primary.confidence < self.policy.challenger_confidence_threshold
            or high_impact
            or ambiguous_value
        )


def resolve(
    packet: DecisionPacket,
    primary: ModelDecision,
    challenger: ModelDecision | None,
) -> RoutedDecision:
    allowed = set(packet.allowed_actions)
    if primary.action not in allowed:
        return RoutedDecision(
            final_action=_safe_fallback(packet),
            source="risk_contract",
            primary=primary,
            challenger=challenger,
            reason="primary_action_not_allowed",
        )

    if challenger is None:
        return RoutedDecision(
            final_action=primary.action,
            source="primary",
            primary=primary,
            challenger=None,
            reason="primary_decision_within_contract",
        )

    if challenger.action not in allowed:
        return RoutedDecision(
            final_action=_safe_fallback(packet),
            source="risk_contract",
            primary=primary,
            challenger=challenger,
            reason="challenger_action_not_allowed",
        )

    if primary.action == challenger.action:
        return RoutedDecision(
            final_action=primary.action,
            source="agreement",
            primary=primary,
            challenger=challenger,
            reason="independent_models_agree",
        )

    # Never use majority theatre. Disagreement on adding risk resolves to no trade.
    if DecisionAction.OPEN in {primary.action, challenger.action}:
        fallback = DecisionAction.HOLD if DecisionAction.HOLD in allowed else DecisionAction.NO_TRADE
        return RoutedDecision(
            final_action=fallback,
            source="conservative_resolver",
            primary=primary,
            challenger=challenger,
            reason="model_disagreement_on_new_risk",
        )

    # For an existing position, deterministic economic dominance may settle HOLD/CLOSE.
    close_candidates = [
        item
        for item in (primary, challenger)
        if item.action is DecisionAction.CLOSE
        and item.expected_value_close_r is not None
        and item.expected_value_hold_r is not None
        and item.expected_value_close_r >= item.expected_value_hold_r
    ]
    if close_candidates and DecisionAction.CLOSE in allowed:
        return RoutedDecision(
            final_action=DecisionAction.CLOSE,
            source="economic_resolver",
            primary=primary,
            challenger=challenger,
            reason="close_value_dominates_for_at_least_one_independent_model",
        )

    return RoutedDecision(
        final_action=_safe_fallback(packet),
        source="conservative_resolver",
        primary=primary,
        challenger=challenger,
        reason="unresolved_model_disagreement",
    )


def _safe_fallback(packet: DecisionPacket) -> DecisionAction:
    for action in (DecisionAction.HOLD, DecisionAction.NO_TRADE, DecisionAction.CLOSE):
        if action in packet.allowed_actions:
            return action
    return packet.allowed_actions[0]

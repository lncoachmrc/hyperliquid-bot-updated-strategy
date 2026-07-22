from __future__ import annotations

from dataclasses import dataclass

from hyperliquid_v2.domain.models import (
    DecisionAction,
    DecisionPacket,
    ModelDecision,
)
from hyperliquid_v2.llm_router.providers import AsyncDecisionProvider


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


class AsyncModelRouter:
    def __init__(
        self,
        primary: AsyncDecisionProvider,
        challenger: AsyncDecisionProvider | None,
        policy: RouterPolicy = RouterPolicy(),
    ) -> None:
        self.primary = primary
        self.challenger = challenger
        self.policy = policy

    async def decide(
        self,
        packet: DecisionPacket,
    ) -> RoutedDecision:
        primary = await self.primary.decide(packet)
        challenger = (
            await self.challenger.decide(packet)
            if self.challenger
            and self._needs_challenger(packet, primary)
            else None
        )
        return resolve(packet, primary, challenger)

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
            and abs(
                primary.expected_value_hold_r
                - primary.expected_value_close_r
            )
            < 0.10
        )
        return (
            primary.confidence
            < self.policy.challenger_confidence_threshold
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
            _fallback(packet),
            "risk_contract",
            primary,
            challenger,
            "primary_action_not_allowed",
        )
    if not _sizing_within_contract(packet, primary):
        return RoutedDecision(
            _fallback(packet),
            "risk_contract",
            primary,
            challenger,
            "primary_sizing_outside_envelope",
        )
    if challenger is None:
        return RoutedDecision(
            primary.action,
            "primary",
            primary,
            None,
            "primary_decision_within_contract",
        )
    if challenger.action not in allowed:
        return RoutedDecision(
            _fallback(packet),
            "risk_contract",
            primary,
            challenger,
            "challenger_action_not_allowed",
        )
    if not _sizing_within_contract(packet, challenger):
        return RoutedDecision(
            _fallback(packet),
            "risk_contract",
            primary,
            challenger,
            "challenger_sizing_outside_envelope",
        )
    if primary.action == challenger.action:
        return RoutedDecision(
            primary.action,
            "agreement",
            primary,
            challenger,
            "independent_models_agree",
        )
    if DecisionAction.OPEN in {
        primary.action,
        challenger.action,
    }:
        fallback = (
            DecisionAction.HOLD
            if DecisionAction.HOLD in allowed
            else DecisionAction.NO_TRADE
        )
        return RoutedDecision(
            fallback,
            "conservative_resolver",
            primary,
            challenger,
            "model_disagreement_on_new_risk",
        )
    close_candidates = [
        decision
        for decision in (primary, challenger)
        if decision.action is DecisionAction.CLOSE
        and decision.expected_value_close_r is not None
        and decision.expected_value_hold_r is not None
        and decision.expected_value_close_r
        >= decision.expected_value_hold_r
    ]
    if close_candidates and DecisionAction.CLOSE in allowed:
        return RoutedDecision(
            DecisionAction.CLOSE,
            "economic_resolver",
            primary,
            challenger,
            "close_value_dominates",
        )
    return RoutedDecision(
        _fallback(packet),
        "conservative_resolver",
        primary,
        challenger,
        "unresolved_model_disagreement",
    )


def _fallback(packet: DecisionPacket) -> DecisionAction:
    for action in (
        DecisionAction.HOLD,
        DecisionAction.NO_TRADE,
        DecisionAction.CLOSE,
    ):
        if action in packet.allowed_actions:
            return action
    return packet.allowed_actions[0]


def _sizing_within_contract(
    packet: DecisionPacket,
    decision: ModelDecision,
) -> bool:
    if decision.action is not DecisionAction.OPEN:
        return True
    envelope = packet.risk_envelope
    return (
        decision.selected_leverage
        in envelope.allowed_leverage
        and decision.selected_effective_exposure
        is not None
        and 0
        < decision.selected_effective_exposure
        <= envelope.maximum_effective_exposure
        and decision.selected_balance_portion
        is not None
        and 0
        < decision.selected_balance_portion
        <= envelope.maximum_balance_portion
        and decision.selected_stop_distance_pct
        is not None
        and envelope.minimum_stop_distance_pct
        <= decision.selected_stop_distance_pct
        <= envelope.maximum_stop_distance_pct
    )

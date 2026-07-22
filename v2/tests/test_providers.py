from datetime import datetime, timezone

import pytest

from hyperliquid_v2.domain.models import (
    DecisionAction,
    DecisionPacket,
    DecisionType,
    ModelDecision,
    RiskEnvelope,
)
from hyperliquid_v2.llm_router.async_router import AsyncModelRouter
from hyperliquid_v2.llm_router.providers import DeterministicShadowProvider


def packet():
    risk = RiskEnvelope(
        maximum_risk_usd=10,
        maximum_effective_exposure=0.2,
        allowed_leverage=(1, 2),
        maximum_balance_portion=0.2,
        minimum_stop_distance_pct=0.3,
        maximum_stop_distance_pct=0.8,
        allowed_actions=(DecisionAction.OPEN, DecisionAction.HOLD, DecisionAction.NO_TRADE),
    )
    return DecisionPacket(
        decision_id="decision",
        decision_type=DecisionType.ENTRY_REVIEW,
        market_timestamp=datetime.now(timezone.utc),
        symbol="BTC",
        trade_thesis=None,
        position_state=None,
        market_state={},
        pump_momentum={},
        quant_evidence=None,
        risk_envelope=risk,
        execution_costs={},
        data_quality={},
        allowed_actions=risk.allowed_actions,
    )


@pytest.mark.asyncio
async def test_deterministic_fallback_refuses_entry_without_operational_evidence():
    decision = await DeterministicShadowProvider().decide(packet())
    assert decision.action is DecisionAction.NO_TRADE


class InvalidOpenProvider:
    name = "invalid"
    model = "invalid"

    async def decide(self, _packet):
        return ModelDecision(
            provider=self.name,
            model=self.model,
            action=DecisionAction.OPEN,
            confidence=0.9,
            expected_value_hold_r=0.2,
            expected_value_close_r=0.0,
            thesis_status="valid",
            main_reason="test",
            selected_leverage=10,
            selected_effective_exposure=2.0,
            selected_balance_portion=1.0,
            selected_stop_distance_pct=2.0,
        )


class FailingProvider:
    name = "failing"
    model = "failing"

    async def decide(self, _packet):
        raise RuntimeError("provider outage")


@pytest.mark.asyncio
async def test_router_rejects_open_sizing_outside_risk_envelope():
    routed = await AsyncModelRouter(InvalidOpenProvider(), None).decide(packet())
    assert routed.final_action is DecisionAction.HOLD
    assert routed.reason == "primary_sizing_outside_envelope"


@pytest.mark.asyncio
async def test_router_survives_primary_provider_outage():
    routed = await AsyncModelRouter(FailingProvider(), None).decide(packet())
    assert routed.final_action is DecisionAction.NO_TRADE
    assert routed.source == "provider_failure_fallback"

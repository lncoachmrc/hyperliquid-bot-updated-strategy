import json
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
from hyperliquid_v2.llm_router.providers import (
    DeterministicShadowProvider,
    HttpDecisionProvider,
)


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


class StubResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(self.payload),
                    }
                }
            ]
        }


class StubDeepSeekClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return StubResponse(self.payloads.pop(0))

    async def aclose(self):
        return None


def deepseek_payload(**overrides):
    payload = {
        "action": "NO_TRADE",
        "confidence": 0.82,
        "expected_value_hold_r": -0.2,
        "expected_value_close_r": 0.0,
        "thesis_status": "review",
        "main_reason": "negative expected value",
        "evidence_used": ["quant_evidence"],
        "partial_fraction": None,
        "selected_leverage": None,
        "selected_effective_exposure": None,
        "selected_balance_portion": None,
        "selected_stop_distance_pct": None,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_deepseek_repairs_once_when_confidence_is_missing(caplog):
    first = deepseek_payload()
    first.pop("confidence")
    repaired = deepseek_payload(confidence=0.74)
    provider = HttpDecisionProvider(
        "deepseek",
        "deepseek-v4-flash",
        api_key="test-key",
    )
    await provider.client.aclose()
    provider.client = StubDeepSeekClient([first, repaired])

    decision = await provider.decide(packet())

    assert decision.action is DecisionAction.NO_TRADE
    assert decision.confidence == pytest.approx(0.74)
    assert len(provider.client.calls) == 2
    assert "confidence" in caplog.text
    first_request = provider.client.calls[0][1]["json"]
    repair_request = provider.client.calls[1][1]["json"]
    assert '"confidence"' in first_request["messages"][0]["content"]
    assert "confidence" in repair_request["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_deepseek_fails_after_single_incomplete_repair_attempt():
    incomplete = deepseek_payload()
    incomplete.pop("confidence")
    provider = HttpDecisionProvider(
        "deepseek",
        "deepseek-v4-flash",
        api_key="test-key",
    )
    await provider.client.aclose()
    provider.client = StubDeepSeekClient([incomplete, incomplete])

    with pytest.raises(
        ValueError,
        match="after one repair attempt: confidence",
    ):
        await provider.decide(packet())

    assert len(provider.client.calls) == 2

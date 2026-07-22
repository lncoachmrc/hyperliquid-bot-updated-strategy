from datetime import datetime, timezone

from hyperliquid_v2.domain.models import (
    DecisionAction,
    DecisionPacket,
    DecisionType,
    ModelDecision,
    RiskEnvelope,
)
from hyperliquid_v2.llm_router.router import resolve


def packet(*actions: DecisionAction) -> DecisionPacket:
    risk = RiskEnvelope(
        maximum_risk_usd=15,
        maximum_effective_exposure=0.2,
        allowed_leverage=(1, 2),
        maximum_balance_portion=0.2,
        minimum_stop_distance_pct=0.2,
        maximum_stop_distance_pct=1.0,
        allowed_actions=actions,
    )
    return DecisionPacket(
        decision_id="decision-1",
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
        allowed_actions=actions,
    )


def decision(action: DecisionAction, hold=None, close=None) -> ModelDecision:
    return ModelDecision(
        provider="test",
        model="test-model",
        action=action,
        confidence=0.7,
        expected_value_hold_r=hold,
        expected_value_close_r=close,
        thesis_status="valid",
        main_reason="test",
    )


def test_disagreement_on_open_resolves_to_hold():
    result = resolve(
        packet(DecisionAction.OPEN, DecisionAction.HOLD),
        decision(DecisionAction.OPEN),
        decision(DecisionAction.HOLD),
    )
    assert result.final_action is DecisionAction.HOLD
    assert result.reason == "model_disagreement_on_new_risk"


def test_close_can_win_when_close_value_dominates():
    result = resolve(
        packet(DecisionAction.HOLD, DecisionAction.CLOSE),
        decision(DecisionAction.HOLD, hold=0.1, close=0.2),
        decision(DecisionAction.CLOSE, hold=-0.1, close=0.5),
    )
    assert result.final_action is DecisionAction.CLOSE

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping


class DecisionAction(StrEnum):
    OPEN = "OPEN"
    HOLD = "HOLD"
    CLOSE = "CLOSE"
    TAKE_PARTIAL = "TAKE_PARTIAL"
    NO_TRADE = "NO_TRADE"


class DecisionType(StrEnum):
    ENTRY_REVIEW = "entry_review"
    POSITION_REVIEW = "position_review"
    EMERGENCY_REVIEW = "emergency_review"


class PositionPhase(StrEnum):
    NEW = "NEW"
    UNDERWATER = "UNDERWATER"
    RECOVERY = "RECOVERY"
    PROFITABLE = "PROFITABLE"
    EXPANSION = "EXPANSION"
    CONTINUATION = "CONTINUATION"
    EXHAUSTION = "EXHAUSTION"
    PROFIT_LOCKED = "PROFIT_LOCKED"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class TradeThesis:
    thesis_id: str
    symbol: str
    direction: str
    setup_family: str
    regime: str
    entry_reference_price: float
    invalidation_price: float
    expected_horizon_minutes: int
    expected_upside_r: float
    expected_downside_r: float
    expiry_at: datetime
    required_market_behaviour: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.direction not in {"long", "short"}:
            raise ValueError("direction must be long or short")
        if self.entry_reference_price <= 0 or self.invalidation_price <= 0:
            raise ValueError("prices must be positive")
        if self.expected_horizon_minutes <= 0:
            raise ValueError("expected_horizon_minutes must be positive")


@dataclass(frozen=True)
class PositionState:
    symbol: str
    side: str
    phase: PositionPhase
    entry_price: float
    mark_price: float
    size: float
    current_r: float
    mfe_r: float
    mae_r: float
    profit_retention_ratio: float | None
    giveback_ratio: float | None
    opened_at: datetime
    observed_at: datetime
    thesis_valid: bool


@dataclass(frozen=True)
class QuantEvidence:
    setup_family: str
    comparable_samples: int
    probability_positive_15m: float | None
    probability_positive_60m: float | None
    probability_positive_180m: float | None
    median_return_15m_pct: float | None
    median_return_60m_pct: float | None
    median_return_180m_pct: float | None
    median_mfe_r: float | None
    median_mae_r: float | None
    green_to_red_rate: float | None
    expected_net_value_r: float | None
    confidence_interval_r: tuple[float, float] | None
    evidence_quality: str
    operational: bool = False
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskEnvelope:
    maximum_risk_usd: float
    maximum_effective_exposure: float
    allowed_leverage: tuple[int, ...]
    maximum_balance_portion: float
    minimum_stop_distance_pct: float
    maximum_stop_distance_pct: float
    allowed_actions: tuple[DecisionAction, ...]

    def __post_init__(self) -> None:
        if self.maximum_risk_usd < 0:
            raise ValueError("maximum_risk_usd cannot be negative")
        if not self.allowed_leverage or min(self.allowed_leverage) < 1:
            raise ValueError("allowed_leverage must contain positive integers")


@dataclass(frozen=True)
class DecisionPacket:
    decision_id: str
    decision_type: DecisionType
    market_timestamp: datetime
    symbol: str
    trade_thesis: TradeThesis | None
    position_state: PositionState | None
    market_state: Mapping[str, Any]
    pump_momentum: Mapping[str, Any]
    quant_evidence: QuantEvidence | None
    risk_envelope: RiskEnvelope
    execution_costs: Mapping[str, float]
    data_quality: Mapping[str, Any]
    allowed_actions: tuple[DecisionAction, ...]
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if self.market_timestamp.tzinfo is None:
            raise ValueError("market_timestamp must be timezone-aware")
        if not set(self.allowed_actions).issubset(set(self.risk_envelope.allowed_actions)):
            raise ValueError("packet actions must be allowed by the risk envelope")

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class ModelDecision:
    provider: str
    model: str
    action: DecisionAction
    confidence: float
    expected_value_hold_r: float | None
    expected_value_close_r: float | None
    thesis_status: str
    main_reason: str
    evidence_used: tuple[str, ...] = ()
    partial_fraction: float | None = None
    latency_ms: int | None = None
    estimated_cost_usd: float | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.action is DecisionAction.TAKE_PARTIAL:
            if self.partial_fraction is None or not 0 < self.partial_fraction < 1:
                raise ValueError("TAKE_PARTIAL requires partial_fraction between 0 and 1")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value

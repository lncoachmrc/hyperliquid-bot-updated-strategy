from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from hyperliquid_v2.domain.models import (
    DecisionAction,
    DecisionPacket,
    DecisionType,
    RiskEnvelope,
    TradeThesis,
)
from hyperliquid_v2.market_data.features import Candle, FeatureSnapshot
from hyperliquid_v2.market_data.momentum import PumpMomentum


@dataclass(frozen=True)
class FailedBreakoutPolicy:
    lookback_bars: int = 20
    max_arm_bars: int = 3
    min_breakout_extension_atr: float = 0.05
    min_data_quality: float = 0.70
    max_spread_bps: float = 5.0
    min_volume_ratio: float = 0.75
    min_confirmations: int = 2
    retest_tolerance_atr: float = 0.35
    max_entry_distance_atr: float = 1.25
    stop_buffer_atr: float = 0.15
    min_stop_distance_pct: float = 0.10
    max_stop_distance_pct: float = 1.50
    target_r: float = 1.60
    thesis_horizon_minutes: int = 180


@dataclass(frozen=True)
class FailedBreakoutEvent:
    event_key: str
    symbol: str
    original_direction: str
    reversal_direction: str
    breakout_level: float
    breakout_extreme: float
    armed_at: datetime
    failed_at: datetime | None
    entry_mode: str | None
    status: str
    confirmation_count: int
    confirmations: tuple[str, ...]
    reasons: tuple[str, ...]
    breakout_bar_open_ms: int
    failure_bar_open_ms: int | None

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class FailedBreakoutAssessment:
    candidate: bool
    event: FailedBreakoutEvent
    thesis: TradeThesis | None
    stop_distance_pct: float | None
    reward_risk: float | None
    rank: float


@dataclass(frozen=True)
class ReplayPoint:
    observed_at: datetime
    price: float
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class FailedBreakoutReplayResult:
    event_key: str
    symbol: str
    original_direction: str
    reversal_direction: str
    breakout_level: float
    breakout_extreme: float
    armed_at: datetime
    failed_at: datetime
    entry_mode: str
    entry_price: float
    stop_price: float
    target_price: float
    closed_at: datetime
    exit_reason: str
    mfe_r: float
    mae_r: float
    gross_r: float
    cost_r: float
    realized_net_r: float
    outcome: str
    source_sample_key: str

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


class FailedBreakoutEngine:
    """Detect confirmed failed breakouts and build bidirectional shadow theses.

    A breakout is only tradable after a later completed 15-minute candle closes
    back through the broken level and live microstructure confirms the reversal.
    This engine never sends orders.
    """

    def __init__(self, policy: FailedBreakoutPolicy = FailedBreakoutPolicy()) -> None:
        self.policy = policy

    def scan(
        self,
        feature: FeatureSnapshot,
        pump: PumpMomentum,
        candles_15m: Sequence[Candle],
    ) -> tuple[FailedBreakoutAssessment, ...]:
        atr = feature.atr14_15m
        if atr is None or atr <= 0:
            return ()
        completed = tuple(
            candle
            for candle in candles_15m
            if candle.close_time_ms > 0
            and candle.close_time_ms <= feature.observed_at_ms
        )
        minimum = self.policy.lookback_bars + 2
        if len(completed) < minimum:
            return ()

        first_index = max(
            self.policy.lookback_bars,
            len(completed) - self.policy.max_arm_bars - 1,
        )
        assessments: list[FailedBreakoutAssessment] = []
        for index in range(first_index, len(completed) - 1):
            breakout_bar = completed[index]
            history = completed[index - self.policy.lookback_bars : index]
            prior_high = max(candle.high for candle in history)
            prior_low = min(candle.low for candle in history)
            if self._is_upside_breakout(breakout_bar, prior_high, atr):
                assessments.append(
                    self._assess_breakout(
                        feature,
                        pump,
                        completed,
                        index,
                        original_direction="upside",
                        breakout_level=prior_high,
                    )
                )
            if self._is_downside_breakout(breakout_bar, prior_low, atr):
                assessments.append(
                    self._assess_breakout(
                        feature,
                        pump,
                        completed,
                        index,
                        original_direction="downside",
                        breakout_level=prior_low,
                    )
                )
        return tuple(assessments)

    def packet(
        self,
        assessment: FailedBreakoutAssessment,
        feature: FeatureSnapshot,
        pump: PumpMomentum,
        *,
        equity_usd: float,
        max_risk_fraction: float,
        max_effective_exposure: float,
        quant_evidence: Any,
        execution_cost_bps: float,
    ) -> DecisionPacket:
        if (
            not assessment.candidate
            or assessment.thesis is None
            or assessment.stop_distance_pct is None
        ):
            raise ValueError("assessment must be a confirmed failed-breakout candidate")
        stop_fraction = assessment.stop_distance_pct / 100.0
        risk_fraction = max(0.0, max_risk_fraction)
        risk_usd = max(0.0, equity_usd * risk_fraction)
        exposure = min(
            max_effective_exposure,
            risk_fraction / stop_fraction if stop_fraction > 0 else 0.0,
        )
        envelope = RiskEnvelope(
            maximum_risk_usd=risk_usd,
            maximum_effective_exposure=exposure,
            allowed_leverage=(1, 2, 3),
            maximum_balance_portion=exposure,
            minimum_stop_distance_pct=max(
                self.policy.min_stop_distance_pct,
                assessment.stop_distance_pct * 0.85,
            ),
            maximum_stop_distance_pct=min(
                self.policy.max_stop_distance_pct,
                assessment.stop_distance_pct * 1.15,
            ),
            allowed_actions=(
                DecisionAction.OPEN,
                DecisionAction.HOLD,
                DecisionAction.NO_TRADE,
            ),
        )
        market_state = feature.to_dict()
        market_state["failed_breakout_event"] = assessment.event.to_dict()
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
            market_state=market_state,
            pump_momentum=pump.to_dict(),
            quant_evidence=quant_evidence,
            risk_envelope=envelope,
            execution_costs={"round_trip_bps": execution_cost_bps},
            data_quality={
                "score": feature.data_quality_score,
                "flags": list(feature.data_quality_flags),
            },
            allowed_actions=envelope.allowed_actions,
        )

    def _is_upside_breakout(self, bar: Candle, level: float, atr: float) -> bool:
        extension = max(0.0, bar.high - level) / atr
        return bar.high > level and (
            bar.close >= level
            or extension >= self.policy.min_breakout_extension_atr
        )

    def _is_downside_breakout(self, bar: Candle, level: float, atr: float) -> bool:
        extension = max(0.0, level - bar.low) / atr
        return bar.low < level and (
            bar.close <= level
            or extension >= self.policy.min_breakout_extension_atr
        )

    def _assess_breakout(
        self,
        feature: FeatureSnapshot,
        pump: PumpMomentum,
        completed: Sequence[Candle],
        breakout_index: int,
        *,
        original_direction: str,
        breakout_level: float,
    ) -> FailedBreakoutAssessment:
        policy = self.policy
        atr = float(feature.atr14_15m or 0.0)
        breakout_bar = completed[breakout_index]
        later = completed[
            breakout_index + 1 : breakout_index + 1 + policy.max_arm_bars
        ]
        reversal_direction = "short" if original_direction == "upside" else "long"
        event_key = (
            f"fbr|{feature.symbol}|{original_direction}|"
            f"{breakout_bar.open_time_ms}"
        )
        armed_at = _utc_from_ms(breakout_bar.close_time_ms)
        failure_offset = next(
            (
                offset
                for offset, candle in enumerate(later)
                if (
                    candle.close < breakout_level
                    if original_direction == "upside"
                    else candle.close > breakout_level
                )
            ),
            None,
        )
        if failure_offset is None:
            extreme = (
                max([breakout_bar.high, *(candle.high for candle in later)])
                if original_direction == "upside"
                else min([breakout_bar.low, *(candle.low for candle in later)])
            )
            event = FailedBreakoutEvent(
                event_key=event_key,
                symbol=feature.symbol,
                original_direction=original_direction,
                reversal_direction=reversal_direction,
                breakout_level=breakout_level,
                breakout_extreme=extreme,
                armed_at=armed_at,
                failed_at=None,
                entry_mode=None,
                status="armed",
                confirmation_count=0,
                confirmations=(),
                reasons=("awaiting_completed_failure_close",),
                breakout_bar_open_ms=breakout_bar.open_time_ms,
                failure_bar_open_ms=None,
            )
            return FailedBreakoutAssessment(False, event, None, None, None, 0.0)

        failure_index = breakout_index + 1 + failure_offset
        failure_bar = completed[failure_index]
        failed_at = _utc_from_ms(failure_bar.close_time_ms)
        path = completed[breakout_index : failure_index + 1]
        extreme = (
            max(candle.high for candle in path)
            if original_direction == "upside"
            else min(candle.low for candle in path)
        )
        reasons: list[str] = []
        confirmations = self._confirmations(feature, pump, reversal_direction)
        if feature.data_quality_score < policy.min_data_quality:
            reasons.append("data_quality_below_threshold")
        if feature.spread_bps is None or feature.spread_bps > policy.max_spread_bps:
            reasons.append("spread_above_reversal_threshold")
        if (
            feature.volume_ratio_15m is None
            or feature.volume_ratio_15m < policy.min_volume_ratio
        ):
            reasons.append("volume_ratio_below_reversal_threshold")
        if len(confirmations) < policy.min_confirmations:
            reasons.append("insufficient_reversal_confirmations")

        distance_atr = abs(feature.mid_price - breakout_level) / atr
        if distance_atr > policy.max_entry_distance_atr:
            reasons.append("reversal_entry_too_far_from_breakout_level")

        on_reversal_side = (
            feature.mid_price < breakout_level
            if reversal_direction == "short"
            else feature.mid_price > breakout_level
        )
        if not on_reversal_side:
            reasons.append("breakout_level_reclaimed_after_failure")

        entry_mode = self._entry_mode(
            feature,
            failure_bar,
            breakout_level,
            atr,
            reversal_direction,
        )
        if entry_mode is None:
            reasons.append("awaiting_retest_rejection_or_failure_continuation")

        if reversal_direction == "short":
            stop_price = max(
                extreme + policy.stop_buffer_atr * atr,
                feature.mid_price * (1.0 + policy.min_stop_distance_pct / 100.0),
            )
        else:
            stop_price = min(
                extreme - policy.stop_buffer_atr * atr,
                feature.mid_price * (1.0 - policy.min_stop_distance_pct / 100.0),
            )
        stop_pct = abs(stop_price - feature.mid_price) / feature.mid_price * 100.0
        if stop_pct > policy.max_stop_distance_pct:
            reasons.append("reversal_stop_distance_too_wide")

        candidate = not reasons
        status = "candidate" if candidate else "failure_confirmed_waiting"
        event = FailedBreakoutEvent(
            event_key=event_key,
            symbol=feature.symbol,
            original_direction=original_direction,
            reversal_direction=reversal_direction,
            breakout_level=breakout_level,
            breakout_extreme=extreme,
            armed_at=armed_at,
            failed_at=failed_at,
            entry_mode=entry_mode,
            status=status,
            confirmation_count=len(confirmations),
            confirmations=confirmations,
            reasons=tuple(reasons) if reasons else ("failed_breakout_reversal_confirmed",),
            breakout_bar_open_ms=breakout_bar.open_time_ms,
            failure_bar_open_ms=failure_bar.open_time_ms,
        )
        if not candidate:
            rank = float(len(confirmations)) - distance_atr
            return FailedBreakoutAssessment(False, event, None, stop_pct, None, rank)

        now = _utc_from_ms(feature.observed_at_ms)
        thesis = TradeThesis(
            thesis_id=str(uuid4()),
            symbol=feature.symbol,
            direction=reversal_direction,
            setup_family=f"failed_breakout_reversal_{reversal_direction}",
            regime="intraday_failed_breakout_reversal",
            entry_reference_price=feature.mid_price,
            invalidation_price=stop_price,
            expected_horizon_minutes=policy.thesis_horizon_minutes,
            expected_upside_r=policy.target_r,
            expected_downside_r=1.0,
            expiry_at=now + timedelta(minutes=30),
            required_market_behaviour=(
                "broken_level_remains_failed",
                "reversal_microstructure_persists",
                "protective_stop_beyond_breakout_extreme",
            ),
        )
        rank = (
            len(confirmations)
            + (1.0 if entry_mode == "retest_rejection" else 0.5)
            + feature.data_quality_score
            - distance_atr
            - ((feature.spread_bps or 0.0) / 10.0)
        )
        return FailedBreakoutAssessment(
            True,
            event,
            thesis,
            stop_pct,
            policy.target_r,
            rank,
        )

    def _confirmations(
        self,
        feature: FeatureSnapshot,
        pump: PumpMomentum,
        direction: str,
    ) -> tuple[str, ...]:
        confirmations: list[str] = []
        if direction == "short":
            if feature.price_velocity_bps_60s < 0:
                confirmations.append("negative_velocity_60s")
            if feature.price_acceleration_bps < 0:
                confirmations.append("negative_price_acceleration")
            if feature.book_imbalance is not None and feature.book_imbalance < 0:
                confirmations.append("sell_side_book_imbalance")
            if (
                feature.sell_aggression is not None
                and feature.buy_aggression is not None
                and feature.sell_aggression > feature.buy_aggression
            ):
                confirmations.append("sell_aggression_dominates")
            if pump.reversal_probability >= 0.55:
                confirmations.append("pump_model_reversal_probability")
        else:
            if feature.price_velocity_bps_60s > 0:
                confirmations.append("positive_velocity_60s")
            if feature.price_acceleration_bps > 0:
                confirmations.append("positive_price_acceleration")
            if feature.book_imbalance is not None and feature.book_imbalance > 0:
                confirmations.append("buy_side_book_imbalance")
            if (
                feature.buy_aggression is not None
                and feature.sell_aggression is not None
                and feature.buy_aggression > feature.sell_aggression
            ):
                confirmations.append("buy_aggression_dominates")
            if pump.continuation_probability >= 0.55:
                confirmations.append("pump_model_bullish_probability")
        return tuple(confirmations)

    def _entry_mode(
        self,
        feature: FeatureSnapshot,
        failure_bar: Candle,
        level: float,
        atr: float,
        direction: str,
    ) -> str | None:
        distance_atr = abs(feature.mid_price - level) / atr
        if distance_atr <= self.policy.retest_tolerance_atr:
            return "retest_rejection"
        if direction == "short" and feature.mid_price <= failure_bar.low:
            return "failure_continuation"
        if direction == "long" and feature.mid_price >= failure_bar.high:
            return "failure_continuation"
        return None


def replay_blocked_upside_breakout(
    sample: Mapping[str, Any],
    points: Iterable[ReplayPoint],
    *,
    policy: FailedBreakoutPolicy = FailedBreakoutPolicy(),
    round_trip_cost_bps: float = 10.0,
) -> FailedBreakoutReplayResult | None:
    """Counterfactual replay for legacy blocked long breakouts.

    Historical rows do not contain candle OHLC. A completed 15-minute close is
    reconstructed from the final market-feature snapshot in each UTC bucket.
    The result is labelled as replay evidence and never routed to an order path.
    """

    payload = sample.get("payload")
    if not isinstance(payload, Mapping):
        return None
    feature = payload.get("feature")
    if not isinstance(feature, Mapping):
        return None
    level = _number(feature.get("donchian_high_20_15m"))
    atr = _number(feature.get("atr14_15m"))
    baseline = _number(sample.get("baseline_price"))
    observed_at = sample.get("observed_at")
    sample_key = str(sample.get("sample_key") or "")
    symbol = str(sample.get("symbol") or "")
    if (
        level is None
        or atr is None
        or atr <= 0
        or baseline is None
        or baseline < level
        or not isinstance(observed_at, datetime)
        or not sample_key
        or not symbol
    ):
        return None

    ordered = sorted(
        (
            point
            for point in points
            if point.observed_at >= observed_at
            and point.observed_at <= observed_at + timedelta(minutes=180)
        ),
        key=lambda point: point.observed_at,
    )
    if not ordered:
        return None

    bucket_closes: dict[int, ReplayPoint] = {}
    for point in ordered:
        bucket = int(point.observed_at.timestamp()) // 900
        bucket_closes[bucket] = point
    failure_close = next(
        (
            point
            for _, point in sorted(bucket_closes.items())
            if point.price < level
        ),
        None,
    )
    if failure_close is None:
        return None

    entry_point = next(
        (
            point
            for point in ordered
            if point.observed_at >= failure_close.observed_at
            and point.price < level
            and abs(point.price - level) / atr <= policy.max_entry_distance_atr
            and len(_replay_short_confirmations(point.payload))
            >= policy.min_confirmations
        ),
        None,
    )
    if entry_point is None:
        return None

    pre_entry = [
        point.price
        for point in ordered
        if point.observed_at <= entry_point.observed_at
    ]
    extreme = max(pre_entry or [baseline])
    stop_price = max(
        extreme + policy.stop_buffer_atr * atr,
        entry_point.price * (1.0 + policy.min_stop_distance_pct / 100.0),
    )
    stop_distance = stop_price - entry_point.price
    if stop_distance <= 0:
        return None
    stop_pct = stop_distance / entry_point.price * 100.0
    if stop_pct > policy.max_stop_distance_pct:
        return None
    target_price = entry_point.price - policy.target_r * stop_distance
    entry_mode = (
        "retest_rejection"
        if abs(entry_point.price - level) / atr <= policy.retest_tolerance_atr
        else "failure_continuation"
    )

    mfe_r = 0.0
    mae_r = 0.0
    gross_r = 0.0
    closed_at = ordered[-1].observed_at
    exit_reason = "time_stop_180m"
    for point in ordered:
        if point.observed_at < entry_point.observed_at:
            continue
        current_r = (entry_point.price - point.price) / stop_distance
        mfe_r = max(mfe_r, current_r)
        mae_r = min(mae_r, current_r)
        if point.price >= stop_price:
            gross_r = -1.0
            closed_at = point.observed_at
            exit_reason = "protective_stop"
            break
        if point.price <= target_price:
            gross_r = policy.target_r
            closed_at = point.observed_at
            exit_reason = "target_reached"
            break
        gross_r = current_r
        closed_at = point.observed_at

    cost_r = (round_trip_cost_bps / 100.0) / stop_pct
    net_r = gross_r - cost_r
    return FailedBreakoutReplayResult(
        event_key=f"replay|{sample_key}|short",
        symbol=symbol,
        original_direction="upside",
        reversal_direction="short",
        breakout_level=level,
        breakout_extreme=extreme,
        armed_at=observed_at,
        failed_at=failure_close.observed_at,
        entry_mode=entry_mode,
        entry_price=entry_point.price,
        stop_price=stop_price,
        target_price=target_price,
        closed_at=closed_at,
        exit_reason=exit_reason,
        mfe_r=mfe_r,
        mae_r=mae_r,
        gross_r=gross_r,
        cost_r=cost_r,
        realized_net_r=net_r,
        outcome="win" if net_r > 0 else "loss",
        source_sample_key=sample_key,
    )


def _replay_short_confirmations(payload: Mapping[str, Any]) -> tuple[str, ...]:
    confirmations: list[str] = []
    if (_number(payload.get("price_velocity_bps_60s")) or 0.0) < 0:
        confirmations.append("negative_velocity_60s")
    if (_number(payload.get("price_acceleration_bps")) or 0.0) < 0:
        confirmations.append("negative_price_acceleration")
    imbalance = _number(payload.get("book_imbalance"))
    if imbalance is not None and imbalance < 0:
        confirmations.append("sell_side_book_imbalance")
    sell = _number(payload.get("sell_aggression"))
    buy = _number(payload.get("buy_aggression"))
    if sell is not None and buy is not None and sell > buy:
        confirmations.append("sell_aggression_dominates")
    return tuple(confirmations)


def _utc_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value

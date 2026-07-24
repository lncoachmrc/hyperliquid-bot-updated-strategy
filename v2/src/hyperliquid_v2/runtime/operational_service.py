from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from hyperliquid_v2.domain.models import DecisionPacket, DecisionType
from hyperliquid_v2.market_data.hyperliquid import account_equity, parse_positions
from hyperliquid_v2.opportunity_engine.failed_breakout import (
    FailedBreakoutAssessment,
    FailedBreakoutEngine,
    ReplayPoint,
    replay_blocked_upside_breakout,
)
from hyperliquid_v2.runtime.settings import Settings
from hyperliquid_v2.runtime.shadow_service import ShadowService
from hyperliquid_v2.storage.operational import OperationalPostgresRepository

LOGGER = logging.getLogger(__name__)


class OperationalShadowService(ShadowService):
    """Shadow runtime with a bidirectional failed-breakout reversal engine."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.repository = OperationalPostgresRepository(settings.database_url)
        self.failed_breakout = FailedBreakoutEngine()
        self._entry_review_lock = asyncio.Lock()
        self._processed_failed_breakouts: set[str] = set()
        self._last_entry_packet_monotonic = 0.0

    async def start(self) -> None:
        await super().start()
        if not self.settings.failed_breakout_enabled:
            return
        self._processed_failed_breakouts = (
            await self.repository.failed_breakout_processed_keys()
        )
        self.tasks.append(
            asyncio.create_task(
                self._failed_breakout_loop(),
                name="v2-failed-breakout-loop",
            )
        )
        if self.settings.failed_breakout_replay_enabled:
            self.tasks.append(
                asyncio.create_task(
                    self._failed_breakout_replay_once(),
                    name="v2-failed-breakout-replay",
                )
            )

    async def _review_entries(
        self,
        snapshots: dict[str, Any],
        now: datetime,
    ) -> None:
        async with self._entry_review_lock:
            if self._entry_decision_cooldown_active():
                return
            await super()._review_entries(snapshots, now)

    async def _route_and_persist(self, packet: DecisionPacket):
        routed = await super()._route_and_persist(packet)
        if packet.decision_type is DecisionType.ENTRY_REVIEW:
            self._last_entry_packet_monotonic = time.monotonic()
        return routed

    async def _failed_breakout_loop(self) -> None:
        while not self.stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                await self._failed_breakout_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                LOGGER.exception(
                    "Failed-breakout reversal cycle failed; shadow runtime continues"
                )
            elapsed = time.monotonic() - cycle_started
            await self._sleep_or_stop(
                max(
                    1.0,
                    self.settings.failed_breakout_scan_seconds - elapsed,
                )
            )

    async def _failed_breakout_cycle(self) -> None:
        if parse_positions(self.account_state, self.mids):
            return
        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        snapshots = {
            symbol: snapshot
            for symbol in self.settings.symbols
            if (snapshot := self.features.snapshot(symbol, now_ms)) is not None
        }
        if not snapshots:
            return

        async with self._entry_review_lock:
            if self._entry_decision_cooldown_active():
                return
            candidates: list[
                tuple[
                    float,
                    Any,
                    Any,
                    FailedBreakoutAssessment,
                    Any,
                ]
            ] = []
            for symbol, feature in snapshots.items():
                pump = self.momentum.assess(feature)
                assessments = self.failed_breakout.scan(
                    feature,
                    pump,
                    self.features.candles(symbol, "15m"),
                )
                for assessment in assessments:
                    if (
                        assessment.event.event_key
                        in self._processed_failed_breakouts
                    ):
                        continue
                    event_record = self._failed_breakout_record(assessment)
                    await self.repository.save_failed_breakout_event(
                        event_record,
                        payload={
                            "event": assessment.event.to_dict(),
                            "feature": feature.to_dict(),
                            "pump": pump.to_dict(),
                            "shadow_only": True,
                        },
                    )
                    if (
                        not assessment.candidate
                        or assessment.thesis is None
                    ):
                        continue
                    observations = await self.repository.comparable_observations(
                        assessment.thesis.setup_family
                    )
                    evidence = self.quant.build(
                        assessment.thesis.setup_family,
                        observations,
                    )
                    candidates.append(
                        (
                            assessment.rank,
                            feature,
                            pump,
                            assessment,
                            evidence,
                        )
                    )

            if not candidates:
                return
            _, feature, pump, assessment, evidence = max(
                candidates,
                key=lambda item: item[0],
            )
            packet = self.failed_breakout.packet(
                assessment,
                feature,
                pump,
                equity_usd=account_equity(self.account_state),
                max_risk_fraction=min(
                    self.settings.max_risk_fraction,
                    self.settings.failed_breakout_risk_fraction,
                ),
                max_effective_exposure=min(
                    self.settings.max_effective_exposure,
                    self.settings.failed_breakout_max_effective_exposure,
                ),
                quant_evidence=evidence,
                execution_cost_bps=self.settings.round_trip_cost_bps,
            )
            routed = await self._route_and_persist(packet)
            self._processed_failed_breakouts.add(
                assessment.event.event_key
            )
            await self.repository.record_quant_sample(
                assessment.event.event_key,
                packet.market_timestamp,
                feature.symbol,
                assessment.thesis.setup_family,
                feature.mid_price,
                assessment.stop_distance_pct,
                packet.decision_id,
                "selected_candidate",
                {
                    "direction": assessment.thesis.direction,
                    "round_trip_cost_bps": self.settings.round_trip_cost_bps,
                    "failed_breakout_event": assessment.event.to_dict(),
                    "packet_preview": packet.to_dict(),
                },
            )
            event_record = self._failed_breakout_record(assessment)
            event_record["decision_id"] = packet.decision_id
            await self.repository.save_failed_breakout_event(
                event_record,
                status="routed",
                decision_id=packet.decision_id,
                payload={
                    "event": assessment.event.to_dict(),
                    "packet": packet.to_dict(),
                    "routed_action": str(routed.final_action),
                    "routed_source": routed.source,
                    "routed_reason": routed.reason,
                    "shadow_only": True,
                    "order_sent": False,
                },
            )

    async def _failed_breakout_replay_once(self) -> None:
        await self._sleep_or_stop(10)
        if self.stop_event.is_set():
            return
        try:
            samples = (
                await self.repository.blocked_samples_for_failed_breakout_replay()
            )
            replayed = 0
            for sample in samples:
                try:
                    end_at = sample["observed_at"] + timedelta(minutes=180)
                    rows = await self.repository.feature_points_for_replay(
                        str(sample["symbol"]),
                        sample["observed_at"],
                        end_at,
                    )
                    points = []
                    for row in rows:
                        payload = row.get("payload")
                        if not isinstance(payload, dict):
                            continue
                        try:
                            price = float(payload["mid_price"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        points.append(
                            ReplayPoint(
                                observed_at=row["observed_at"],
                                price=price,
                                payload=payload,
                            )
                        )
                    result = replay_blocked_upside_breakout(
                        sample,
                        points,
                        round_trip_cost_bps=self.settings.round_trip_cost_bps,
                    )
                    if result is None:
                        continue
                    await self.repository.save_failed_breakout_event(
                        result.to_dict(),
                        status="replayed",
                        payload={
                            "replay": result.to_dict(),
                            "evidence_mode": (
                                "completed_15m_close_reconstructed_from_"
                                "market_feature_buckets"
                            ),
                            "shadow_only": True,
                            "order_sent": False,
                        },
                    )
                    replayed += 1
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    LOGGER.exception(
                        "Failed-breakout replay failed for sample %s",
                        sample.get("sample_key"),
                    )
            LOGGER.info(
                "Failed-breakout replay completed samples=%s replayed=%s",
                len(samples),
                replayed,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed-breakout replay batch failed")

    def _failed_breakout_record(
        self,
        assessment: FailedBreakoutAssessment,
    ) -> dict[str, Any]:
        record = assessment.event.to_dict()
        if assessment.thesis is None:
            return record
        entry = assessment.thesis.entry_reference_price
        stop = assessment.thesis.invalidation_price
        distance = abs(stop - entry)
        target = (
            entry + assessment.thesis.expected_upside_r * distance
            if assessment.thesis.direction == "long"
            else entry - assessment.thesis.expected_upside_r * distance
        )
        record.update(
            {
                "entry_price": entry,
                "stop_price": stop,
                "target_price": target,
            }
        )
        return record

    def _entry_decision_cooldown_active(self) -> bool:
        if self._last_entry_packet_monotonic <= 0:
            return False
        return (
            time.monotonic() - self._last_entry_packet_monotonic
            < self.settings.entry_decision_cooldown_seconds
        )

    def runtime_status(self) -> dict[str, Any]:
        status = super().runtime_status()
        status["failed_breakout_reversal"] = {
            "enabled": self.settings.failed_breakout_enabled,
            "replay_enabled": self.settings.failed_breakout_replay_enabled,
            "scan_seconds": self.settings.failed_breakout_scan_seconds,
            "processed_events": len(self._processed_failed_breakouts),
            "directions": ["long", "short"],
            "shadow_only": True,
        }
        return status

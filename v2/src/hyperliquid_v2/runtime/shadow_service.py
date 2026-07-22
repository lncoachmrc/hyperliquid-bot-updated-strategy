from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from hyperliquid_v2.domain.models import DecisionAction, DecisionPacket
from hyperliquid_v2.llm_router.async_router import AsyncModelRouter, RoutedDecision
from hyperliquid_v2.llm_router.providers import build_provider
from hyperliquid_v2.market_data.features import FeatureEngine, FeatureSnapshot
from hyperliquid_v2.market_data.hyperliquid import (
    HyperliquidReadOnlyClient,
    account_equity,
    find_protective_stop,
    parse_positions,
)
from hyperliquid_v2.market_data.momentum import PumpMomentumEngine
from hyperliquid_v2.opportunity_engine.engine import OpportunityEngine
from hyperliquid_v2.position_guardian.tracker import GuardianResult, PositionTracker
from hyperliquid_v2.quant_expert.evidence import QuantExpert
from hyperliquid_v2.runtime.settings import Settings
from hyperliquid_v2.storage.postgres import PostgresRepository

LOGGER = logging.getLogger(__name__)


class ShadowService:
    """Operational shadow twin. It observes, reasons and persists; it cannot trade."""

    lock_id = 8_202_602

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repository = PostgresRepository(settings.database_url)
        self.client = HyperliquidReadOnlyClient(
            settings.hyperliquid_http_url,
            settings.hyperliquid_ws_url,
            settings.wallet_address,
            settings.symbols,
        )
        self.features = FeatureEngine(settings.symbols)
        self.momentum = PumpMomentumEngine()
        self.opportunity = OpportunityEngine()
        self.tracker = PositionTracker()
        self.quant = QuantExpert(settings.quant_minimum_samples)
        self.primary = build_provider(settings.primary_provider, settings.primary_model)
        self.challenger = (
            build_provider(settings.challenger_provider, settings.challenger_model)
            if settings.challenger_provider and settings.challenger_model
            else None
        )
        self.observer = (
            build_provider(settings.observer_provider, settings.observer_model)
            if settings.observer_provider and settings.observer_model
            else None
        )
        self.router = AsyncModelRouter(self.primary, self.challenger)
        self.stop_event = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.mids: dict[str, float] = {}
        self.account_state: dict[str, Any] = {}
        self.open_orders: list[dict[str, Any]] = []
        self.account_aux: dict[str, Any] = {"fills": [], "fundings": []}
        self.last_entry_review_ms = 0
        self.last_position_review_ms: dict[str, int] = {}
        self.active_position_samples: dict[str, str] = {}
        self.last_guardian_result: dict[str, GuardianResult] = {}
        self.last_feature_at: datetime | None = None
        self.last_ws_at: datetime | None = None
        self.started_at: datetime | None = None
        self._lock_connection = None

    async def start(self) -> None:
        await self.repository.connect()
        await self._acquire_singleton_lock()
        await self._bootstrap()
        self.started_at = datetime.now(timezone.utc)
        self.tasks = [
            asyncio.create_task(
                self.client.stream_forever(self._handle_ws, self.stop_event),
                name="hyperliquid-websocket",
            ),
            asyncio.create_task(self._feature_loop(), name="v2-feature-loop"),
            asyncio.create_task(self._account_poll_loop(), name="v2-account-poll"),
        ]
        await self.repository.heartbeat(
            "v2-shadow-runtime",
            "started",
            self.runtime_status(),
        )

    async def stop(self) -> None:
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        await self._close_provider(self.primary)
        await self._close_provider(self.challenger)
        await self._close_provider(self.observer)
        await self.client.close()
        await self._release_singleton_lock()
        await self.repository.close()

    async def _acquire_singleton_lock(self) -> None:
        pool = self.repository._require_pool()
        connection = await pool.acquire()
        acquired = await connection.fetchval("SELECT pg_try_advisory_lock($1);", self.lock_id)
        if not acquired:
            await pool.release(connection)
            raise RuntimeError("another V2 shadow runtime owns the PostgreSQL advisory lock")
        self._lock_connection = connection

    async def _release_singleton_lock(self) -> None:
        if self._lock_connection is None or self.repository.pool is None:
            return
        try:
            await self._lock_connection.execute("SELECT pg_advisory_unlock($1);", self.lock_id)
        finally:
            await self.repository.pool.release(self._lock_connection)
            self._lock_connection = None

    async def _bootstrap(self) -> None:
        lookbacks = {
            "1m": 12 * 60 * 60 * 1000,
            "15m": 14 * 24 * 60 * 60 * 1000,
            "1h": 45 * 24 * 60 * 60 * 1000,
        }
        for symbol in self.settings.symbols:
            for interval, lookback in lookbacks.items():
                try:
                    rows = await self.client.bootstrap_candles(symbol, interval, lookback)
                    self.features.bootstrap_candles(symbol, interval, rows)
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Candle bootstrap failed for %s %s", symbol, interval)
        await self._poll_account_once(persist=True)

    async def _handle_ws(self, message: dict[str, Any]) -> None:
        channel = str(message.get("channel") or "")
        data = message.get("data")
        now_ms = int(time.time() * 1000)
        self.last_ws_at = datetime.now(timezone.utc)
        if channel == "allMids":
            raw_mids = data.get("mids") if isinstance(data, dict) and isinstance(data.get("mids"), dict) else data
            if isinstance(raw_mids, dict):
                parsed = {}
                for symbol, value in raw_mids.items():
                    try:
                        parsed[str(symbol).upper()] = float(value)
                    except (TypeError, ValueError):
                        continue
                self.mids.update(parsed)
                self.features.update_mid(now_ms, parsed)
        elif channel == "trades":
            self.features.update_trades(data)
        elif channel == "l2Book":
            self.features.update_book(data)
        elif channel == "candle":
            self.features.update_candle(data)
        elif channel == "activeAssetCtx":
            self.features.update_asset_context(data, now_ms)
        elif channel == "clearinghouseState" and isinstance(data, dict):
            self.account_state = data.get("clearinghouseState") or data
        elif channel == "openOrders":
            if isinstance(data, dict):
                data = data.get("orders") or data.get("openOrders") or []
            self.open_orders = data if isinstance(data, list) else []
        elif channel == "userFills":
            self.account_aux["fills"] = _bounded_events(data)
        elif channel == "userFundings":
            self.account_aux["fundings"] = _bounded_events(data)

    async def _account_poll_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self._poll_account_once(persist=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                LOGGER.exception("V2 account polling failed")
            await self._sleep_or_stop(30)

    async def _poll_account_once(self, persist: bool) -> None:
        account, orders = await asyncio.gather(
            self.client.account_state(),
            self.client.open_orders(),
        )
        self.account_state = account
        self.open_orders = orders
        if persist:
            now = datetime.now(timezone.utc)
            await self.repository.save_account_state(
                now,
                self.settings.wallet_address,
                account_equity(account),
                {
                    "account": account,
                    "open_orders": orders,
                    "auxiliary_events": self.account_aux,
                },
            )

    async def _feature_loop(self) -> None:
        while not self.stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                await self._feature_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                LOGGER.exception("V2 feature cycle failed; shadow runtime continues")
                await self.repository.heartbeat(
                    "v2-shadow-runtime",
                    "degraded",
                    self.runtime_status(),
                )
            elapsed = time.monotonic() - cycle_started
            await self._sleep_or_stop(max(1.0, self.settings.feature_interval_seconds - elapsed))

    async def _feature_cycle(self) -> None:
        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        snapshots: dict[str, FeatureSnapshot] = {}
        for symbol in self.settings.symbols:
            snapshot = self.features.snapshot(symbol, now_ms)
            if snapshot is None:
                continue
            snapshots[symbol] = snapshot
            self.mids[symbol] = snapshot.mid_price
            await self.repository.save_feature(now, symbol, snapshot.to_dict())
        self.last_feature_at = now
        if snapshots:
            await self.repository.mature_quant_samples(now, {symbol: item.mid_price for symbol, item in snapshots.items()})
        positions = parse_positions(self.account_state, self.mids)
        active_symbols = {position["symbol"] for position in positions}
        await self._finalize_disappeared_positions(active_symbols)
        if positions:
            for position in positions:
                feature = snapshots.get(position["symbol"])
                if feature is not None:
                    await self._review_position(position, feature, now_ms)
        elif now_ms - self.last_entry_review_ms >= self.settings.entry_review_seconds * 1000:
            await self._review_entries(snapshots, now)
            self.last_entry_review_ms = now_ms
        await self.repository.heartbeat(
            "v2-shadow-runtime",
            "healthy",
            self.runtime_status(),
        )

    async def _review_entries(self, snapshots: dict[str, FeatureSnapshot], now: datetime) -> None:
        equity = account_equity(self.account_state)
        candidates: list[tuple[float, FeatureSnapshot, Any, Any, Any, str]] = []
        for symbol, feature in snapshots.items():
            pump = self.momentum.assess(feature)
            lows = tuple(candle.low for candle in self.features.candles(symbol, "15m")[-3:])
            assessment = self.opportunity.assess(feature, pump, lows)
            if assessment.setup_family and assessment.stop_distance_pct:
                sample_key = self._entry_sample_key(feature, assessment.setup_family)
                await self.repository.record_quant_sample(
                    sample_key,
                    now,
                    symbol,
                    assessment.setup_family,
                    feature.mid_price,
                    assessment.stop_distance_pct,
                    None,
                    "allowed" if assessment.candidate else "blocked",
                    {
                        "assessment": asdict(assessment),
                        "feature": feature.to_dict(),
                        "pump": pump.to_dict(),
                    },
                )
            if not assessment.candidate or assessment.thesis is None:
                continue
            observations = await self.repository.comparable_observations(assessment.setup_family)
            evidence = self.quant.build(assessment.setup_family, observations)
            rank = (
                pump.continuation_probability
                - pump.reversal_probability
                + feature.data_quality_score
            )
            candidates.append((rank, feature, pump, assessment, evidence, sample_key))
        if not candidates:
            return
        _, feature, pump, assessment, evidence, sample_key = max(candidates, key=lambda item: item[0])
        packet = self.opportunity.packet(
            assessment,
            feature,
            pump,
            equity_usd=equity,
            max_risk_fraction=self.settings.max_risk_fraction,
            max_effective_exposure=self.settings.max_effective_exposure,
            quant_evidence=evidence,
            execution_cost_bps=self.settings.round_trip_cost_bps,
        )
        await self.repository.record_quant_sample(
            sample_key,
            now,
            feature.symbol,
            assessment.setup_family,
            feature.mid_price,
            assessment.stop_distance_pct,
            packet.decision_id,
            "selected_candidate",
            {"packet_preview": packet.to_dict()},
        )
        await self._route_and_persist(packet)

    async def _review_position(self, position: dict[str, Any], feature: FeatureSnapshot, now_ms: int) -> None:
        symbol = position["symbol"]
        pump = self.momentum.assess(feature)
        stop_price = find_protective_stop(self.open_orders, symbol, position["side"])
        result = self.tracker.observe(
            position,
            feature,
            pump,
            stop_price=stop_price,
            default_stop_pct=self.settings.default_stop_pct,
            round_trip_cost_bps=self.settings.round_trip_cost_bps,
        )
        self.last_guardian_result[symbol] = result
        sample_key = self.active_position_samples.get(symbol)
        if sample_key is None:
            sample_key = f"position|{symbol}|{position['entry_price']:.10f}|{int(result.position_state.opened_at.timestamp())}"
            self.active_position_samples[symbol] = sample_key
            stop_pct = abs(position["entry_price"] - result.thesis.invalidation_price) / position["entry_price"] * 100
            await self.repository.record_quant_sample(
                sample_key,
                result.position_state.opened_at,
                symbol,
                "inherited_live_position",
                position["entry_price"],
                stop_pct,
                None,
                "live_position_observed",
                {"position": position},
            )
        await self.repository.save_position_event(
            result.position_state.observed_at,
            symbol,
            str(result.position_state.phase),
            result.position_state.current_r,
            result.position_state.mfe_r,
            result.position_state.mae_r,
            result.exit_assessment.dynamic_profit_floor_r,
            result.exit_assessment.close_review,
            {
                "position_state": asdict(result.position_state),
                "telemetry": asdict(result.telemetry),
                "exit_assessment": asdict(result.exit_assessment),
                "pump": pump.to_dict(),
            },
        )
        due = now_ms - self.last_position_review_ms.get(symbol, 0) >= self.settings.position_review_seconds * 1000
        if not due and not result.exit_assessment.close_review:
            return
        observations = await self.repository.comparable_observations("inherited_live_position")
        evidence = self.quant.build("inherited_live_position", observations)
        packet = self.tracker.packet(
            result,
            feature,
            pump,
            equity_usd=account_equity(self.account_state),
            quant_evidence=evidence,
            round_trip_cost_bps=self.settings.round_trip_cost_bps,
        )
        await self.repository.record_quant_sample(
            sample_key,
            result.position_state.opened_at,
            symbol,
            "inherited_live_position",
            position["entry_price"],
            abs(position["entry_price"] - result.thesis.invalidation_price) / position["entry_price"] * 100,
            packet.decision_id,
            "position_review",
            {"position_state": asdict(result.position_state)},
        )
        await self._route_and_persist(packet)
        self.last_position_review_ms[symbol] = now_ms

    async def _route_and_persist(self, packet: DecisionPacket) -> RoutedDecision:
        await self.repository.save_packet(packet)
        routed = await self.router.decide(packet)
        await self.repository.save_model_decision(packet.decision_id, "primary", routed.primary)
        if routed.challenger is not None:
            await self.repository.save_model_decision(packet.decision_id, "challenger", routed.challenger)
        observer_output = None
        if self.observer is not None:
            try:
                observer_output = await self.observer.decide(packet)
                await self.repository.save_model_decision(packet.decision_id, "observer", observer_output)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Observer model failed; routed shadow decision is unchanged")
        await self.repository.save_shadow_action(
            packet.decision_id,
            packet.symbol,
            str(routed.final_action),
            routed.source,
            routed.reason,
            {
                "routed": {
                    "final_action": str(routed.final_action),
                    "source": routed.source,
                    "reason": routed.reason,
                    "primary": asdict(routed.primary),
                    "challenger": asdict(routed.challenger) if routed.challenger else None,
                    "observer": asdict(observer_output) if observer_output else None,
                },
                "shadow_only": True,
                "order_sent": False,
            },
        )
        LOGGER.info(
            "V2 shadow decision %s %s source=%s reason=%s",
            packet.symbol,
            routed.final_action,
            routed.source,
            routed.reason,
        )
        return routed

    async def _finalize_disappeared_positions(self, active_symbols: set[str]) -> None:
        for symbol in list(self.active_position_samples):
            if symbol in active_symbols:
                continue
            sample_key = self.active_position_samples.pop(symbol)
            result = self.last_guardian_result.pop(symbol, None)
            self.tracker.mark_closed(symbol)
            self.last_position_review_ms.pop(symbol, None)
            if result is not None:
                await self.repository.finalize_quant_sample(
                    sample_key,
                    result.position_state.current_r,
                    result.position_state.current_r < 0,
                )

    def _entry_sample_key(self, feature: FeatureSnapshot, setup_family: str) -> str:
        candles = self.features.candles(feature.symbol, "15m")
        bar = candles[-1].open_time_ms if candles else feature.observed_at_ms // 900_000 * 900_000
        return f"entry|{feature.symbol}|{setup_family}|{bar}"

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _close_provider(self, provider: Any) -> None:
        if provider is not None and hasattr(provider, "close"):
            try:
                await provider.close()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Provider close failed")

    def runtime_status(self) -> dict[str, Any]:
        return {
            "mode": "shadow",
            "live_trading_enabled": False,
            "wallet": self.settings.wallet_address[:6] + "..." + self.settings.wallet_address[-4:],
            "symbols": list(self.settings.symbols),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_websocket_at": self.last_ws_at.isoformat() if self.last_ws_at else None,
            "last_feature_at": self.last_feature_at.isoformat() if self.last_feature_at else None,
            "open_positions": [position["symbol"] for position in parse_positions(self.account_state, self.mids)],
            "primary_provider": getattr(self.primary, "name", "unknown"),
            "primary_model": getattr(self.primary, "model", "unknown"),
            "challenger_provider": getattr(self.challenger, "name", None),
            "observer_provider": getattr(self.observer, "name", None),
        }


def _bounded_events(data: Any) -> list[Any]:
    if isinstance(data, dict):
        data = data.get("fills") or data.get("fundings") or [data]
    if not isinstance(data, list):
        return []
    return data[-100:]

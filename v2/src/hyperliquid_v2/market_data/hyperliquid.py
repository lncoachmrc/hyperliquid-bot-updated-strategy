from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import websockets

LOGGER = logging.getLogger(__name__)
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class HyperliquidReadOnlyClient:
    """Read-only HTTP/WebSocket client. It has no signing or exchange methods."""

    def __init__(
        self,
        http_url: str,
        ws_url: str,
        wallet_address: str,
        symbols: tuple[str, ...],
    ) -> None:
        self.http_url = http_url.rstrip("/")
        self.ws_url = ws_url
        self.wallet_address = wallet_address
        self.symbols = symbols
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0)
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def info(self, payload: dict[str, Any]) -> Any:
        response = await self._http.post(
            f"{self.http_url}/info",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def bootstrap_candles(
        self,
        symbol: str,
        interval: str,
        lookback_ms: int,
    ) -> list[dict[str, Any]]:
        end = int(time.time() * 1000)
        start = end - lookback_ms
        result = await self.info(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": interval,
                    "startTime": start,
                    "endTime": end,
                },
            }
        )
        return result if isinstance(result, list) else []

    async def account_state(self) -> dict[str, Any]:
        result = await self.info(
            {
                "type": "clearinghouseState",
                "user": self.wallet_address,
            }
        )
        return result if isinstance(result, dict) else {}

    async def open_orders(self) -> list[dict[str, Any]]:
        result = await self.info(
            {
                "type": "frontendOpenOrders",
                "user": self.wallet_address,
            }
        )
        return result if isinstance(result, list) else []

    async def stream_forever(
        self,
        handler: EventHandler,
        stop_event: asyncio.Event,
    ) -> None:
        retry = 1.0
        while not stop_event.is_set():
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    max_queue=4096,
                ) as websocket:
                    subscriptions = self.subscriptions()
                    for subscription in subscriptions:
                        await websocket.send(
                            json.dumps(
                                {
                                    "method": "subscribe",
                                    "subscription": subscription,
                                }
                            )
                        )
                    LOGGER.info(
                        "Hyperliquid WebSocket connected with %s subscriptions",
                        len(subscriptions),
                    )
                    retry = 1.0
                    while not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(
                                websocket.recv(),
                                timeout=45,
                            )
                        except asyncio.TimeoutError:
                            await websocket.ping()
                            continue
                        message = json.loads(raw)
                        if isinstance(message, dict):
                            await handler(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "Hyperliquid WebSocket disconnected; retrying in %.1fs",
                    retry,
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=retry)
                except asyncio.TimeoutError:
                    pass
                retry = min(30.0, retry * 2)

    def subscriptions(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = [{"type": "allMids"}]
        for symbol in self.symbols:
            result.extend(
                [
                    {"type": "trades", "coin": symbol},
                    {"type": "l2Book", "coin": symbol},
                    {"type": "candle", "coin": symbol, "interval": "1m"},
                    {"type": "candle", "coin": symbol, "interval": "15m"},
                    {"type": "candle", "coin": symbol, "interval": "1h"},
                    {"type": "activeAssetCtx", "coin": symbol},
                ]
            )
        result.extend(
            [
                {
                    "type": "clearinghouseState",
                    "user": self.wallet_address,
                },
                {
                    "type": "openOrders",
                    "user": self.wallet_address,
                },
                {
                    "type": "userFills",
                    "user": self.wallet_address,
                    "aggregateByTime": True,
                },
                {
                    "type": "userFundings",
                    "user": self.wallet_address,
                },
            ]
        )
        return result


def parse_positions(
    account_state: dict[str, Any],
    mids: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in account_state.get("assetPositions") or []:
        position = item.get("position") if isinstance(item, dict) else None
        if not isinstance(position, dict):
            continue
        symbol = str(position.get("coin") or "").upper()
        try:
            signed_size = float(position.get("szi") or 0)
        except (TypeError, ValueError):
            continue
        if not symbol or signed_size == 0:
            continue
        entry = float(position.get("entryPx") or 0)
        mark = float((mids or {}).get(symbol) or position.get("markPx") or entry)
        result.append(
            {
                "symbol": symbol,
                "side": "long" if signed_size > 0 else "short",
                "signed_size": signed_size,
                "size": abs(signed_size),
                "entry_price": entry,
                "mark_price": mark,
                "leverage": position.get("leverage"),
                "liquidation_price": _to_float(position.get("liquidationPx")),
                "unrealized_pnl": _to_float(position.get("unrealizedPnl")),
                "raw": position,
            }
        )
    return result


def account_equity(account_state: dict[str, Any]) -> float:
    summary = (
        account_state.get("marginSummary")
        or account_state.get("crossMarginSummary")
        or {}
    )
    try:
        return float(summary.get("accountValue") or 0)
    except (TypeError, ValueError):
        return 0.0


def find_protective_stop(
    open_orders: list[dict[str, Any]],
    symbol: str,
    side: str,
) -> float | None:
    candidates: list[float] = []
    for order in open_orders:
        if str(order.get("coin") or "").upper() != symbol.upper():
            continue
        if not bool(order.get("reduceOnly")):
            continue
        trigger = _to_float(order.get("triggerPx"))
        if not trigger or trigger <= 0:
            continue
        order_side = str(order.get("side") or "").upper()
        expected = "A" if side == "long" else "B"
        if order_side and order_side != expected:
            continue
        candidates.append(trigger)
    if not candidates:
        return None
    return max(candidates) if side == "long" else min(candidates)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

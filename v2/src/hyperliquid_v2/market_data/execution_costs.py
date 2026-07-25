"""Normalize public fill and funding events for cost attribution."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping


def normalize_fill(
    raw: Any,
    observed_at: datetime,
) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    symbol = str(raw.get("coin") or raw.get("symbol") or "").upper()
    price = _float(raw.get("px") or raw.get("price"))
    size = _float(raw.get("sz") or raw.get("size"))
    exchange_time = _exchange_time(
        raw.get("time") or raw.get("timestamp")
    )
    if not symbol or not price or price <= 0 or not size or size == 0:
        return None
    size = abs(size)
    notional = price * size
    fee = _float(raw.get("fee"))
    fee_bps = (
        abs(fee) / notional * 10_000
        if fee is not None and notional > 0
        else None
    )
    crossed = _optional_bool(raw.get("crossed"))
    order_id = _optional_text(raw.get("oid") or raw.get("orderId"))
    transaction_hash = _optional_text(raw.get("hash"))
    trade_id = _optional_text(raw.get("tid") or raw.get("tradeId"))
    fill_key = (
        f"tid:{trade_id}"
        if trade_id
        else _stable_key(
            "fill",
            {
                "symbol": symbol,
                "time": exchange_time.isoformat() if exchange_time else None,
                "price": price,
                "size": size,
                "side": raw.get("side"),
                "order_id": order_id,
                "hash": transaction_hash,
            },
        )
    )
    return {
        "fill_key": fill_key,
        "observed_at": observed_at,
        "exchange_time": exchange_time,
        "symbol": symbol,
        "side": _optional_text(raw.get("side")),
        "direction": _optional_text(raw.get("dir")),
        "price": price,
        "size": size,
        "notional_usd": notional,
        "fee_usd": fee,
        "fee_bps": fee_bps,
        "builder_fee_usd": _float(
            _first_present(raw, "builderFee", "builder_fee")
        ),
        "fee_token": _optional_text(
            raw.get("feeToken") or raw.get("fee_token")
        ),
        "is_maker": (not crossed) if crossed is not None else None,
        "closed_pnl_usd": _float(
            _first_present(raw, "closedPnl", "closed_pnl")
        ),
        "order_id": order_id,
        "transaction_hash": transaction_hash,
        "payload": dict(raw),
    }


def normalize_funding(
    raw: Any,
    observed_at: datetime,
) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    delta = raw.get("delta")
    values = delta if isinstance(delta, Mapping) else raw
    symbol = str(
        values.get("coin") or values.get("symbol") or ""
    ).upper()
    exchange_time = _exchange_time(
        raw.get("time")
        or raw.get("timestamp")
        or values.get("time")
    )
    funding_usd = _float(
        _first_present(values, "usdc", "funding", "fundingUsd")
    )
    if not symbol or exchange_time is None or funding_usd is None:
        return None
    funding_key = _stable_key(
        "funding",
        {
            "symbol": symbol,
            "time": exchange_time.isoformat(),
            "funding_usd": funding_usd,
            "size": values.get("szi"),
            "rate": values.get("fundingRate"),
        },
    )
    return {
        "funding_key": funding_key,
        "observed_at": observed_at,
        "exchange_time": exchange_time,
        "symbol": symbol,
        "funding_rate": _float(
            _first_present(
                values,
                "fundingRate",
                "funding_rate",
            )
        ),
        "position_size": _float(
            _first_present(values, "szi", "position_size")
        ),
        "funding_usd": funding_usd,
        "payload": dict(raw),
    }


def _exchange_time(value: Any) -> datetime | None:
    numeric = _float(value)
    if numeric is None or numeric <= 0:
        return None
    # Hyperliquid public user events use epoch milliseconds.
    return datetime.fromtimestamp(numeric / 1000.0, tz=timezone.utc)


def _stable_key(prefix: str, value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_present(
    value: Mapping[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

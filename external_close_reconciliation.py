"""Canonical order-id wrapper for external stop reconciliation.

The historical JSON normalizer may have stored numeric order ids as strings such
as ``500903859646.0``. This wrapper canonicalizes both pending database ids and
Hyperliquid fill ids before delegating to the existing audit-only reconciler.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional

import db_utils
from performance_observability import (
    reconcile_pending_external_closures as _reconcile_pending_external_closures,
    register_external_close_events,
)


def canonical_order_id(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = Decimal(text)
        if numeric.is_finite() and numeric == numeric.to_integral_value():
            return format(numeric.to_integral_value(), "f")
    except (InvalidOperation, ValueError):
        pass
    return text


def normalize_fill_order_ids(fills: Any) -> Any:
    if not isinstance(fills, list):
        return fills
    normalized = []
    for raw_fill in fills:
        if not isinstance(raw_fill, Mapping):
            normalized.append(raw_fill)
            continue
        fill = dict(raw_fill)
        canonical = canonical_order_id(fill.get("oid"))
        if canonical is not None:
            fill["oid"] = canonical
        normalized.append(fill)
    return normalized


def canonicalize_pending_stop_order_ids() -> int:
    """Normalize pending expected stop ids without touching matched audit rows."""
    updated = 0
    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, expected_stop_order_id
                FROM external_close_reconciliations
                WHERE reconciliation_status = 'pending'
                  AND expected_stop_order_id IS NOT NULL;
                """
            )
            rows = cursor.fetchall()
            for reconciliation_id, raw_order_id in rows:
                canonical = canonical_order_id(raw_order_id)
                if canonical is None or canonical == str(raw_order_id):
                    continue
                cursor.execute(
                    """
                    UPDATE external_close_reconciliations
                    SET expected_stop_order_id = %s
                    WHERE id = %s;
                    """,
                    (canonical, reconciliation_id),
                )
                updated += cursor.rowcount
        connection.commit()
    return updated


class _CanonicalInfoProxy:
    def __init__(self, info: Any):
        self._info = info

    def user_fills_by_time(self, *args: Any, **kwargs: Any) -> Any:
        return normalize_fill_order_ids(
            self._info.user_fills_by_time(*args, **kwargs)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._info, name)


class _CanonicalBotProxy:
    def __init__(self, bot: Any):
        self.info = _CanonicalInfoProxy(bot.info)
        self.account_address = bot.account_address


def reconcile_pending_external_closures(
    bot: Any,
    stop_losses: Any = None,
    *,
    observed_at: datetime | str | None = None,
) -> Dict[str, Any]:
    """Register, canonicalize and reconcile while remaining audit-only/fail-open."""
    observation_time = observed_at or datetime.now(timezone.utc)
    try:
        registration = register_external_close_events(
            stop_losses,
            detected_at=observation_time,
        )
        normalized_ids = canonicalize_pending_stop_order_ids()
        result = _reconcile_pending_external_closures(
            _CanonicalBotProxy(bot),
            None,
            observed_at=observation_time,
        )
        result["registration"] = registration
        result["canonicalized_pending_order_ids"] = normalized_ids
        return result
    except Exception as exc:  # noqa: BLE001
        return {
            "events": 0,
            "registered": 0,
            "attempted": 0,
            "matched": 0,
            "expired": 0,
            "error": str(exc),
            "audit_only": True,
        }

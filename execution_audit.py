from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from psycopg2.extras import Json

import db_utils


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS execution_results (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    operation_id BIGINT NOT NULL REFERENCES bot_operations(id) ON DELETE CASCADE,
    pre_snapshot_id BIGINT REFERENCES account_snapshots(id) ON DELETE SET NULL,
    post_snapshot_id BIGINT REFERENCES account_snapshots(id) ON DELETE SET NULL,
    requested_operation TEXT NOT NULL,
    symbol TEXT,
    execution_status TEXT NOT NULL,
    exchange_status TEXT,
    order_id TEXT,
    filled_size NUMERIC(30, 10),
    avg_price NUMERIC(30, 10),
    error_message TEXT,
    raw_payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_results_created_at
    ON execution_results(created_at);
CREATE INDEX IF NOT EXISTS idx_execution_results_operation_id
    ON execution_results(operation_id);
"""


def ensure_execution_audit_schema() -> None:
    """Create the additive execution-audit schema without changing trading logic."""
    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
        connection.commit()


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:  # noqa: BLE001
        return None


def _classify_order_response(raw_response: Any) -> Dict[str, Any]:
    """Classify a Hyperliquid order response using the nested statuses payload.

    Hyperliquid may return top-level ``status=ok`` while an individual order is
    rejected in ``response.data.statuses``.  This helper therefore treats the
    nested statuses as authoritative for order success/failure.
    """
    result: Dict[str, Any] = {
        "execution_status": "unknown",
        "exchange_status": None,
        "order_id": None,
        "filled_size": None,
        "avg_price": None,
        "error_message": None,
    }

    if not isinstance(raw_response, dict):
        result["error_message"] = "Exchange response missing or not a JSON object"
        return result

    exchange_status = raw_response.get("status")
    result["exchange_status"] = str(exchange_status) if exchange_status is not None else None

    if exchange_status != "ok":
        result["execution_status"] = "failed"
        result["error_message"] = str(
            raw_response.get("error")
            or raw_response.get("message")
            or "Hyperliquid returned a non-ok top-level status"
        )
        return result

    response = raw_response.get("response")
    data = response.get("data") if isinstance(response, dict) else None
    statuses = data.get("statuses") if isinstance(data, dict) else None

    if statuses is None:
        result["error_message"] = "No order statuses returned by Hyperliquid"
        return result
    if not isinstance(statuses, list):
        statuses = [statuses]

    errors = []
    filled = []
    resting = []
    explicit_success = False

    for status in statuses:
        if status == "success":
            explicit_success = True
            continue
        if not isinstance(status, dict):
            continue
        if status.get("error") is not None:
            errors.append(str(status.get("error")))
        if isinstance(status.get("filled"), dict):
            filled.append(status["filled"])
        if isinstance(status.get("resting"), dict):
            resting.append(status["resting"])

    if errors:
        result["execution_status"] = "rejected"
        result["error_message"] = "; ".join(errors)
        return result

    if filled:
        primary = filled[0]
        result["execution_status"] = "success"
        if primary.get("oid") is not None:
            result["order_id"] = str(primary.get("oid"))
        result["filled_size"] = _as_float(primary.get("totalSz"))
        result["avg_price"] = _as_float(primary.get("avgPx"))
        return result

    if resting:
        primary = resting[0]
        result["execution_status"] = "accepted"
        if primary.get("oid") is not None:
            result["order_id"] = str(primary.get("oid"))
        return result

    if explicit_success:
        result["execution_status"] = "success"
        return result

    result["error_message"] = "Hyperliquid returned status=ok but no filled/resting/error status"
    return result


def normalize_execution_result(
    decision: Dict[str, Any], raw_response: Any
) -> Dict[str, Any]:
    """Normalize HOLD/OPEN/CLOSE results into a durable execution audit record."""
    operation = str(decision.get("operation") or "unknown")
    symbol = decision.get("symbol")

    if operation == "hold":
        return {
            "requested_operation": operation,
            "symbol": symbol,
            "execution_status": "no_action",
            "exchange_status": "hold",
            "order_id": None,
            "filled_size": None,
            "avg_price": None,
            "error_message": None,
            "raw_exchange_response": raw_response,
        }

    classified = _classify_order_response(raw_response)
    normalized = {
        "requested_operation": operation,
        "symbol": symbol,
        **classified,
        "raw_exchange_response": raw_response,
    }

    if isinstance(raw_response, dict) and "stop_loss_order" in raw_response:
        normalized["stop_loss_audit"] = _classify_order_response(
            raw_response.get("stop_loss_order")
        )

    return normalized


def normalize_execution_exception(
    decision: Dict[str, Any], exc: BaseException
) -> Dict[str, Any]:
    return {
        "requested_operation": str(decision.get("operation") or "unknown"),
        "symbol": decision.get("symbol"),
        "execution_status": "failed",
        "exchange_status": None,
        "order_id": None,
        "filled_size": None,
        "avg_price": None,
        "error_message": str(exc),
        "exception_type": type(exc).__name__,
        "raw_exchange_response": None,
    }


def log_execution_result(
    *,
    operation_id: int,
    pre_snapshot_id: Optional[int],
    decision: Dict[str, Any],
    execution_result: Dict[str, Any],
) -> int:
    payload = {
        "decision": decision,
        "execution": execution_result,
    }
    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO execution_results (
                    operation_id,
                    pre_snapshot_id,
                    requested_operation,
                    symbol,
                    execution_status,
                    exchange_status,
                    order_id,
                    filled_size,
                    avg_price,
                    error_message,
                    raw_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id;
                """,
                (
                    operation_id,
                    pre_snapshot_id,
                    execution_result.get("requested_operation"),
                    execution_result.get("symbol"),
                    execution_result.get("execution_status"),
                    execution_result.get("exchange_status"),
                    execution_result.get("order_id"),
                    execution_result.get("filled_size"),
                    execution_result.get("avg_price"),
                    execution_result.get("error_message"),
                    Json(payload),
                ),
            )
            execution_id = cursor.fetchone()[0]
        connection.commit()
    return execution_id


def attach_post_snapshot(execution_id: int, post_snapshot_id: int) -> None:
    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE execution_results
                SET post_snapshot_id = %s
                WHERE id = %s;
                """,
                (post_snapshot_id, execution_id),
            )
        connection.commit()

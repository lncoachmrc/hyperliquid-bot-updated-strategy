from execution_audit import (
    normalize_execution_exception,
    normalize_execution_result,
)


def _decision(operation="open", symbol="BTC"):
    return {
        "operation": operation,
        "symbol": symbol,
        "direction": "long",
        "target_portion_of_balance": 0.1,
        "leverage": 1,
        "reason": "test",
    }


def test_filled_order_is_success():
    raw = {
        "status": "ok",
        "response": {
            "type": "order",
            "data": {
                "statuses": [
                    {
                        "filled": {
                            "totalSz": "0.02",
                            "avgPx": "1891.4",
                            "oid": 77747314,
                        }
                    }
                ]
            },
        },
    }

    result = normalize_execution_result(_decision(), raw)

    assert result["execution_status"] == "success"
    assert result["exchange_status"] == "ok"
    assert result["order_id"] == "77747314"
    assert result["filled_size"] == 0.02
    assert result["avg_price"] == 1891.4


def test_nested_order_error_is_rejected_even_when_top_level_is_ok():
    raw = {
        "status": "ok",
        "response": {
            "type": "order",
            "data": {
                "statuses": [
                    {"error": "Order must have minimum value of $10."}
                ]
            },
        },
    }

    result = normalize_execution_result(_decision(), raw)

    assert result["execution_status"] == "rejected"
    assert result["exchange_status"] == "ok"
    assert "minimum value" in result["error_message"]


def test_resting_order_is_accepted_not_filled():
    raw = {
        "status": "ok",
        "response": {
            "type": "order",
            "data": {"statuses": [{"resting": {"oid": 77738308}}]},
        },
    }

    result = normalize_execution_result(_decision(), raw)

    assert result["execution_status"] == "accepted"
    assert result["order_id"] == "77738308"


def test_hold_is_explicit_no_action():
    result = normalize_execution_result(
        _decision(operation="hold"),
        {"status": "hold", "message": "No action taken."},
    )

    assert result["execution_status"] == "no_action"
    assert result["exchange_status"] == "hold"
    assert result["order_id"] is None


def test_exchange_exception_is_failed():
    result = normalize_execution_exception(
        _decision(operation="close", symbol="ETH"),
        RuntimeError("signature rejected"),
    )

    assert result["execution_status"] == "failed"
    assert result["requested_operation"] == "close"
    assert result["symbol"] == "ETH"
    assert result["exception_type"] == "RuntimeError"
    assert result["error_message"] == "signature rejected"

import json

import utils


def test_external_stop_detection_returns_operation_id_and_position_metadata(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    previous_positions = [
        {
            "symbol": "ETH",
            "side": "long",
            "size": 0.25,
            "entry_price": 1900.0,
            "mark_price": 1895.0,
            "pnl_usd": -1.25,
        }
    ]
    (tmp_path / "account_status_old.json").write_text(
        json.dumps(previous_positions),
        encoding="utf-8",
    )

    captured = {}

    def fake_log_bot_operation(signal, **kwargs):
        captured["signal"] = signal
        captured["kwargs"] = kwargs
        return 321

    monkeypatch.setattr(utils.db_utils, "log_bot_operation", fake_log_bot_operation)

    result = json.loads(
        utils.check_stop_loss(
            {
                "open_positions": [],
            }
        )
    )

    assert result == [
        {
            "operation_id": 321,
            "symbol": "ETH",
            "direction": "long",
            "size": 0.25,
            "entry_price": 1900.0,
            "last_mark_price": 1895.0,
            "last_observed_pnl_usd": -1.25,
        }
    ]
    assert captured["signal"]["operation"] == "close"
    assert captured["signal"]["reason"] == "Stop loss"


def test_no_external_close_returns_empty_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    position = {
        "symbol": "BTC",
        "side": "long",
        "size": 0.01,
        "entry_price": 66000.0,
        "mark_price": 66100.0,
        "pnl_usd": 1.0,
    }
    (tmp_path / "account_status_old.json").write_text(
        json.dumps([position]),
        encoding="utf-8",
    )
    result = json.loads(utils.check_stop_loss({"open_positions": [position]}))
    assert result == []

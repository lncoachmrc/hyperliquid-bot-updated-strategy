import json

import db_utils
from dotenv import load_dotenv

load_dotenv()


def check_stop_loss(account_status):
    try:
        with open("account_status_old.json", "r", encoding="utf-8") as status_file:
            old_status = json.load(status_file)
        old_symbols = [item["symbol"] for item in old_status]
        new_symbols = [item["symbol"] for item in account_status["open_positions"]]
        triggered = []
        for index, symbol in enumerate(old_symbols):
            if symbol not in new_symbols:
                previous_position = old_status[index]
                signal = {
                    "operation": "close",
                    "symbol": symbol,
                    "direction": previous_position["side"],
                    "target_portion_of_balance": 1.0,
                    "leverage": 1,
                    "reason": "Stop loss",
                    "stop_loss_percent": 1,
                }
                print(
                    f"ATTENZIONE: Rilevata chiusura posizione esterna per {symbol}. "
                    "Registrazione operazione."
                )
                operation_id = db_utils.log_bot_operation(
                    signal,
                    system_prompt="External closure detected",
                    news_text="",
                )
                triggered.append(
                    {
                        "operation_id": operation_id,
                        "symbol": symbol,
                        "direction": previous_position["side"],
                        "size": previous_position.get("size"),
                        "entry_price": previous_position.get("entry_price"),
                        "last_mark_price": previous_position.get("mark_price"),
                        "last_observed_pnl_usd": previous_position.get("pnl_usd"),
                    }
                )
        return json.dumps(triggered)
    except Exception as exc:  # noqa: BLE001
        print(
            f"Errore durante la lettura di account_status_old.json: {exc}. "
            "Nessun SL esterno rilevato."
        )
        return "[]"

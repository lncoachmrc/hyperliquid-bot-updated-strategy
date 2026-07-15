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
                signal = {
                    "operation": "close",
                    "symbol": symbol,
                    "direction": old_status[index]["side"],
                    "target_portion_of_balance": 1.0,
                    "leverage": 1,
                    "reason": "Stop loss",
                    "stop_loss_percent": 1,
                }
                triggered.append(
                    {
                        "symbol": symbol,
                        "direction": old_status[index]["side"],
                        "pnl_usd": old_status[index]["pnl_usd"],
                    }
                )
                print(
                    f"ATTENZIONE: Rilevata chiusura posizione esterna per {symbol}. "
                    "Registrazione operazione."
                )
                db_utils.log_bot_operation(
                    signal,
                    system_prompt="External closure detected",
                    news_text="",
                )
        return json.dumps(triggered)
    except Exception as exc:  # noqa: BLE001
        print(
            f"Errore durante la lettura di account_status_old.json: {exc}. "
            "Nessun SL esterno rilevato."
        )
        return "[]"

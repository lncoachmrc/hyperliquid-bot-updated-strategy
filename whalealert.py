import json
from datetime import datetime

import requests

WHALE_URL = "https://whale-alert.io/data.json?alerts=9&prices=BTC&hodl=bitcoin%2CBTC&potential_profit=bitcoin%2CBTC&average_buy_price=bitcoin%2CBTC&realized_profit=bitcoin%2CBTC&volume=bitcoin%2CBTC&news=true"


def _parsed_alerts():
    response = requests.get(WHALE_URL, timeout=10)
    response.raise_for_status()
    data = response.json()
    output = []
    for alert in data.get("alerts", []):
        parts = alert.split(",", 5)
        if len(parts) < 6:
            continue
        try:
            timestamp = datetime.fromtimestamp(int(parts[0])).strftime(
                "%d/%m/%Y %H:%M:%S"
            )
        except Exception:
            timestamp = "N/A"
        output.append(
            {
                "time": timestamp,
                "emoji": parts[1],
                "amount": parts[2].strip('"'),
                "usd": parts[3].strip('"'),
                "description": parts[4].strip('"'),
                "link": parts[5],
            }
        )
    return output


def get_whale_alerts():
    try:
        alerts = _parsed_alerts()
        if not alerts:
            print("Nessun alert trovato.")
            return
        print("🐋 WHALE ALERTS - MOVIMENTI CRYPTO SIGNIFICATIVI 🐋\n")
        print("=" * 80)
        for alert in alerts:
            print(f"\n{alert['emoji']} ALERT del {alert['time']}")
            print(f"💰 Importo: {alert['amount']}")
            print(f"💵 Valore USD: {alert['usd']}")
            print(f"📝 Descrizione: {alert['description']}")
            print(f"🔗 Link: {alert['link']}")
            print("-" * 80)
    except (requests.exceptions.RequestException, json.JSONDecodeError) as exc:
        print(f"Errore whale alert: {exc}")


def format_whale_alerts_to_string():
    try:
        alerts = _parsed_alerts()
        if not alerts:
            return "Nessun alert trovato."
        result = "🐋 WHALE ALERTS - MOVIMENTI CRYPTO SIGNIFICATIVI 🐋\n\n"
        for alert in alerts:
            result += (
                f"\n{alert['emoji']} ALERT del {alert['time']}\n"
                f"Importo: {alert['amount']}\n"
                f"Valore USD: {alert['usd']}\n"
                f"Descrizione: {alert['description']}\n"
            )
        return result
    except Exception as exc:  # noqa: BLE001
        return f"Errore: {exc}"


if __name__ == "__main__":
    get_whale_alerts()

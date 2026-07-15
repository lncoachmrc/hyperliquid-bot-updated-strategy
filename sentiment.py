import os

import requests
from dotenv import load_dotenv

load_dotenv()
API_URL = "https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical"
API_KEY = os.getenv("CMC_PRO_API_KEY")
INTERVALLO_SECONDI = 3 * 60


def get_latest_fear_and_greed():
    if not API_KEY:
        print("Errore: La variabile d'ambiente CMC_PRO_API_KEY non è impostata.")
        return None
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": API_KEY}
    try:
        response = requests.get(API_URL, headers=headers, params={"limit": 1}, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and "data" in data and data["data"]:
            record = data["data"][0]
            return {
                "valore": record.get("value"),
                "classificazione": record.get("value_classification"),
                "timestamp": record.get("timestamp"),
            }
        print("Errore: La risposta JSON non contiene i dati attesi.")
    except requests.exceptions.RequestException as exc:
        print(f"Errore nella richiesta sentiment: {exc}")
    return None


def get_sentiment() -> str:
    data = get_latest_fear_and_greed()
    if data:
        return (
            "Sentiment del mercato (Fear & Greed Index):\n"
            f"  Valore: {data['valore']}\n"
            f"  Classificazione: {data['classificazione']}\n"
            f"  Timestamp: {data['timestamp']}"
        ), data
    return "Impossibile recuperare il sentiment del mercato.", None

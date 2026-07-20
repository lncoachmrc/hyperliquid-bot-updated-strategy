from indicators import analyze_multiple_tickers
from news_feed import fetch_latest_news
from trading_agent import previsione_trading_agent
from utils import check_stop_loss
from whalealert import format_whale_alerts_to_string
from sentiment import get_sentiment
from forecaster import get_crypto_forecasts
from hyperliquid_trader import HyperLiquidTrader
from runtime_config import env_bool
import os
import json
import string
import db_utils
from dotenv import load_dotenv

load_dotenv()


def normalize_private_key(raw_key):
    """Validate a 32-byte EVM private key without ever logging its value."""
    if not raw_key:
        raise RuntimeError("PRIVATE_KEY mancante nelle variabili d'ambiente")

    value = raw_key.strip()
    if value.startswith(("0x", "0X")):
        value = value[2:]

    if len(value) != 64 or any(char not in string.hexdigits for char in value):
        raise RuntimeError(
            "PRIVATE_KEY non valida: deve contenere esattamente 64 caratteri "
            "esadecimali (prefisso 0x opzionale), senza virgolette o spazi"
        )

    return "0x" + value.lower()


# Railway/runtime controls the Hyperliquid environment. Testnet remains the
# safe default when TESTNET is missing.
TESTNET = env_bool("TESTNET", True)
VERBOSE = True
PRIVATE_KEY = normalize_private_key(os.getenv("PRIVATE_KEY"))
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()

if not WALLET_ADDRESS:
    raise RuntimeError("WALLET_ADDRESS mancante nelle variabili d'ambiente")

try:
    network_name = "TESTNET" if TESTNET else "MAINNET"
    masked_account = (
        f"{WALLET_ADDRESS[:6]}...{WALLET_ADDRESS[-4:]}"
        if len(WALLET_ADDRESS) > 12
        else "configured"
    )
    print(f"[runtime] Hyperliquid network={network_name}, account={masked_account}")

    bot = HyperLiquidTrader(
        secret_key=PRIVATE_KEY,
        account_address=WALLET_ADDRESS,
        testnet=TESTNET,
    )

    # The same network selection must be used for strategy market data and for
    # account/execution, otherwise the LLM can reason on a different venue from
    # the one where orders and balances are read.
    tickers = ["BTC", "ETH", "SOL"]
    indicators_txt, indicators_json = analyze_multiple_tickers(
        tickers,
        testnet=TESTNET,
    )
    news_txt = fetch_latest_news()
    # whale_alerts_txt = format_whale_alerts_to_string()
    sentiment_txt, sentiment_json = get_sentiment()
    forecasts_txt, forecasts_json = get_crypto_forecasts()

    msg_info = f"""<indicatori>\n{indicators_txt}\n</indicatori>\n\n
    <news>\n{news_txt}</news>\n\n
    <sentiment>\n{sentiment_txt}\n</sentiment>\n\n
    <forecast>\n{forecasts_txt}\n</forecast>\n\n"""

    account_status = bot.get_account_status()
    stop_losses = check_stop_loss(account_status)

    snapshot_id = db_utils.log_account_status(account_status)
    print(f"[db_utils] Operazione inserita con id={snapshot_id}")

    # Existing PostgreSQL persistence is reused. No parallel state store is
    # introduced; this is the drawdown term required by the new strategy.
    drawdown_state = db_utils.get_account_drawdown_state(
        current_balance=account_status["balance_usd"]
    )
    portfolio_data = (
        f"{json.dumps(account_status)}\n"
        f"Portfolio drawdown state: {json.dumps(drawdown_state)}\n"
        f"Stop Loss attivati 15 min fa: {stop_losses}"
    )

    with open("system_prompt.txt", "r", encoding="utf-8") as prompt_file:
        system_prompt = prompt_file.read()
    system_prompt = system_prompt.format(portfolio_data, msg_info)

    print("L'agente sta decidendo la sua azione!")
    out = previsione_trading_agent(system_prompt)
    bot.execute_signal(out)

    op_id = db_utils.log_bot_operation(
        out,
        system_prompt=system_prompt,
        indicators=indicators_json,
        news_text=news_txt,
        sentiment=sentiment_json,
        forecasts=forecasts_json,
    )
    print(f"[db_utils] Operazione inserita con id={op_id}")

    account_status = bot.get_account_status()
    with open("account_status_old.json", "w", encoding="utf-8") as status_file:
        json.dump(account_status["open_positions"], status_file, indent=4)
    snapshot_id = db_utils.log_account_status(account_status)
    print(f"[db_utils] Operazione inserita con id={snapshot_id}")

except Exception as e:
    # Preserve the existing error path. Locals are collected defensively so an
    # early failure does not conceal the original exception.
    context = {
        "prompt": locals().get("system_prompt"),
        "tickers": locals().get("tickers"),
        "indicators": locals().get("indicators_json"),
        "news": locals().get("news_txt"),
        "sentiment": locals().get("sentiment_json"),
        "forecasts": locals().get("forecasts_json"),
        "balance": locals().get("account_status"),
    }
    try:
        db_utils.log_error(e, context=context, source="trading_agent")
    except Exception as logging_error:
        print(f"Errore durante il logging DB: {logging_error}")
    print(f"An error occurred: {e}")
    raise

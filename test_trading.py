"""Original manual Hyperliquid test script.

WARNING: this is not an offline unit test. It requires credentials and can send
orders to the configured Hyperliquid testnet account. It is intentionally kept
outside the pytest suite to preserve the original workflow.
"""
from hyperliquid_trader import HyperLiquidTrader
import json
import os

from dotenv import load_dotenv

load_dotenv()
TESTNET = True
VERBOSE = True
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")

if not PRIVATE_KEY or not WALLET_ADDRESS:
    raise RuntimeError("PRIVATE_KEY o WALLET_ADDRESS mancanti nel .env")

bot = HyperLiquidTrader(
    secret_key=PRIVATE_KEY,
    account_address=WALLET_ADDRESS,
    testnet=TESTNET,
)
bot.debug_symbol_limits("BTC")
print(f"🔧 Leva corrente per BTC: {bot.get_current_leverage('BTC')}x")
status = bot.get_account_status()
if status["open_positions"]:
    position = status["open_positions"][0]
    print(
        f"📊 Posizione aperta: {position['size']} {position['symbol']} "
        f"con leva {position.get('leverage', 'N/A')}"
    )


def pretty(obj):
    return json.dumps(obj, indent=2)


print(bot.get_account_status())
print("\n---------------------------------------------------")
print("🔄 Testing HyperLiquidTrader")
print("---------------------------------------------------\n")

signal_open = {
    "operation": "close",
    "symbol": "SOL",
    "direction": "long",
    "target_portion_of_balance": 0.05,
    "leverage": 2,
    "stop_loss_percent": 2,
    "reason": "Test apertura posizione long",
}
print("📌 TEST 1 — OPEN ORDER (BTC LONG)")
try:
    result_open = bot.execute_signal(signal_open)
    print("Risultato OPEN:\n", pretty(result_open))
except Exception as exc:  # noqa: BLE001
    print("❌ ERRORE durante apertura:", exc)
print(bot.get_account_status())

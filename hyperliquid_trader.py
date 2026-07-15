import json
import time
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict

import eth_account
from eth_account.signers.local import LocalAccount
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants


class HyperLiquidTrader:
    """Original Hyperliquid execution adapter.

    The order flow, exchange client, leverage setter, position sizing and stop
    placement remain in the same component and preserve the original public
    interface.
    """

    def __init__(
        self,
        secret_key: str,
        account_address: str,
        testnet: bool = True,
        skip_ws: bool = True,
    ):
        self.secret_key = secret_key
        self.account_address = account_address
        base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.base_url = base_url
        account: LocalAccount = eth_account.Account.from_key(secret_key)
        self.info = Info(base_url, skip_ws=skip_ws)
        self.exchange = Exchange(account, base_url, account_address=account_address)
        self.meta = self.info.meta()

    def _to_hl_size(self, size_decimal: Decimal) -> str:
        size_clamped = size_decimal.quantize(
            Decimal("0.00000001"), rounding=ROUND_DOWN
        )
        return format(size_clamped, "f")

    def _round_price(self, price: float) -> float:
        if price > 5000:
            return round(price, 0)
        if price > 500:
            return round(price, 1)
        if price > 10:
            return round(price, 2)
        if price > 1:
            return round(price, 4)
        return round(price, 5)

    def _validate_order_input(self, order_json: Dict[str, Any]):
        required_fields = [
            "operation",
            "symbol",
            "direction",
            "target_portion_of_balance",
            "leverage",
            "reason",
        ]
        for field in required_fields:
            if field not in order_json:
                raise ValueError(f"Missing required field: {field}")
        if order_json["operation"] not in ("open", "close", "hold"):
            raise ValueError("operation must be 'open', 'close', or 'hold'")
        if order_json["direction"] not in ("long", "short"):
            raise ValueError("direction must be 'long' or 'short'")
        try:
            float(order_json["target_portion_of_balance"])
        except Exception as exc:  # noqa: BLE001
            raise ValueError("target_portion_of_balance must be a number") from exc

    def _get_min_tick_for_symbol(self, symbol: str) -> Decimal:
        for perp in self.meta["universe"]:
            if perp["name"] == symbol:
                return Decimal(str(perp["szDecimals"]))
        return Decimal("0.00000001")

    def _round_size(self, size: Decimal, decimals: int) -> float:
        size = size.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        return float(f"{size:.{decimals}f}")

    def get_current_leverage(self, symbol: str) -> Dict[str, Any]:
        try:
            user_state = self.info.user_state(self.account_address)
            for position in user_state.get("assetPositions", []):
                pos = position.get("position", {})
                coin = pos.get("coin", "")
                if coin == symbol:
                    leverage_info = pos.get("leverage", {})
                    return {
                        "value": leverage_info.get("value", 0),
                        "type": leverage_info.get("type", "unknown"),
                        "coin": coin,
                    }
            cross_leverage = user_state.get("crossLeverage", 20)
            return {
                "value": cross_leverage,
                "type": "cross",
                "coin": symbol,
                "note": "No open position, showing account default",
            }
        except Exception as exc:  # noqa: BLE001
            print(f"Errore ottenendo leva corrente: {exc}")
            return {"value": 20, "type": "unknown", "error": str(exc)}

    def set_leverage_for_symbol(
        self, symbol: str, leverage: int, is_cross: bool = True
    ) -> Dict[str, Any]:
        try:
            print(
                f"🔧 Impostando leva {leverage}x per {symbol} "
                f"({'cross' if is_cross else 'isolated'} margin)"
            )
            result = self.exchange.update_leverage(
                leverage=leverage,
                name=symbol,
                is_cross=is_cross,
            )
            if result.get("status") == "ok":
                print(f"✅ Leva impostata con successo a {leverage}x per {symbol}")
            else:
                print(f"⚠️ Risposta dall'exchange: {result}")
            return result
        except Exception as exc:  # noqa: BLE001
            print(f"❌ Errore impostando leva per {symbol}: {exc}")
            return {"status": "error", "error": str(exc)}

    def _place_stop_loss(
        self, symbol: str, is_buy_sl: bool, size: float, trigger_price: float
    ):
        print(
            f"🛡️ Piazzando STOP LOSS per {symbol} a ${trigger_price} "
            f"(Size: {size})"
        )
        order_type = {
            "trigger": {
                "triggerPx": float(trigger_price),
                "isMarket": True,
                "tpsl": "sl",
            }
        }
        try:
            result = self.exchange.order(
                name=symbol,
                is_buy=is_buy_sl,
                sz=size,
                limit_px=float(trigger_price),
                order_type=order_type,
                reduce_only=True,
            )
            if result["status"] == "ok":
                print(
                    "✅ Stop Loss piazzato: "
                    f"{result['response']['data']['statuses'][0]}"
                )
            else:
                print(f"❌ Errore piazzamento Stop Loss: {result}")
            return result
        except Exception as exc:  # noqa: BLE001
            print(f"❌ Eccezione durante piazzamento SL: {exc}")
            return {"status": "error", "error": str(exc)}

    def execute_signal(self, order_json: Dict[str, Any]) -> Dict[str, Any]:
        self._validate_order_input(order_json)
        operation = order_json["operation"]
        symbol = order_json["symbol"]

        if operation == "hold":
            print(f"[HyperLiquidTrader] HOLD — nessuna azione per {symbol}.")
            return {"status": "hold", "message": "No action taken."}
        if operation == "close":
            print(f"[HyperLiquidTrader] Market CLOSE per {symbol}")
            return self.exchange.market_close(symbol)

        direction = order_json["direction"]
        portion = Decimal(str(order_json["target_portion_of_balance"]))
        leverage = int(order_json.get("leverage", 1))
        stop_loss_percent = order_json.get("stop_loss_percent", 0) / 100
        sl_percent = float(stop_loss_percent)
        sl_price_explicit = order_json.get("stop_loss_price")

        leverage_result = self.set_leverage_for_symbol(
            symbol, leverage, is_cross=True
        )
        if leverage_result.get("status") != "ok":
            print(f"⚠️ Warning leva: {leverage_result}")
        time.sleep(0.5)

        user = self.info.user_state(self.account_address)
        balance_usd = Decimal(str(user["marginSummary"]["accountValue"]))
        if balance_usd <= 0:
            raise RuntimeError("Balance account = 0")

        mids = self.info.all_mids()
        if symbol not in mids:
            raise RuntimeError(f"Symbol {symbol} non presente su HL")
        mark_px = float(mids[symbol])
        mark_px_dec = Decimal(str(mark_px))

        notional = balance_usd * portion * Decimal(str(leverage))
        raw_size = notional / mark_px_dec
        symbol_info = next(
            (p for p in self.meta["universe"] if p["name"] == symbol), None
        )
        if not symbol_info:
            raise RuntimeError(f"Symbol {symbol} non trovato nei metadata")

        min_size = Decimal(str(symbol_info.get("minSz", "0.001")))
        sz_decimals = int(symbol_info.get("szDecimals", 8))
        quantizer = Decimal(10) ** -sz_decimals
        size_decimal = raw_size.quantize(quantizer, rounding=ROUND_DOWN)
        if size_decimal < min_size:
            print(f"⚠️ Size calcolata < min size. Uso min size: {min_size}")
            size_decimal = min_size

        size_float = float(size_decimal)
        is_buy = direction == "long"
        print(
            f"\n[HyperLiquidTrader] Market {'BUY' if is_buy else 'SELL'} "
            f"{size_float} {symbol}\n"
            f"  💰 Prezzo Mark: ${mark_px}\n"
            f"  🎯 Leva: {leverage}x\n"
        )
        result = self.exchange.market_open(
            symbol,
            is_buy,
            size_float,
            None,
            0.01,
        )

        if result["status"] == "ok":
            final_sl_price = None
            if sl_price_explicit:
                final_sl_price = float(sl_price_explicit)
            elif sl_percent > 0:
                print(
                    f"🧮 Calcolo SL automatico: {sl_percent * 100}% da {mark_px}"
                )
                raw_price = (
                    mark_px * (1 - sl_percent)
                    if is_buy
                    else mark_px * (1 + sl_percent)
                )
                final_sl_price = self._round_price(raw_price)

            if final_sl_price:
                sl_result = self._place_stop_loss(
                    symbol=symbol,
                    is_buy_sl=not is_buy,
                    size=size_float,
                    trigger_price=final_sl_price,
                )
                result["stop_loss_order"] = sl_result
                result["stop_loss_price"] = final_sl_price
        return result

    def get_account_status(self) -> Dict[str, Any]:
        data = self.info.user_state(self.account_address)
        balance = float(data["marginSummary"]["accountValue"])
        mids = self.info.all_mids()
        positions = []

        for item in data.get("assetPositions", []):
            if isinstance(item, dict) and "position" in item:
                position = item["position"]
                coin = position.get("coin", "")
            else:
                position = item
                coin = item.get("coin", item.get("symbol", ""))
            if not position or not coin:
                continue

            size = float(position.get("szi", 0))
            if size == 0:
                continue
            entry = float(position.get("entryPx", 0))
            mark = float(mids.get(coin, entry))
            pnl = (mark - entry) * size
            leverage_info = position.get("leverage", {})
            leverage_value = leverage_info.get("value", "N/A")
            leverage_type = leverage_info.get("type", "unknown")
            positions.append(
                {
                    "symbol": coin,
                    "side": "long" if size > 0 else "short",
                    "size": abs(size),
                    "entry_price": entry,
                    "mark_price": mark,
                    "pnl_usd": round(pnl, 4),
                    "leverage": f"{leverage_value}x ({leverage_type})",
                }
            )
        return {"balance_usd": balance, "open_positions": positions}

    def debug_symbol_limits(self, symbol: str = None):
        print("\n📊 LIMITI TRADING HYPERLIQUID")
        print("-" * 60)
        for perp in self.meta["universe"]:
            if symbol and perp["name"] != symbol:
                continue
            print(f"\nSymbol: {perp['name']}")
            print(f"  Min Size: {perp.get('minSz', 'N/A')}")
            print(f"  Size Decimals: {perp.get('szDecimals', 'N/A')}")
            print(f"  Price Decimals: {perp.get('pxDecimals', 'N/A')}")
            print(f"  Max Leverage: {perp.get('maxLeverage', 'N/A')}")
            print(f"  Only Isolated: {perp.get('onlyIsolated', False)}")

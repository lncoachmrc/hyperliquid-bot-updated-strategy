import json
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, Iterable, Tuple

import eth_account
from eth_account.signers.local import LocalAccount
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from strategy_config import DEFAULT_STRATEGY_CONFIG


SPOT_COLLATERAL_ACCOUNT_MODES = {"unifiedAccount", "portfolioMargin"}
MIN_PERP_ORDER_NOTIONAL = Decimal(
    str(DEFAULT_STRATEGY_CONFIG.minimum_perp_order_notional_usd)
)


def _as_decimal(value: Any, default: str = "0") -> Decimal:
    try:
        if value is None or value == "":
            return Decimal(default)
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal(default)


def _normalize_account_mode(value: Any) -> str:
    """Normalize the userAbstraction response without assuming one SDK version."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("abstraction", "mode", "type"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    return "unknown"


def _extract_spot_usdc_balance(spot_state: Any) -> Tuple[bool, Decimal, Decimal, Decimal]:
    """Return (found, total, hold, available) for USDC in spotClearinghouseState."""
    if not isinstance(spot_state, dict):
        return False, Decimal("0"), Decimal("0"), Decimal("0")

    balances = spot_state.get("balances", [])
    if not isinstance(balances, list):
        return False, Decimal("0"), Decimal("0"), Decimal("0")

    for balance in balances:
        if not isinstance(balance, dict):
            continue
        coin = str(balance.get("coin", "")).upper()
        token = balance.get("token")
        if coin == "USDC" or (not coin and token == 0):
            total = _as_decimal(balance.get("total"))
            hold = _as_decimal(balance.get("hold"))
            available = max(Decimal("0"), total - hold)
            return True, total, hold, available

    return False, Decimal("0"), Decimal("0"), Decimal("0")


class HyperLiquidTrader:
    """Hyperliquid execution adapter with account-mode-aware balance handling."""

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

    @staticmethod
    def _minimum_executable_size(
        symbol_info: Dict[str, Any], mark_price: Decimal
    ) -> Tuple[Decimal, int, Decimal]:
        """Return minimum executable size, decimals and size increment.

        The minimum combines the market's size precision, any explicit minSz
        metadata and Hyperliquid's $10 minimum perp notional.
        """
        if mark_price <= 0:
            raise ValueError("mark price must be positive")
        size_decimals = int(symbol_info.get("szDecimals", 8))
        increment = Decimal(10) ** -size_decimals
        explicit_minimum = _as_decimal(symbol_info.get("minSz"), str(increment))
        if explicit_minimum <= 0:
            explicit_minimum = increment
        notional_minimum_size = (MIN_PERP_ORDER_NOTIONAL / mark_price).quantize(
            increment, rounding=ROUND_UP
        )
        minimum_size = max(increment, explicit_minimum, notional_minimum_size)
        return minimum_size, size_decimals, increment

    def get_execution_constraints(
        self, symbols: Iterable[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Expose real executable minimums before the LLM chooses an order."""
        balance_details = self._get_account_balance_details()
        available_balance = Decimal(str(balance_details["available_balance_usd"]))
        mids = self.info.all_mids()
        constraints: Dict[str, Dict[str, Any]] = {}

        for raw_symbol in symbols:
            symbol = str(raw_symbol).upper()
            symbol_info = next(
                (item for item in self.meta.get("universe", []) if item.get("name") == symbol),
                None,
            )
            mark_raw = mids.get(symbol)
            if not symbol_info or mark_raw is None or available_balance <= 0:
                constraints[symbol] = {
                    "available": False,
                    "available_balance_usd": float(available_balance),
                    "reason": "missing_symbol_metadata_mark_or_balance",
                }
                continue

            mark_price = Decimal(str(mark_raw))
            minimum_size, size_decimals, increment = self._minimum_executable_size(
                symbol_info, mark_price
            )
            minimum_notional = minimum_size * mark_price
            minimum_effective_exposure = minimum_notional / available_balance
            constraints[symbol] = {
                "available": True,
                "available_balance_usd": float(available_balance),
                "mark_price": float(mark_price),
                "size_decimals": size_decimals,
                "size_increment": float(increment),
                "minimum_executable_size": float(minimum_size),
                "minimum_executable_notional_usd": float(minimum_notional),
                "minimum_executable_effective_exposure": float(
                    minimum_effective_exposure
                ),
                "minimum_balance_portion_at_1x": float(minimum_effective_exposure),
                "minimum_balance_portion_at_2x": float(
                    minimum_effective_exposure / Decimal("2")
                ),
            }
        return constraints

    def _get_account_mode(self) -> str:
        """Query Hyperliquid's userAbstraction endpoint with a safe fallback."""
        try:
            response = self.info.post(
                "/info",
                {"type": "userAbstraction", "user": self.account_address},
            )
            return _normalize_account_mode(response)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ Impossibile leggere userAbstraction: {exc}")
            return "unknown"

    def _get_account_balance_details(
        self, perp_state: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        """Resolve equity and available balance according to account abstraction mode.

        Hyperliquid Unified Account and Portfolio Margin expose balances/holds in
        spotClearinghouseState. Standard/legacy modes continue to use the perp
        clearinghouse state's marginSummary.
        """
        data = perp_state if perp_state is not None else self.info.user_state(self.account_address)
        margin_summary = data.get("marginSummary", {}) if isinstance(data, dict) else {}
        perp_account_value = _as_decimal(margin_summary.get("accountValue"))
        perp_withdrawable = _as_decimal(data.get("withdrawable")) if isinstance(data, dict) else Decimal("0")

        account_mode = self._get_account_mode()
        spot_state: Dict[str, Any] = {}
        try:
            raw_spot_state = self.info.spot_user_state(self.account_address)
            if isinstance(raw_spot_state, dict):
                spot_state = raw_spot_state
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ Impossibile leggere spotClearinghouseState: {exc}")

        (
            spot_usdc_found,
            spot_usdc_total,
            spot_usdc_hold,
            spot_usdc_available,
        ) = _extract_spot_usdc_balance(spot_state)

        uses_spot_collateral = account_mode in SPOT_COLLATERAL_ACCOUNT_MODES
        if uses_spot_collateral and spot_usdc_found:
            balance_usd = spot_usdc_total
            available_balance_usd = spot_usdc_available
            balance_source = "spotClearinghouseState.USDC.total"
            available_balance_source = "spotClearinghouseState.USDC.total_minus_hold"
        else:
            balance_usd = perp_account_value
            available_balance_usd = max(Decimal("0"), perp_withdrawable)
            if available_balance_usd == 0 and balance_usd > 0:
                available_balance_usd = balance_usd
            balance_source = "clearinghouseState.marginSummary.accountValue"
            available_balance_source = "clearinghouseState.withdrawable"
            if uses_spot_collateral and not spot_usdc_found:
                balance_source += "_fallback_missing_spot_usdc"
                available_balance_source += "_fallback_missing_spot_usdc"

        return {
            "account_mode": account_mode,
            "balance_source": balance_source,
            "available_balance_source": available_balance_source,
            "balance_usd": float(balance_usd),
            "available_balance_usd": float(available_balance_usd),
            "perp_account_value": float(perp_account_value),
            "perp_withdrawable": float(perp_withdrawable),
            "spot_usdc_total": float(spot_usdc_total),
            "spot_usdc_hold": float(spot_usdc_hold),
            "spot_usdc_available": float(spot_usdc_available),
        }

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

        balance_details = self._get_account_balance_details()
        balance_usd = Decimal(str(balance_details["available_balance_usd"]))
        print(
            "[HyperLiquidTrader] Balance source="
            f"{balance_details['available_balance_source']}, "
            f"available=${balance_usd}, mode={balance_details['account_mode']}"
        )
        if balance_usd <= 0:
            raise RuntimeError("Available balance account = 0")

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

        minimum_size, sz_decimals, quantizer = self._minimum_executable_size(
            symbol_info, mark_px_dec
        )
        size_decimal = raw_size.quantize(quantizer, rounding=ROUND_DOWN)
        minimum_notional = minimum_size * mark_px_dec
        if size_decimal < minimum_size:
            print(
                "[HyperLiquidTrader] OPEN saltato: la size richiesta è inferiore "
                "al minimo realmente eseguibile; non viene aumentata automaticamente."
            )
            return {
                "status": "skipped",
                "reason": "requested_order_below_minimum_executable_size",
                "symbol": symbol,
                "requested_notional_usd": float(notional),
                "requested_size": float(size_decimal),
                "minimum_executable_size": float(minimum_size),
                "minimum_executable_notional_usd": float(minimum_notional),
                "size_decimals": sz_decimals,
            }

        # Leverage is changed only after the order has passed all feasibility
        # checks, so an impossible OPEN does not modify exchange account state.
        leverage_result = self.set_leverage_for_symbol(
            symbol, leverage, is_cross=True
        )
        if leverage_result.get("status") != "ok":
            print(f"⚠️ Warning leva: {leverage_result}")
        time.sleep(0.5)

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
        balance_details = self._get_account_balance_details(perp_state=data)
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

        return {
            **balance_details,
            "open_positions": positions,
        }

    def debug_symbol_limits(self, symbol: str = None):
        print("\n📊 LIMITI TRADING HYPERLIQUID")
        print("-" * 60)
        for perp in self.meta["universe"]:
            if symbol and perp["name"] != symbol:
                continue
            print(f"\nSymbol: {perp['name']}")
            print(f"  Min Size: {perp.get('minSz', 'N/A')}")
            print(f"  Size Decimals: {perp.get('szDecimals', 'N/A')}")

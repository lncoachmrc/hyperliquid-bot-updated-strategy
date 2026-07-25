"""Resolve Hyperliquid account equity without assuming one account mode.

The module is deliberately pure: network calls live in the read-only client,
while this file turns already-fetched public account states into one auditable
resolution.  Perp and spot balances are never added together because Unified
and Portfolio Margin can expose the same collateral through different views.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


SPOT_COLLATERAL_ACCOUNT_MODES = frozenset(
    {"unifiedaccount", "portfoliomargin"}
)


@dataclass(frozen=True)
class EquityResolution:
    equity_usd: float
    available_usd: float
    source: str
    account_mode: str
    perp_equity_usd: float
    spot_usdc_total: float
    spot_usdc_available: float
    warnings: tuple[str, ...] = ()

    @property
    def is_degenerate(self) -> bool:
        return self.equity_usd <= 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_account_mode(response: Any) -> str:
    """Normalize the public ``userAbstraction`` response."""
    if isinstance(response, str):
        return response.strip() or "unknown"
    if isinstance(response, Mapping):
        for key in ("accountAbstraction", "abstraction", "mode", "type"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "unknown"


def extract_spot_usdc(
    spot_state: Any,
) -> tuple[bool, Decimal, Decimal, Decimal]:
    """Return ``found, total, hold, available`` for spot USDC."""
    if not isinstance(spot_state, Mapping):
        return False, Decimal("0"), Decimal("0"), Decimal("0")
    balances = spot_state.get("balances")
    if not isinstance(balances, list):
        return False, Decimal("0"), Decimal("0"), Decimal("0")
    for balance in balances:
        if not isinstance(balance, Mapping):
            continue
        coin = str(balance.get("coin") or "").upper()
        token = balance.get("token")
        if coin != "USDC" and not (not coin and str(token) == "0"):
            continue
        total = _decimal(balance.get("total"))
        hold = _decimal(balance.get("hold"))
        return True, total, hold, max(Decimal("0"), total - hold)
    return False, Decimal("0"), Decimal("0"), Decimal("0")


def resolve_equity(
    perp_state: Mapping[str, Any] | None,
    spot_state: Mapping[str, Any] | None,
    account_mode: str,
) -> EquityResolution:
    """Choose one collateral view and expose the choice in telemetry."""
    perp_state = perp_state or {}
    margin = (
        perp_state.get("marginSummary")
        or perp_state.get("crossMarginSummary")
        or {}
    )
    margin = margin if isinstance(margin, Mapping) else {}
    perp_equity = _decimal(margin.get("accountValue"))
    perp_available = _decimal(perp_state.get("withdrawable"))
    found_spot, spot_total, _spot_hold, spot_available = extract_spot_usdc(
        spot_state
    )
    normalized_mode = str(account_mode or "unknown").strip() or "unknown"
    compact_mode = normalized_mode.lower().replace("_", "").replace("-", "")
    warnings: list[str] = []

    if compact_mode in SPOT_COLLATERAL_ACCOUNT_MODES:
        if found_spot:
            return _resolution(
                spot_total,
                spot_available,
                "spotClearinghouseState.USDC.total",
                normalized_mode,
                perp_equity,
                spot_total,
                spot_available,
                warnings,
            )
        warnings.append("spot_usdc_missing_for_spot_collateral_mode")

    # Defensive fallback for the audited failure mode: a transient
    # userAbstraction error must not turn a funded Unified account into $0 risk.
    if (
        compact_mode == "unknown"
        and perp_equity <= 0
        and found_spot
        and spot_total > 0
    ):
        warnings.append("spot_fallback_used_because_perp_equity_is_zero")
        return _resolution(
            spot_total,
            spot_available,
            "spotClearinghouseState.USDC.total(fallback_perp_zero)",
            normalized_mode,
            perp_equity,
            spot_total,
            spot_available,
            warnings,
        )

    if perp_equity <= 0:
        warnings.append("resolved_equity_is_zero")
    return _resolution(
        perp_equity,
        perp_available,
        "marginSummary.accountValue",
        normalized_mode,
        perp_equity,
        spot_total,
        spot_available,
        warnings,
    )


def resolution_from_account_state(
    account_state: Mapping[str, Any] | None,
) -> EquityResolution:
    """Read an attached resolution, or safely resolve a plain perp state."""
    state = account_state or {}
    attached = state.get("_equity_resolution")
    if isinstance(attached, Mapping):
        try:
            return EquityResolution(
                equity_usd=float(attached.get("equity_usd") or 0),
                available_usd=float(attached.get("available_usd") or 0),
                source=str(attached.get("source") or "unknown"),
                account_mode=str(attached.get("account_mode") or "unknown"),
                perp_equity_usd=float(
                    attached.get("perp_equity_usd") or 0
                ),
                spot_usdc_total=float(attached.get("spot_usdc_total") or 0),
                spot_usdc_available=float(
                    attached.get("spot_usdc_available") or 0
                ),
                warnings=tuple(
                    str(item) for item in attached.get("warnings") or ()
                ),
            )
        except (TypeError, ValueError):
            pass
    spot = state.get("_spot_clearinghouse_state")
    mode = str(state.get("_account_mode") or "unknown")
    return resolve_equity(
        state,
        spot if isinstance(spot, Mapping) else {},
        mode,
    )


def enrich_account_state(
    perp_state: Mapping[str, Any] | None,
    spot_state: Mapping[str, Any] | None,
    account_mode: str,
) -> dict[str, Any]:
    """Attach public collateral evidence without changing exchange fields."""
    result = dict(perp_state or {})
    spot = dict(spot_state or {})
    resolution = resolve_equity(result, spot, account_mode)
    result["_account_mode"] = account_mode
    result["_spot_clearinghouse_state"] = spot
    result["_equity_resolution"] = resolution.to_dict()
    return result


def _resolution(
    equity: Decimal,
    available: Decimal,
    source: str,
    account_mode: str,
    perp_equity: Decimal,
    spot_total: Decimal,
    spot_available: Decimal,
    warnings: list[str],
) -> EquityResolution:
    return EquityResolution(
        equity_usd=float(equity),
        available_usd=float(max(Decimal("0"), available)),
        source=source,
        account_mode=account_mode,
        perp_equity_usd=float(perp_equity),
        spot_usdc_total=float(spot_total),
        spot_usdc_available=float(spot_available),
        warnings=tuple(warnings),
    )


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")

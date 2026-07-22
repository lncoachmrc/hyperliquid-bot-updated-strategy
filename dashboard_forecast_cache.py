from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import db_utils


_SYMBOL_ORDER = {"BTC": 0, "ETH": 1, "SOL": 2}


def _symbol(item: Dict[str, Any]) -> str:
    return str(item.get("Ticker") or item.get("ticker") or "").upper().strip()


def _timeframe(item: Dict[str, Any]) -> str:
    return str(item.get("Timeframe") or item.get("timeframe") or "").strip()


def _horizon_rank(timeframe: str) -> int:
    value = timeframe.lower()
    if "15" in value:
        return 0
    if "ora" in value or "hour" in value or value.replace(" ", "") == "1h":
        return 1
    return 2


def _forecast_key(item: Dict[str, Any]) -> Optional[Tuple[str, int]]:
    symbol = _symbol(item)
    timeframe = _timeframe(item)
    if not symbol or not timeframe:
        return None
    return symbol, _horizon_rank(timeframe)


def _normalise_items(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict) and "raw" not in raw:
        return [dict(raw)]
    return []


def load_latest_forecasts(
    symbols: Sequence[str] = ("BTC", "ETH", "SOL"),
) -> List[Dict[str, Any]]:
    """Load the latest stored 15m/1h forecast for each requested symbol.

    This query is dashboard-only. A database failure is deliberately fail-open:
    it returns an empty list and must never interrupt trading or position
    management.
    """

    wanted = {str(symbol).upper() for symbol in symbols}
    try:
        with db_utils.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        f.ticker,
                        f.timeframe,
                        f.last_price,
                        f.prediction,
                        f.lower_bound,
                        f.upper_bound,
                        f.change_pct,
                        f.forecast_timestamp,
                        f.raw,
                        a.created_at
                    FROM forecasts_contexts f
                    JOIN ai_contexts a ON a.id = f.context_id
                    WHERE UPPER(f.ticker) IN ('BTC', 'ETH', 'SOL')
                      AND f.prediction IS NOT NULL
                    ORDER BY a.created_at DESC, f.id DESC
                    LIMIT 100;
                    """
                )
                rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        print(f"[dashboard_forecasts] cache unavailable: {exc}")
        return []

    latest: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for row in rows:
        symbol = str(row[0] or "").upper()
        timeframe = str(row[1] or "")
        if symbol not in wanted:
            continue
        key = (symbol, _horizon_rank(timeframe))
        if key[1] not in (0, 1) or key in latest:
            continue
        raw_payload = dict(row[8]) if isinstance(row[8], dict) else {}
        raw_payload["dashboard_cached"] = True
        raw_payload["dashboard_cached_created_at"] = (
            row[9].isoformat() if row[9] is not None else None
        )
        latest[key] = {
            "Ticker": symbol,
            "Timeframe": timeframe,
            "Ultimo Prezzo": row[2],
            "Previsione": row[3],
            "Limite Inferiore": row[4],
            "Limite Superiore": row[5],
            "Variazione %": row[6],
            "Timestamp Previsione": row[7],
            "raw": raw_payload,
        }

    return sorted(
        latest.values(),
        key=lambda item: (
            _horizon_rank(_timeframe(item)),
            _SYMBOL_ORDER.get(_symbol(item), 99),
        ),
    )


def merge_dashboard_forecasts(
    current: Any,
    cached: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge current Prophet output with cached rows without filtering cards.

    Current forecasts override cached values for the same symbol/horizon. Any
    unknown but valid forecast remains visible after the six standard cards.
    """

    merged: Dict[Tuple[str, int], Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []

    for item in _normalise_items(list(cached)):
        key = _forecast_key(item)
        if key is None or key[1] not in (0, 1):
            extras.append(item)
        else:
            merged[key] = item

    for item in _normalise_items(current):
        key = _forecast_key(item)
        if key is None or key[1] not in (0, 1):
            extras.append(item)
        else:
            merged[key] = item

    standard = sorted(
        merged.values(),
        key=lambda item: (
            _horizon_rank(_timeframe(item)),
            _SYMBOL_ORDER.get(_symbol(item), 99),
        ),
    )
    return standard + extras


def resolve_dashboard_forecasts(current: Any) -> Tuple[List[Dict[str, Any]], str]:
    current_items = _normalise_items(current)
    cached_items = load_latest_forecasts()
    merged = merge_dashboard_forecasts(current_items, cached_items)

    if current_items and cached_items:
        source = "current_plus_cached"
    elif current_items:
        source = "current"
    elif cached_items:
        source = "cached"
    else:
        source = "unavailable"
    return merged, source

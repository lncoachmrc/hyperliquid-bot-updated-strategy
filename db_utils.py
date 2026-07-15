from __future__ import annotations

import json
import os
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json

from strategy_core import drawdown_factor

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

load_dotenv()


@dataclass
class DBConfig:
    dsn: str


def get_db_config() -> DBConfig:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL non impostata. Imposta la variabile d'ambiente, "
            "ad esempio: postgresql://user:password@localhost:5432/trading_db"
        )
    return DBConfig(dsn=dsn)


@contextmanager
def get_connection():
    connection = psycopg2.connect(get_db_config().dsn)
    try:
        yield connection
    finally:
        connection.close()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS account_snapshots (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    balance_usd NUMERIC(20, 8) NOT NULL,
    raw_payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS open_positions (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES account_snapshots(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    size NUMERIC(30, 10) NOT NULL,
    entry_price NUMERIC(30, 10),
    mark_price NUMERIC(30, 10),
    pnl_usd NUMERIC(30, 10),
    leverage TEXT,
    raw_payload JSONB NOT NULL,
    stop_loss_percent NUMERIC(10, 4)
);
CREATE INDEX IF NOT EXISTS idx_open_positions_snapshot_id
    ON open_positions(snapshot_id);

CREATE TABLE IF NOT EXISTS ai_contexts (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    system_prompt TEXT
);

CREATE TABLE IF NOT EXISTS indicators_contexts (
    id BIGSERIAL PRIMARY KEY,
    context_id BIGINT NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    ts TIMESTAMPTZ,
    price NUMERIC(20, 8),
    ema20 NUMERIC(20, 8),
    macd NUMERIC(20, 8),
    rsi_7 NUMERIC(20, 8),
    volume_bid NUMERIC(30, 10),
    volume_ask NUMERIC(30, 10),
    pp NUMERIC(20, 8),
    s1 NUMERIC(20, 8),
    s2 NUMERIC(20, 8),
    r1 NUMERIC(20, 8),
    r2 NUMERIC(20, 8),
    open_interest_latest NUMERIC(30, 10),
    open_interest_average NUMERIC(30, 10),
    funding_rate NUMERIC(20, 10),
    ema20_15m NUMERIC(20, 8),
    ema50_15m NUMERIC(20, 8),
    atr3_15m NUMERIC(20, 8),
    atr14_15m NUMERIC(20, 8),
    volume_15m_current NUMERIC(30, 10),
    volume_15m_average NUMERIC(30, 10),
    intraday_mid_prices JSONB,
    intraday_ema20_series JSONB,
    intraday_macd_series JSONB,
    intraday_rsi7_series JSONB,
    intraday_rsi14_series JSONB,
    lt15m_macd_series JSONB,
    lt15m_rsi14_series JSONB,
    strategy JSONB
);

CREATE TABLE IF NOT EXISTS news_contexts (
    id BIGSERIAL PRIMARY KEY,
    context_id BIGINT NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    news_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment_contexts (
    id BIGSERIAL PRIMARY KEY,
    context_id BIGINT NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    value INTEGER,
    classification TEXT,
    sentiment_timestamp BIGINT,
    raw JSONB
);

CREATE TABLE IF NOT EXISTS forecasts_contexts (
    id BIGSERIAL PRIMARY KEY,
    context_id BIGINT NOT NULL REFERENCES ai_contexts(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    last_price NUMERIC(30, 10),
    prediction NUMERIC(30, 10),
    lower_bound NUMERIC(30, 10),
    upper_bound NUMERIC(30, 10),
    change_pct NUMERIC(10, 4),
    forecast_timestamp BIGINT,
    raw JSONB
);

CREATE TABLE IF NOT EXISTS bot_operations (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    context_id BIGINT REFERENCES ai_contexts(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    symbol TEXT,
    direction TEXT,
    target_portion_of_balance NUMERIC(10, 4),
    leverage NUMERIC(10, 4),
    stop_loss_percent NUMERIC(10, 4),
    raw_payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bot_operations_created_at
    ON bot_operations(created_at);

CREATE TABLE IF NOT EXISTS errors (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error_type TEXT NOT NULL,
    error_message TEXT,
    traceback TEXT,
    context JSONB,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_errors_created_at ON errors(created_at);
"""

MIGRATION_SQL = """
ALTER TABLE bot_operations ADD COLUMN IF NOT EXISTS context_id BIGINT;
ALTER TABLE bot_operations ADD COLUMN IF NOT EXISTS stop_loss_percent NUMERIC(10, 4);
ALTER TABLE indicators_contexts ADD COLUMN IF NOT EXISTS strategy JSONB;
"""


def init_db() -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
            cursor.execute(MIGRATION_SQL)
        connection.commit()


def _normalize_json_arg(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {"raw": value}
    return value


def _to_plain_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if np is not None:
        try:
            if isinstance(value, np.generic):
                return float(value)
        except Exception:
            pass
    try:
        numeric = float(value)
        return numeric
    except (TypeError, ValueError):
        return None


def _normalize_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_for_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_for_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    numeric = _to_plain_number(value)
    if numeric is not None:
        return numeric
    return value


def log_error(
    exc: BaseException,
    *,
    context: Optional[Dict[str, Any]] = None,
    source: Optional[str] = None,
) -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO errors
                    (error_type, error_message, traceback, context, source)
                VALUES (%s, %s, %s, %s, %s);
                """,
                (
                    type(exc).__name__,
                    str(exc),
                    traceback.format_exc(),
                    Json(_normalize_for_json(context)) if context is not None else None,
                    source,
                ),
            )
        connection.commit()


def log_account_status(account_status: Dict[str, Any]) -> int:
    balance = account_status.get("balance_usd")
    if balance is None:
        raise ValueError("account_status deve contenere 'balance_usd'")
    positions = account_status.get("open_positions") or []

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO account_snapshots (balance_usd, raw_payload)
                VALUES (%s, %s) RETURNING id;
                """,
                (balance, Json(_normalize_for_json(account_status))),
            )
            snapshot_id = cursor.fetchone()[0]
            for position in positions:
                cursor.execute(
                    """
                    INSERT INTO open_positions (
                        snapshot_id, symbol, side, size, entry_price, mark_price,
                        pnl_usd, leverage, raw_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        snapshot_id,
                        position.get("symbol"),
                        position.get("side"),
                        position.get("size"),
                        position.get("entry_price"),
                        position.get("mark_price"),
                        position.get("pnl_usd"),
                        position.get("leverage"),
                        Json(_normalize_for_json(position)),
                    ),
                )
        connection.commit()
    return snapshot_id


def get_account_drawdown_state(
    current_balance: float,
    soft: float = 0.05,
    hard: float = 0.15,
) -> Dict[str, Any]:
    """Reuse account_snapshots to calculate the strategy drawdown factor.

    A database failure does not fabricate a value.  The returned availability
    flag tells the LLM that the factor could not be verified.
    """
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT MAX(balance_usd) FROM account_snapshots;")
                row = cursor.fetchone()
        peak = float(row[0]) if row and row[0] is not None else float(current_balance)
        drawdown = float(current_balance) / peak - 1.0 if peak > 0 else 0.0
        return {
            "available": True,
            "current_balance": float(current_balance),
            "historical_peak_balance": peak,
            "drawdown": drawdown,
            "drawdown_factor": drawdown_factor(drawdown, soft, hard),
            "soft_threshold": -soft,
            "hard_threshold": -hard,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "drawdown_factor": None,
            "error": str(exc),
            "instruction": "Do not invent a drawdown factor.",
        }


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _extract_volume(volume: Any) -> tuple[Optional[float], Optional[float]]:
    if not isinstance(volume, str) or "Bid Vol" not in volume:
        return None, None
    try:
        cleaned = volume.replace("Bid Vol:", "")
        bid_text, ask_text = cleaned.split("Ask Vol:", 1)
        return float(bid_text.strip().strip(",")), float(ask_text.strip())
    except Exception:
        return None, None


def log_bot_operation(
    operation_payload: Dict[str, Any],
    *,
    system_prompt: Optional[str] = None,
    indicators: Optional[Any] = None,
    news_text: Optional[str] = None,
    sentiment: Optional[Any] = None,
    forecasts: Optional[Any] = None,
) -> int:
    operation = operation_payload.get("operation")
    if operation is None:
        raise ValueError("operation_payload deve contenere 'operation'")

    sentiment_norm = _normalize_json_arg(sentiment) if sentiment is not None else None
    forecasts_norm = _normalize_json_arg(forecasts) if forecasts is not None else None

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO ai_contexts (system_prompt) VALUES (%s) RETURNING id;",
                (system_prompt,),
            )
            context_id = cursor.fetchone()[0]

            if indicators is not None:
                normalized = _normalize_json_arg(indicators)
                if isinstance(normalized, dict):
                    indicator_items = [normalized]
                elif isinstance(normalized, list):
                    indicator_items = [item for item in normalized if isinstance(item, dict)]
                else:
                    indicator_items = []

                for item in indicator_items:
                    ticker = item.get("ticker")
                    if not ticker:
                        continue
                    current = item.get("current") or {}
                    pivot = item.get("pivot_points") or {}
                    derivatives = item.get("derivatives") or {}
                    intraday = item.get("intraday") or {}
                    longer = item.get("longer_term_15m") or {}
                    bid, ask = _extract_volume(item.get("volume"))
                    cursor.execute(
                        """
                        INSERT INTO indicators_contexts (
                            context_id, ticker, ts, price, ema20, macd, rsi_7,
                            volume_bid, volume_ask, pp, s1, s2, r1, r2,
                            open_interest_latest, open_interest_average, funding_rate,
                            ema20_15m, ema50_15m, atr3_15m, atr14_15m,
                            volume_15m_current, volume_15m_average,
                            intraday_mid_prices, intraday_ema20_series,
                            intraday_macd_series, intraday_rsi7_series,
                            intraday_rsi14_series, lt15m_macd_series,
                            lt15m_rsi14_series, strategy
                        ) VALUES (
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                        );
                        """,
                        (
                            context_id,
                            ticker,
                            _parse_timestamp(item.get("timestamp")),
                            _to_plain_number(current.get("price")),
                            _to_plain_number(current.get("ema20")),
                            _to_plain_number(current.get("macd")),
                            _to_plain_number(current.get("rsi_7")),
                            _to_plain_number(bid),
                            _to_plain_number(ask),
                            _to_plain_number(pivot.get("pp")),
                            _to_plain_number(pivot.get("s1")),
                            _to_plain_number(pivot.get("s2")),
                            _to_plain_number(pivot.get("r1")),
                            _to_plain_number(pivot.get("r2")),
                            _to_plain_number(derivatives.get("open_interest_latest")),
                            _to_plain_number(derivatives.get("open_interest_average")),
                            _to_plain_number(derivatives.get("funding_rate")),
                            _to_plain_number(longer.get("ema_20_current")),
                            _to_plain_number(longer.get("ema_50_current")),
                            _to_plain_number(longer.get("atr_3_current")),
                            _to_plain_number(longer.get("atr_14_current")),
                            _to_plain_number(longer.get("volume_current")),
                            _to_plain_number(longer.get("volume_average")),
                            Json(_normalize_for_json(intraday.get("mid_prices"))),
                            Json(_normalize_for_json(intraday.get("ema_20"))),
                            Json(_normalize_for_json(intraday.get("macd"))),
                            Json(_normalize_for_json(intraday.get("rsi_7"))),
                            Json(_normalize_for_json(intraday.get("rsi_14"))),
                            Json(_normalize_for_json(longer.get("macd_series"))),
                            Json(_normalize_for_json(longer.get("rsi_14_series"))),
                            Json(_normalize_for_json(item.get("strategy"))),
                        ),
                    )

            if news_text:
                cursor.execute(
                    "INSERT INTO news_contexts (context_id, news_text) VALUES (%s, %s);",
                    (context_id, news_text),
                )

            if isinstance(sentiment_norm, dict):
                timestamp = sentiment_norm.get("timestamp")
                try:
                    timestamp = int(timestamp) if timestamp is not None else None
                except Exception:
                    timestamp = None
                cursor.execute(
                    """
                    INSERT INTO sentiment_contexts
                        (context_id, value, classification, sentiment_timestamp, raw)
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (
                        context_id,
                        sentiment_norm.get("valore"),
                        sentiment_norm.get("classificazione"),
                        timestamp,
                        Json(_normalize_for_json(sentiment_norm)),
                    ),
                )

            if isinstance(forecasts_norm, dict) and "raw" in forecasts_norm:
                forecast_items: List[Dict[str, Any]] = []
            elif isinstance(forecasts_norm, list):
                forecast_items = [item for item in forecasts_norm if isinstance(item, dict)]
            elif isinstance(forecasts_norm, dict):
                forecast_items = [forecasts_norm]
            else:
                forecast_items = []

            for forecast in forecast_items:
                ticker = forecast.get("Ticker") or forecast.get("ticker")
                timeframe = forecast.get("Timeframe") or forecast.get("timeframe")
                if not ticker or not timeframe:
                    continue
                timestamp = forecast.get("Timestamp Previsione") or forecast.get(
                    "forecast_timestamp"
                )
                try:
                    timestamp = int(timestamp) if timestamp is not None else None
                except Exception:
                    timestamp = None
                cursor.execute(
                    """
                    INSERT INTO forecasts_contexts (
                        context_id, ticker, timeframe, last_price, prediction,
                        lower_bound, upper_bound, change_pct, forecast_timestamp, raw
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
                    """,
                    (
                        context_id,
                        ticker,
                        timeframe,
                        _to_plain_number(
                            forecast.get("Ultimo Prezzo") or forecast.get("last_price")
                        ),
                        _to_plain_number(
                            forecast.get("Previsione") or forecast.get("prediction")
                        ),
                        _to_plain_number(
                            forecast.get("Limite Inferiore")
                            or forecast.get("lower_bound")
                        ),
                        _to_plain_number(
                            forecast.get("Limite Superiore")
                            or forecast.get("upper_bound")
                        ),
                        _to_plain_number(
                            forecast.get("Variazione %") or forecast.get("change_pct")
                        ),
                        timestamp,
                        Json(_normalize_for_json(forecast)),
                    ),
                )

            cursor.execute(
                """
                INSERT INTO bot_operations (
                    context_id, operation, symbol, direction,
                    target_portion_of_balance, leverage, stop_loss_percent,
                    raw_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id;
                """,
                (
                    context_id,
                    operation,
                    operation_payload.get("symbol"),
                    operation_payload.get("direction"),
                    operation_payload.get("target_portion_of_balance"),
                    operation_payload.get("leverage"),
                    operation_payload.get("stop_loss_percent"),
                    Json(_normalize_for_json(operation_payload)),
                ),
            )
            operation_id = cursor.fetchone()[0]
        connection.commit()
    return operation_id


def get_latest_account_snapshot() -> Optional[Dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT raw_payload FROM account_snapshots
                ORDER BY created_at DESC LIMIT 1;
                """
            )
            row = cursor.fetchone()
    return row[0] if row else None


def get_recent_bot_operations(limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT raw_payload FROM bot_operations
                ORDER BY created_at DESC LIMIT %s;
                """,
                (limit,),
            )
            rows = cursor.fetchall()
    return [row[0] for row in rows]


if __name__ == "__main__":
    init_db()

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from psycopg2.extras import Json

import db_utils
from news_feed import canonicalize_url, fetch_news_items
from sentiment import get_latest_fear_and_greed


TARGET_SYMBOLS: Tuple[str, ...] = ("BTC", "ETH", "SOL")
NEWS_HORIZONS: Tuple[int, ...] = (15, 60)
SENTIMENT_HORIZONS: Tuple[int, ...] = (360, 1440)

ASSET_ALIASES: Dict[str, Tuple[str, ...]] = {
    "BTC": ("btc", "bitcoin"),
    "ETH": ("eth", "ethereum", "ether"),
    "SOL": ("sol", "solana"),
}

MARKET_WIDE_TERMS = {
    "crypto market",
    "cryptocurrency market",
    "digital assets",
    "risk assets",
    "federal reserve",
    "fed",
    "cpi",
    "inflation",
    "interest rates",
    "rate cut",
    "rate hike",
    "sec",
    "liquidation",
    "liquidations",
    "geopolitical",
    "tariff",
}

CATEGORY_TERMS: Tuple[Tuple[str, Set[str]], ...] = (
    ("etf", {"etf", "inflow", "outflow", "fund flow", "blackrock", "fidelity"}),
    ("regulation", {"sec", "regulation", "regulator", "mica", "micar", "lawsuit", "approval"}),
    ("macro", {"fed", "federal reserve", "cpi", "inflation", "rates", "rate cut", "rate hike", "tariff"}),
    ("security", {"hack", "exploit", "breach", "stolen", "attack", "vulnerability"}),
    ("exchange", {"exchange", "binance", "coinbase", "kraken", "hyperliquid", "listing", "delisting"}),
    ("whale", {"whale", "wallet", "accumulation", "distribution", "transfer"}),
    ("network", {"upgrade", "fork", "outage", "network", "validator", "staking"}),
    ("liquidation", {"liquidation", "liquidations", "short squeeze", "long squeeze"}),
)

POSITIVE_TERMS = {
    "approval",
    "approved",
    "adoption",
    "accumulation",
    "accumulates",
    "buying",
    "bullish",
    "breakout",
    "inflow",
    "inflows",
    "launch",
    "record demand",
    "rate cut",
    "recovery",
    "rally",
    "surge",
    "upgrade",
}

NEGATIVE_TERMS = {
    "ban",
    "bearish",
    "breach",
    "crash",
    "decline",
    "delisting",
    "dump",
    "exploit",
    "hack",
    "lawsuit",
    "liquidation",
    "liquidations",
    "outflow",
    "outflows",
    "rate hike",
    "rejection",
    "sell-off",
    "selling",
    "shutdown",
}

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their", "this",
    "to", "was", "were", "will", "with", "after", "amid", "over", "under", "new", "says",
    "could", "may", "more", "than", "why", "how", "what", "when", "where", "crypto",
    "cryptocurrency", "market", "markets", "price", "prices", "token", "tokens",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS news_sentiment_shadow_events (
    id BIGSERIAL PRIMARY KEY,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('news', 'sentiment')),
    provider TEXT NOT NULL,
    source_identifier TEXT,
    canonical_url TEXT,
    title TEXT,
    summary TEXT,
    published_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    exact_duplicate_count INTEGER NOT NULL DEFAULT 0,
    semantic_duplicate_count INTEGER NOT NULL DEFAULT 0,
    semantic_cluster_key TEXT,
    event_category TEXT,
    assets JSONB NOT NULL DEFAULT '[]'::jsonb,
    relevance_score NUMERIC(8, 6),
    direction_score NUMERIC(8, 6),
    confidence NUMERIC(8, 6),
    sentiment_value NUMERIC(10, 4),
    sentiment_classification TEXT,
    raw_payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ns_shadow_events_kind_time
    ON news_sentiment_shadow_events(event_kind, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_ns_shadow_events_semantic
    ON news_sentiment_shadow_events(semantic_cluster_key);

CREATE TABLE IF NOT EXISTS news_sentiment_shadow_aliases (
    exact_hash TEXT PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES news_sentiment_shadow_events(id) ON DELETE CASCADE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    seen_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_ns_shadow_alias_event
    ON news_sentiment_shadow_aliases(event_id);

CREATE TABLE IF NOT EXISTS news_sentiment_shadow_observations (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES news_sentiment_shadow_events(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    horizon_minutes INTEGER NOT NULL,
    baseline_at TIMESTAMPTZ NOT NULL,
    baseline_price NUMERIC(30, 10) NOT NULL,
    target_at TIMESTAMPTZ NOT NULL,
    expected_direction SMALLINT NOT NULL DEFAULT 0,
    realized_at TIMESTAMPTZ,
    realized_price NUMERIC(30, 10),
    realized_return_pct NUMERIC(16, 8),
    mfe_pct NUMERIC(16, 8),
    mae_pct NUMERIC(16, 8),
    direction_correct BOOLEAN,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'complete', 'missing')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(event_id, symbol, horizon_minutes)
);
CREATE INDEX IF NOT EXISTS idx_ns_shadow_obs_pending
    ON news_sentiment_shadow_observations(status, target_at);
"""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def ensure_news_sentiment_shadow_schema() -> None:
    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA_SQL)
        connection.commit()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            numeric = float(value)
            if numeric > 10_000_000_000:
                numeric /= 1000.0
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _normalise_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9%$]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> Set[str]:
    return {
        token
        for token in _normalise_text(value).split()
        if len(token) >= 3 and token not in STOP_WORDS
    }


def _jaccard(left: Set[str], right: Set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _contains_term(text: str, term: str) -> bool:
    if " " in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def _extract_assets(text: str) -> Tuple[List[str], bool]:
    assets: List[str] = []
    for symbol, aliases in ASSET_ALIASES.items():
        if any(_contains_term(text, alias) for alias in aliases):
            assets.append(symbol)
    market_wide = any(_contains_term(text, term) for term in MARKET_WIDE_TERMS)
    if not assets and market_wide:
        assets = list(TARGET_SYMBOLS)
    return assets, market_wide


def _category(text: str) -> str:
    for category, terms in CATEGORY_TERMS:
        if any(_contains_term(text, term) for term in terms):
            return category
    return "other"


def _direction_score(text: str) -> float:
    positive = sum(1 for term in POSITIVE_TERMS if _contains_term(text, term))
    negative = sum(1 for term in NEGATIVE_TERMS if _contains_term(text, term))
    total = positive + negative
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (positive - negative) / total))


def classify_news_item(item: Dict[str, Any]) -> Dict[str, Any]:
    title = str(item.get("title") or "")
    summary = str(item.get("summary") or "")
    text = _normalise_text(f"{title} {summary}")
    assets, market_wide = _extract_assets(text)
    category = _category(text)
    direction = _direction_score(text)

    direct_mentions = sum(
        1
        for symbol in assets
        if any(_contains_term(text, alias) for alias in ASSET_ALIASES[symbol])
    )
    if direct_mentions:
        relevance = min(1.0, 0.72 + 0.08 * direct_mentions)
    elif market_wide:
        relevance = 0.68
    else:
        relevance = 0.0
    if category in {"macro", "regulation", "security", "liquidation", "etf"} and assets:
        relevance = min(1.0, relevance + 0.08)

    confidence = min(0.95, 0.38 + 0.28 * abs(direction) + 0.24 * relevance)
    title_tokens = sorted(_tokens(title))
    semantic_material = "|".join(
        [category, ",".join(sorted(assets)), " ".join(title_tokens[:14])]
    )
    semantic_key = hashlib.sha256(semantic_material.encode("utf-8")).hexdigest()
    return {
        "assets": assets,
        "event_category": category,
        "direction_score": round(direction, 6),
        "relevance_score": round(relevance, 6),
        "confidence": round(confidence, 6),
        "semantic_cluster_key": semantic_key,
        "title_tokens": title_tokens,
    }


def exact_news_hash(item: Dict[str, Any]) -> str:
    provider = str(item.get("source") or "unknown").lower().strip()
    identity = canonicalize_url(str(item.get("url") or ""))
    if not identity:
        identity = str(item.get("guid") or "").strip()
    if not identity:
        identity = "|".join(
            [
                _normalise_text(item.get("title")),
                str(item.get("published_at") or ""),
            ]
        )
    return hashlib.sha256(f"news|{provider}|{identity}".encode("utf-8")).hexdigest()


def exact_sentiment_hash(provider: str, payload: Dict[str, Any]) -> str:
    material = "|".join(
        [
            "sentiment",
            provider.lower().strip(),
            str(payload.get("timestamp") or ""),
            str(payload.get("valore") or payload.get("value") or ""),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def sentiment_direction(value: Optional[float]) -> float:
    """Contrarian hypothesis used only for shadow evaluation."""

    if value is None:
        return 0.0
    if value <= 25:
        return 1.0
    if value <= 40:
        return 0.5
    if value >= 75:
        return -1.0
    if value >= 60:
        return -0.5
    return 0.0


def _expected_direction(score: float) -> int:
    if score > 0.15:
        return 1
    if score < -0.15:
        return -1
    return 0


def _extract_prices(indicators: Any) -> Tuple[Dict[str, float], datetime]:
    prices: Dict[str, float] = {}
    timestamps: List[datetime] = []
    items = indicators if isinstance(indicators, list) else [indicators]
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("ticker") or item.get("symbol") or "").upper()
        if symbol not in TARGET_SYMBOLS:
            continue
        current = item.get("current") if isinstance(item.get("current"), dict) else {}
        value = current.get("price") if current else item.get("price")
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        prices[symbol] = price
        timestamp = _parse_datetime(item.get("timestamp") or item.get("ts"))
        if timestamp is not None:
            timestamps.append(timestamp)
    baseline_at = max(timestamps) if timestamps else _utc_now()
    return prices, baseline_at


def _alias_event_id(cursor: Any, exact_hash: str) -> Optional[int]:
    cursor.execute(
        "SELECT event_id FROM news_sentiment_shadow_aliases WHERE exact_hash = %s;",
        (exact_hash,),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None


def _touch_exact_duplicate(cursor: Any, exact_hash: str, event_id: int) -> None:
    cursor.execute(
        """
        UPDATE news_sentiment_shadow_aliases
        SET last_seen_at = NOW(), seen_count = seen_count + 1
        WHERE exact_hash = %s;
        """,
        (exact_hash,),
    )
    cursor.execute(
        """
        UPDATE news_sentiment_shadow_events
        SET last_seen_at = NOW(), exact_duplicate_count = exact_duplicate_count + 1
        WHERE id = %s;
        """,
        (event_id,),
    )


def _find_semantic_duplicate(
    cursor: Any,
    *,
    classification: Dict[str, Any],
    title: str,
    published_at: Optional[datetime],
) -> Optional[int]:
    cluster_hours = max(1, _env_int("NEWS_EVENT_CLUSTER_HOURS", 12))
    threshold = _env_float("NEWS_SEMANTIC_SIMILARITY", 0.56)
    since = (published_at or _utc_now()) - timedelta(hours=cluster_hours)
    cursor.execute(
        """
        SELECT id, title, event_category, assets
        FROM news_sentiment_shadow_events
        WHERE event_kind = 'news'
          AND first_seen_at >= %s
        ORDER BY first_seen_at DESC
        LIMIT 150;
        """,
        (since,),
    )
    incoming_assets = set(classification.get("assets") or [])
    incoming_tokens = _tokens(title)
    incoming_category = classification.get("event_category")
    best_id: Optional[int] = None
    best_score = 0.0
    for row in cursor.fetchall():
        existing_assets = set(row[3] or [])
        if incoming_assets and existing_assets and not (incoming_assets & existing_assets):
            continue
        if incoming_category != row[2] and incoming_category != "other" and row[2] != "other":
            continue
        score = _jaccard(incoming_tokens, _tokens(row[1]))
        if score >= threshold and score > best_score:
            best_id = int(row[0])
            best_score = score
    return best_id


def _insert_alias(cursor: Any, exact_hash: str, event_id: int) -> None:
    cursor.execute(
        """
        INSERT INTO news_sentiment_shadow_aliases (exact_hash, event_id)
        VALUES (%s, %s)
        ON CONFLICT (exact_hash) DO NOTHING;
        """,
        (exact_hash, event_id),
    )


def _create_observations(
    cursor: Any,
    *,
    event_id: int,
    assets: Sequence[str],
    horizons: Sequence[int],
    prices: Dict[str, float],
    baseline_at: datetime,
    direction_score: float,
) -> int:
    created = 0
    expected = _expected_direction(direction_score)
    for symbol in assets:
        price = prices.get(symbol)
        if price is None:
            continue
        for horizon in horizons:
            cursor.execute(
                """
                INSERT INTO news_sentiment_shadow_observations (
                    event_id, symbol, horizon_minutes, baseline_at,
                    baseline_price, target_at, expected_direction
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, symbol, horizon_minutes) DO NOTHING;
                """,
                (
                    event_id,
                    symbol,
                    int(horizon),
                    baseline_at,
                    price,
                    baseline_at + timedelta(minutes=int(horizon)),
                    expected,
                ),
            )
            created += max(cursor.rowcount, 0)
    return created


def _collect_news(
    cursor: Any,
    *,
    items: Iterable[Dict[str, Any]],
    prices: Dict[str, float],
    baseline_at: datetime,
) -> Dict[str, int]:
    max_age_minutes = max(15, _env_int("NEWS_MAX_AGE_MINUTES", 180))
    min_relevance = _env_float("NEWS_MIN_RELEVANCE", 0.60)
    now = _utc_now()
    counts = {
        "fetched": 0,
        "new_events": 0,
        "exact_duplicates": 0,
        "semantic_duplicates": 0,
        "irrelevant": 0,
        "stale": 0,
        "observations": 0,
    }

    for item in items:
        counts["fetched"] += 1
        published_at = _parse_datetime(item.get("published_at"))
        if published_at is not None:
            age_minutes = (now - published_at).total_seconds() / 60.0
            if age_minutes > max_age_minutes:
                counts["stale"] += 1
                continue

        classification = classify_news_item(item)
        if (
            classification["relevance_score"] < min_relevance
            or not classification["assets"]
        ):
            counts["irrelevant"] += 1
            continue

        exact_hash = exact_news_hash(item)
        existing_id = _alias_event_id(cursor, exact_hash)
        if existing_id is not None:
            _touch_exact_duplicate(cursor, exact_hash, existing_id)
            counts["exact_duplicates"] += 1
            continue

        semantic_id = _find_semantic_duplicate(
            cursor,
            classification=classification,
            title=str(item.get("title") or ""),
            published_at=published_at,
        )
        if semantic_id is not None:
            _insert_alias(cursor, exact_hash, semantic_id)
            cursor.execute(
                """
                UPDATE news_sentiment_shadow_events
                SET last_seen_at = NOW(),
                    semantic_duplicate_count = semantic_duplicate_count + 1
                WHERE id = %s;
                """,
                (semantic_id,),
            )
            counts["semantic_duplicates"] += 1
            continue

        cursor.execute(
            """
            INSERT INTO news_sentiment_shadow_events (
                event_kind, provider, source_identifier, canonical_url,
                title, summary, published_at, semantic_cluster_key,
                event_category, assets, relevance_score, direction_score,
                confidence, raw_payload
            ) VALUES (
                'news', %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            ) RETURNING id;
            """,
            (
                item.get("source") or "unknown",
                item.get("guid"),
                canonicalize_url(str(item.get("url") or "")),
                item.get("title"),
                item.get("summary"),
                published_at,
                classification["semantic_cluster_key"],
                classification["event_category"],
                Json(classification["assets"]),
                classification["relevance_score"],
                classification["direction_score"],
                classification["confidence"],
                Json(item),
            ),
        )
        event_id = int(cursor.fetchone()[0])
        _insert_alias(cursor, exact_hash, event_id)
        counts["new_events"] += 1
        counts["observations"] += _create_observations(
            cursor,
            event_id=event_id,
            assets=classification["assets"],
            horizons=NEWS_HORIZONS,
            prices=prices,
            baseline_at=baseline_at,
            direction_score=classification["direction_score"],
        )
    return counts


def _collect_sentiment(
    cursor: Any,
    *,
    payload: Optional[Dict[str, Any]],
    prices: Dict[str, float],
    baseline_at: datetime,
) -> Dict[str, int]:
    counts = {"new_events": 0, "exact_duplicates": 0, "observations": 0}
    if not isinstance(payload, dict):
        return counts

    provider = "coinmarketcap_fear_greed"
    exact_hash = exact_sentiment_hash(provider, payload)
    existing_id = _alias_event_id(cursor, exact_hash)
    if existing_id is not None:
        _touch_exact_duplicate(cursor, exact_hash, existing_id)
        counts["exact_duplicates"] += 1
        return counts

    raw_value = payload.get("valore") if "valore" in payload else payload.get("value")
    try:
        value = float(raw_value) if raw_value is not None else None
    except (TypeError, ValueError):
        value = None
    direction = sentiment_direction(value)
    timestamp = _parse_datetime(payload.get("timestamp"))
    classification = payload.get("classificazione") or payload.get("classification")
    semantic_key = hashlib.sha256(
        f"{provider}|{payload.get('timestamp')}".encode("utf-8")
    ).hexdigest()

    cursor.execute(
        """
        INSERT INTO news_sentiment_shadow_events (
            event_kind, provider, source_identifier, published_at,
            semantic_cluster_key, event_category, assets,
            relevance_score, direction_score, confidence,
            sentiment_value, sentiment_classification, raw_payload
        ) VALUES (
            'sentiment', %s, %s, %s, %s, 'fear_greed', %s,
            1.0, %s, 0.65, %s, %s, %s
        ) RETURNING id;
        """,
        (
            provider,
            str(payload.get("timestamp") or ""),
            timestamp,
            semantic_key,
            Json(list(TARGET_SYMBOLS)),
            direction,
            value,
            classification,
            Json(payload),
        ),
    )
    event_id = int(cursor.fetchone()[0])
    _insert_alias(cursor, exact_hash, event_id)
    counts["new_events"] += 1
    counts["observations"] += _create_observations(
        cursor,
        event_id=event_id,
        assets=TARGET_SYMBOLS,
        horizons=SENTIMENT_HORIZONS,
        prices=prices,
        baseline_at=baseline_at,
        direction_score=direction,
    )
    return counts


def _price_at_or_after(
    cursor: Any,
    *,
    symbol: str,
    target_at: datetime,
    tolerance_minutes: int,
) -> Optional[Tuple[datetime, float]]:
    cursor.execute(
        """
        SELECT ts, price
        FROM indicators_contexts
        WHERE UPPER(ticker) = %s
          AND ts >= %s
          AND ts <= %s
          AND price IS NOT NULL
        ORDER BY ts ASC
        LIMIT 1;
        """,
        (symbol, target_at, target_at + timedelta(minutes=tolerance_minutes)),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return row[0], float(row[1])


def _mfe_mae(
    cursor: Any,
    *,
    symbol: str,
    baseline_at: datetime,
    realized_at: datetime,
    baseline_price: float,
) -> Tuple[Optional[float], Optional[float]]:
    cursor.execute(
        """
        SELECT MAX(price), MIN(price)
        FROM indicators_contexts
        WHERE UPPER(ticker) = %s
          AND ts >= %s
          AND ts <= %s
          AND price IS NOT NULL;
        """,
        (symbol, baseline_at, realized_at),
    )
    row = cursor.fetchone()
    if not row or row[0] is None or row[1] is None:
        return None, None
    maximum = float(row[0])
    minimum = float(row[1])
    return (
        (maximum / baseline_price - 1.0) * 100.0,
        (minimum / baseline_price - 1.0) * 100.0,
    )


def evaluate_pending_observations(limit: int = 250) -> Dict[str, int]:
    counts = {"complete": 0, "missing": 0, "pending": 0}
    now = _utc_now()
    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, symbol, horizon_minutes, baseline_at,
                       baseline_price, target_at, expected_direction
                FROM news_sentiment_shadow_observations
                WHERE status = 'pending' AND target_at <= NOW()
                ORDER BY target_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED;
                """,
                (max(1, int(limit)),),
            )
            rows = cursor.fetchall()
            for row in rows:
                observation_id = int(row[0])
                symbol = str(row[1])
                horizon = int(row[2])
                baseline_at = row[3]
                baseline_price = float(row[4])
                target_at = row[5]
                expected_direction = int(row[6])
                tolerance = 30 if horizon <= 60 else 180
                result = _price_at_or_after(
                    cursor,
                    symbol=symbol,
                    target_at=target_at,
                    tolerance_minutes=tolerance,
                )
                if result is None:
                    expiry = target_at + timedelta(minutes=max(tolerance * 3, 120))
                    if now >= expiry:
                        cursor.execute(
                            """
                            UPDATE news_sentiment_shadow_observations
                            SET status = 'missing', updated_at = NOW()
                            WHERE id = %s;
                            """,
                            (observation_id,),
                        )
                        counts["missing"] += 1
                    else:
                        counts["pending"] += 1
                    continue

                realized_at, realized_price = result
                return_pct = (realized_price / baseline_price - 1.0) * 100.0
                mfe_pct, mae_pct = _mfe_mae(
                    cursor,
                    symbol=symbol,
                    baseline_at=baseline_at,
                    realized_at=realized_at,
                    baseline_price=baseline_price,
                )
                direction_correct = (
                    None
                    if expected_direction == 0
                    else bool(return_pct * expected_direction > 0)
                )
                cursor.execute(
                    """
                    UPDATE news_sentiment_shadow_observations
                    SET realized_at = %s,
                        realized_price = %s,
                        realized_return_pct = %s,
                        mfe_pct = %s,
                        mae_pct = %s,
                        direction_correct = %s,
                        status = 'complete',
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (
                        realized_at,
                        realized_price,
                        return_pct,
                        mfe_pct,
                        mae_pct,
                        direction_correct,
                        observation_id,
                    ),
                )
                counts["complete"] += 1
        connection.commit()
    return counts


def get_shadow_progress() -> Dict[str, Any]:
    with db_utils.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE event_kind = 'news') AS news_unique,
                    COALESCE(SUM(exact_duplicate_count) FILTER (WHERE event_kind = 'news'), 0),
                    COALESCE(SUM(semantic_duplicate_count) FILTER (WHERE event_kind = 'news'), 0),
                    COUNT(*) FILTER (WHERE event_kind = 'sentiment') AS sentiment_unique,
                    COALESCE(SUM(exact_duplicate_count) FILTER (WHERE event_kind = 'sentiment'), 0)
                FROM news_sentiment_shadow_events;
                """
            )
            event_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    COUNT(DISTINCT event_id) FILTER (
                        WHERE horizon_minutes = 15 AND status = 'complete'
                    ),
                    COUNT(DISTINCT event_id) FILTER (
                        WHERE horizon_minutes = 60 AND status = 'complete'
                    ),
                    COUNT(DISTINCT event_id) FILTER (
                        WHERE horizon_minutes = 360 AND status = 'complete'
                    ),
                    COUNT(DISTINCT event_id) FILTER (
                        WHERE horizon_minutes = 1440 AND status = 'complete'
                    )
                FROM news_sentiment_shadow_observations;
                """
            )
            observation_row = cursor.fetchone()

    news_unique = int(event_row[0] or 0)
    return {
        "mode": "shadow",
        "live_weight": 0,
        "news_unique_events": news_unique,
        "news_minimum_target": 30,
        "news_preferred_target": 50,
        "news_progress_to_minimum_pct": min(100.0, news_unique / 30.0 * 100.0),
        "news_exact_duplicates": int(event_row[1] or 0),
        "news_semantic_duplicates": int(event_row[2] or 0),
        "sentiment_unique_events": int(event_row[3] or 0),
        "sentiment_exact_duplicates": int(event_row[4] or 0),
        "news_completed_15m": int(observation_row[0] or 0),
        "news_completed_60m": int(observation_row[1] or 0),
        "sentiment_completed_6h": int(observation_row[2] or 0),
        "sentiment_completed_24h": int(observation_row[3] or 0),
    }


def run_news_sentiment_shadow(indicators: Any) -> Dict[str, Any]:
    """Collect and evaluate shadow events without affecting live decisions.

    All exceptions are contained here. The caller may log the returned error, but
    this subsystem must never block an order, stop-loss or position-management
    action.
    """

    if not _env_bool("NEWS_SENTIMENT_SHADOW_ENABLED", True):
        return {"mode": "disabled", "live_weight": 0}

    try:
        ensure_news_sentiment_shadow_schema()
        prices, baseline_at = _extract_prices(indicators)
        if not prices:
            return {
                "mode": "shadow",
                "live_weight": 0,
                "error": "no_baseline_prices",
            }

        news_items = fetch_news_items(max_items=max(1, _env_int("NEWS_SHADOW_MAX_ITEMS", 25)))
        sentiment_payload = get_latest_fear_and_greed()

        with db_utils.get_connection() as connection:
            with connection.cursor() as cursor:
                news_counts = _collect_news(
                    cursor,
                    items=news_items,
                    prices=prices,
                    baseline_at=baseline_at,
                )
                sentiment_counts = _collect_sentiment(
                    cursor,
                    payload=sentiment_payload,
                    prices=prices,
                    baseline_at=baseline_at,
                )
            connection.commit()

        evaluation = evaluate_pending_observations()
        progress = get_shadow_progress()
        return {
            **progress,
            "collector": {
                "news": news_counts,
                "sentiment": sentiment_counts,
                "evaluation": evaluation,
            },
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[news_sentiment_shadow] non-blocking error: {exc}")
        return {
            "mode": "shadow",
            "live_weight": 0,
            "error": str(exc),
        }

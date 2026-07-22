import logging
import re
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Dict, Iterable, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)
NEWS_FEED_URL = "https://coinjournal.net/news/feed/"
_TRACKING_QUERY_PREFIXES = ("utm_", "mc_", "ref")


def _strip_html_tags(text: str) -> str:
    if not text:
        return ""
    cleaned = unescape(text)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def canonicalize_url(url: str) -> str:
    """Return a stable URL suitable for exact deduplication."""

    value = (url or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        filtered = [
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith(_TRACKING_QUERY_PREFIXES)
        ]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                path,
                urlencode(filtered, doseq=True),
                "",
            )
        )
    except Exception:
        return value


def fetch_news_items(max_items: int = 25) -> List[Dict[str, Any]]:
    """Fetch structured CoinJournal items.

    This function is used by the shadow collector. The existing text interface
    remains available through ``fetch_latest_news`` for the live LLM prompt.
    """

    try:
        response = requests.get(NEWS_FEED_URL, timeout=10)
        if response.status_code != 200:
            logger.warning("Failed to fetch news feed: status %s", response.status_code)
            return []

        root = ET.fromstring(response.content)
        channel = root.find("channel")
        if channel is None:
            return []

        items: List[Dict[str, Any]] = []
        for node in channel.findall("item")[: max(0, int(max_items))]:
            title = _strip_html_tags(node.findtext("title") or "")
            raw_date = (node.findtext("pubDate") or "").strip()
            summary = _strip_html_tags(node.findtext("description") or "")
            summary = re.sub(
                r"The post .*? appeared first on .*",
                "",
                summary,
                flags=re.IGNORECASE,
            ).strip()
            link = canonicalize_url(node.findtext("link") or "")
            guid = (node.findtext("guid") or "").strip()

            published_at = None
            if raw_date:
                try:
                    parsed = parsedate_to_datetime(raw_date)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    else:
                        parsed = parsed.astimezone(timezone.utc)
                    published_at = parsed.isoformat()
                except Exception:
                    published_at = None

            if not any((title, summary, link, guid)):
                continue
            items.append(
                {
                    "source": "coinjournal",
                    "guid": guid,
                    "url": link,
                    "title": title,
                    "summary": summary,
                    "published_at": published_at,
                    "raw_pub_date": raw_date,
                }
            )
        return items
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to process news feed: %s", exc)
        return []


def format_news_items(items: Iterable[Dict[str, Any]], max_chars: int = 4000) -> str:
    entries: List[str] = []
    for item in items:
        published_at = str(item.get("published_at") or item.get("raw_pub_date") or "")
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        parts = [part for part in (published_at, title) if part]
        entry = " | ".join(parts)
        if summary:
            entry = f"{entry}: {summary}" if entry else summary
        if not entry:
            continue

        existing = "\n".join(entries)
        candidate = f"{existing}\n{entry}" if existing else entry
        if len(candidate) > max_chars:
            remaining = max_chars - len(existing) - (1 if existing else 0)
            if remaining > 0:
                truncated = entry[:remaining].rstrip()
                if len(truncated) < len(entry):
                    truncated = truncated.rstrip(" .,;:-") + "..."
                entries.append(truncated)
            break
        entries.append(entry)
    return "\n".join(entries)


def fetch_latest_news(max_chars: int = 4000) -> str:
    return format_news_items(fetch_news_items(), max_chars=max_chars)

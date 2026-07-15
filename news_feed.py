import logging
import re
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import List

import requests

logger = logging.getLogger(__name__)
NEWS_FEED_URL = "https://coinjournal.net/news/feed/"


def _strip_html_tags(text: str) -> str:
    if not text:
        return ""
    cleaned = unescape(text)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def fetch_latest_news(max_chars: int = 4000) -> str:
    try:
        response = requests.get(NEWS_FEED_URL, timeout=10)
        if response.status_code != 200:
            logger.warning("Failed to fetch news feed: status %s", response.status_code)
            return ""
        root = ET.fromstring(response.content)
        channel = root.find("channel")
        if channel is None:
            return ""
        entries: List[str] = []
        for item in channel.findall("item"):
            title = _strip_html_tags(item.findtext("title") or "")
            raw_date = (item.findtext("pubDate") or "").strip()
            summary = _strip_html_tags(item.findtext("description") or "")
            summary = re.sub(
                r"The post .*? appeared first on .*",
                "",
                summary,
                flags=re.IGNORECASE,
            ).strip()
            formatted_time = raw_date
            if raw_date:
                try:
                    parsed = parsedate_to_datetime(raw_date)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    else:
                        parsed = parsed.astimezone(timezone.utc)
                    formatted_time = parsed.strftime("%Y-%m-%d %H:%M:%SZ")
                except Exception:
                    pass
            parts = [part for part in (formatted_time, title) if part]
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
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to process news feed: %s", exc)
        return f"Failed to process news feed: {exc}"

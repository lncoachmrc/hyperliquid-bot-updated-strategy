from __future__ import annotations

import copy
import os
import threading
from typing import Any, Callable, Dict

import db_utils


_INSTALL_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_INSTALLED = False
_ORIGINAL_LOG_BOT_OPERATION: Callable[..., int] | None = None


def _enabled() -> bool:
    raw = os.getenv("NEWS_SENTIMENT_SHADOW_ENABLED", "true")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _run_collector(indicators: Any) -> None:
    if not _RUN_LOCK.acquire(blocking=False):
        print("[news_sentiment_shadow] previous collection still running; cycle skipped")
        return
    try:
        from news_sentiment_shadow import run_news_sentiment_shadow

        result: Dict[str, Any] = run_news_sentiment_shadow(indicators)
        print(
            "[news_sentiment_shadow] "
            f"unique_news={result.get('news_unique_events', 0)}/30, "
            f"exact_dups={result.get('news_exact_duplicates', 0)}, "
            f"semantic_dups={result.get('news_semantic_duplicates', 0)}, "
            f"completed_15m={result.get('news_completed_15m', 0)}, "
            f"completed_60m={result.get('news_completed_60m', 0)}, "
            f"live_weight={result.get('live_weight', 0)}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[news_sentiment_shadow] non-blocking thread error: {exc}")
    finally:
        _RUN_LOCK.release()


def install_shadow_runtime_hook() -> None:
    """Wrap db_utils.log_bot_operation once.

    The original transaction completes first. The shadow collector is then
    started in a background thread, so exchange execution is never delayed by
    news APIs, sentiment APIs or shadow database evaluation.
    """

    global _INSTALLED, _ORIGINAL_LOG_BOT_OPERATION
    if not _enabled() or _INSTALLED:
        return

    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        original = db_utils.log_bot_operation
        _ORIGINAL_LOG_BOT_OPERATION = original

        def wrapped_log_bot_operation(*args: Any, **kwargs: Any) -> int:
            operation_id = original(*args, **kwargs)
            indicators = kwargs.get("indicators")
            if indicators is not None:
                try:
                    snapshot = copy.deepcopy(indicators)
                except Exception:
                    snapshot = indicators
                thread = threading.Thread(
                    target=_run_collector,
                    args=(snapshot,),
                    name="news-sentiment-shadow",
                    daemon=False,
                )
                thread.start()
            return operation_id

        db_utils.log_bot_operation = wrapped_log_bot_operation
        _INSTALLED = True
        print("[news_sentiment_shadow] runtime hook installed; live weight=0")

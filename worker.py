"""Persistent Railway worker for the existing one-cycle trading entry point."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import psycopg2

from db_init import initialize_database_with_retry
from runtime_config import env_bool, env_int

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger("trading_worker")
ROOT = Path(__file__).resolve().parent
STOP_EVENT = threading.Event()


def _request_shutdown(signum: int, _frame: object) -> None:
    LOGGER.info("Ricevuto segnale %s: arresto dopo il ciclo corrente.", signum)
    STOP_EVENT.set()


def _database_url() -> str:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL non impostata")
    return dsn


@contextmanager
def postgres_advisory_lock(lock_id: int) -> Iterator[bool]:
    """Prevent two Railway replicas from running the same wallet cycle."""
    connection = psycopg2.connect(
        _database_url(),
        connect_timeout=10,
        application_name="hyperliquid_trading_worker",
    )
    connection.autocommit = True
    acquired = False

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s);", (lock_id,))
            row = cursor.fetchone()
            acquired = bool(row and row[0])

        yield acquired
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s);", (lock_id,))
            except Exception:  # noqa: BLE001
                LOGGER.exception("Errore durante il rilascio dell'advisory lock.")
        connection.close()


def seconds_since_last_persisted_cycle() -> Optional[float]:
    """Return age of the latest persisted bot cycle, or None when no cycle exists."""
    connection = psycopg2.connect(
        _database_url(),
        connect_timeout=10,
        application_name="hyperliquid_cycle_gap_guard",
    )
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXTRACT(
                    EPOCH FROM (clock_timestamp() - MAX(created_at))
                )
                FROM bot_operations;
                """
            )
            row = cursor.fetchone()
            if not row or row[0] is None:
                return None
            return max(0.0, float(row[0]))
    finally:
        connection.close()


def cycle_delay_for_recent_persisted_cycle(
    last_cycle_age_seconds: Optional[float],
    minimum_gap_seconds: int,
) -> float:
    """Return how long a restart should wait before another trading cycle."""
    if minimum_gap_seconds <= 0 or last_cycle_age_seconds is None:
        return 0.0
    age = max(0.0, float(last_cycle_age_seconds))
    return max(0.0, float(minimum_gap_seconds) - age)


def run_trading_cycle() -> int:
    """Run the unchanged main.py cycle in a fresh child process."""
    result = subprocess.run(
        [sys.executable, "-u", "main.py"],
        cwd=ROOT,
        check=False,
    )
    return result.returncode


def run_worker() -> None:
    interval_seconds = env_int("BOT_INTERVAL_SECONDS", 600, minimum=60)
    lock_id = env_int("BOT_LOCK_ID", 7_260_315, minimum=1)
    run_immediately = env_bool("BOT_RUN_IMMEDIATELY", True)
    minimum_cycle_gap_seconds = env_int(
        "BOT_MIN_CYCLE_GAP_SECONDS", 120, minimum=0
    )

    initialize_database_with_retry()
    LOGGER.info(
        "Worker avviato: intervallo=%ss, run_immediately=%s, lock_id=%s, "
        "minimum_cycle_gap=%ss.",
        interval_seconds,
        run_immediately,
        lock_id,
        minimum_cycle_gap_seconds,
    )

    if not run_immediately:
        STOP_EVENT.wait(interval_seconds)

    while not STOP_EVENT.is_set():
        cycle_started = time.monotonic()
        wait_override_seconds: Optional[float] = None

        try:
            with postgres_advisory_lock(lock_id) as acquired:
                if not acquired:
                    LOGGER.warning(
                        "Ciclo saltato: un'altra replica possiede il lock PostgreSQL %s.",
                        lock_id,
                    )
                else:
                    last_cycle_age = seconds_since_last_persisted_cycle()
                    recent_cycle_delay = cycle_delay_for_recent_persisted_cycle(
                        last_cycle_age,
                        minimum_cycle_gap_seconds,
                    )
                    if recent_cycle_delay > 0:
                        # A Railway restart/deploy may start the worker immediately
                        # after a cycle that has already persisted a decision. Wait
                        # only the remaining guard interval, then reevaluate under
                        # the advisory lock rather than creating a duplicate cycle.
                        wait_override_seconds = recent_cycle_delay
                        LOGGER.warning(
                            "Ciclo rinviato dal recent-cycle guard: ultimo ciclo "
                            "persistito %.1fs fa; attendo altri %.1fs (minimo=%ss).",
                            last_cycle_age or 0.0,
                            recent_cycle_delay,
                            minimum_cycle_gap_seconds,
                        )
                    else:
                        return_code = run_trading_cycle()
                        if return_code == 0:
                            LOGGER.info("Ciclo terminato con codice 0.")
                        else:
                            LOGGER.error(
                                "Ciclo terminato con codice non nullo: %s.", return_code
                            )
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "Errore nel worker; il servizio resta attivo per il ciclo successivo."
            )

        elapsed = time.monotonic() - cycle_started
        if wait_override_seconds is not None:
            STOP_EVENT.wait(max(1.0, wait_override_seconds))
        else:
            STOP_EVENT.wait(max(0.0, interval_seconds - elapsed))

    LOGGER.info("Worker arrestato correttamente.")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    run_worker()

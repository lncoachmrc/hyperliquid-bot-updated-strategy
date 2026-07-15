"""Initialize the Railway PostgreSQL schema with bounded retries."""

from __future__ import annotations

import logging
import time

import db_utils
from runtime_config import env_int

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger("db_init")


def initialize_database_with_retry() -> None:
    attempts = env_int("DB_INIT_ATTEMPTS", 30, minimum=1)
    delay_seconds = env_int("DB_INIT_RETRY_SECONDS", 2, minimum=1)

    for attempt in range(1, attempts + 1):
        try:
            db_utils.init_db()
            LOGGER.info("Schema PostgreSQL inizializzato o già aggiornato.")
            return
        except Exception:  # noqa: BLE001
            if attempt == attempts:
                LOGGER.exception(
                    "Inizializzazione PostgreSQL fallita dopo %s tentativi.",
                    attempts,
                )
                raise

            LOGGER.warning(
                "PostgreSQL non ancora disponibile (%s/%s); nuovo tentativo tra %ss.",
                attempt,
                attempts,
                delay_seconds,
                exc_info=True,
            )
            time.sleep(delay_seconds)


if __name__ == "__main__":
    initialize_database_with_retry()

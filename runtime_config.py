"""Runtime configuration helpers for the Railway worker."""

from __future__ import annotations

import os

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False

    raise ValueError(
        f"{name} deve essere uno tra: true/false, 1/0, yes/no, on/off"
    )


def env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    value = default if raw is None or not raw.strip() else int(raw)

    if minimum is not None and value < minimum:
        raise ValueError(f"{name} deve essere >= {minimum}")

    return value

"""Select safe, comparable entry opportunities for Prophet shadow sampling.

Shadow forecasting is intentionally skipped whenever any position is open so it
can never delay live position or stop-loss management. The selector does not
alter the underlying strategy or decision fields.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

from entry_quality_policy import executable_candidate_symbols


def flat_account_shadow_candidates(
    indicators: Iterable[Dict[str, Any]],
    account_status: Mapping[str, Any],
) -> list[str]:
    open_positions = [
        position
        for position in (account_status.get("open_positions") or [])
        if isinstance(position, Mapping)
        and position.get("symbol")
        and abs(_as_float(position.get("size"))) > 0
    ]
    if open_positions:
        return []
    return executable_candidate_symbols(indicators)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

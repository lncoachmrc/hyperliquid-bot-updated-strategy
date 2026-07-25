#!/usr/bin/env python3
"""Reproduce the tactical-fade base-rate audit from blocked_opportunities.csv."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Iterable


SYMBOLS = ("BTC", "ETH", "SOL")


@dataclass(frozen=True)
class Event:
    observed_at: datetime
    completed_bar_at: datetime
    symbol: str
    short_net_return_pct: float


def load_events(
    path: Path,
    *,
    cost_bps: float | None = None,
) -> tuple[int, list[Event]]:
    documented = 0
    matured: list[Event] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (
                row.get("environment") != "V1"
                or row.get("direction_basis")
                != "documented_tactical_long_candidate"
            ):
                continue
            documented += 1
            gross = row.get("gross_return_180m_pct")
            if not gross:
                continue
            sample_key = str(row["sample_key"])
            try:
                completed_bar_at = datetime.fromisoformat(
                    sample_key.split("|", 3)[3]
                )
                observed_at = datetime.fromisoformat(row["timestamp_utc"])
                applied_cost_bps = (
                    float(cost_bps)
                    if cost_bps is not None
                    else float(row["round_trip_cost_bps"])
                )
                short_net = -float(gross) - applied_cost_bps / 100.0
            except (IndexError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid documented sample {sample_key!r}"
                ) from exc
            matured.append(
                Event(
                    observed_at=observed_at,
                    completed_bar_at=completed_bar_at,
                    symbol=str(row["symbol"]),
                    short_net_return_pct=short_net,
                )
            )
    return documented, matured


def non_overlapping_by_symbol(
    events: Iterable[Event],
    horizon_minutes: int = 180,
) -> list[Event]:
    selected: list[Event] = []
    horizon = timedelta(minutes=horizon_minutes)
    for symbol in SYMBOLS:
        locked_until: datetime | None = None
        for event in sorted(
            (item for item in events if item.symbol == symbol),
            key=lambda item: item.observed_at,
        ):
            if locked_until is not None and event.observed_at < locked_until:
                continue
            selected.append(event)
            locked_until = event.observed_at + horizon
    return selected


def globally_locked(
    events: Iterable[Event],
    priority: tuple[str, ...],
    horizon_minutes: int = 180,
) -> list[Event]:
    by_bar: dict[datetime, list[Event]] = {}
    for event in events:
        by_bar.setdefault(event.completed_bar_at, []).append(event)
    selected: list[Event] = []
    locked_until: datetime | None = None
    horizon = timedelta(minutes=horizon_minutes)
    for bar_at in sorted(by_bar):
        available = [
            event
            for event in by_bar[bar_at]
            if locked_until is None or event.observed_at >= locked_until
        ]
        if not available:
            continue
        chosen = min(
            available,
            key=lambda event: priority.index(event.symbol),
        )
        selected.append(chosen)
        locked_until = chosen.observed_at + horizon
    return selected


def stats(events: Iterable[Event]) -> dict[str, float | int | None]:
    values = [event.short_net_return_pct for event in events]
    return {
        "n": len(values),
        "mean_net_return_pct": mean(values) if values else None,
        "positive": sum(value > 0 for value in values),
        "positive_rate": (
            sum(value > 0 for value in values) / len(values)
            if values
            else None
        ),
    }


def build_report(
    documented: int,
    events: list[Event],
) -> dict[str, object]:
    without_best_ten = sorted(
        events,
        key=lambda item: item.short_net_return_pct,
        reverse=True,
    )[10:]
    symbol_stats = {
        symbol: stats(item for item in events if item.symbol == symbol)
        for symbol in SYMBOLS
    }
    day_stats = {
        str(day): stats(
            item for item in events if item.observed_at.date() == day
        )
        for day in sorted({item.observed_at.date() for item in events})
    }
    priority_sensitivity = {}
    for priority in itertools.permutations(SYMBOLS):
        priority_sensitivity[">".join(priority)] = stats(
            globally_locked(events, priority)
        )
    return {
        "documented_samples": documented,
        "matured_samples": len(events),
        "all_matured": stats(events),
        "without_best_ten": stats(without_best_ten),
        "by_symbol": symbol_stats,
        "by_utc_day": day_stats,
        "non_overlapping_inside_each_symbol": stats(
            non_overlapping_by_symbol(events)
        ),
        "one_global_position_priority_sensitivity": (
            priority_sensitivity
        ),
        "limitations": [
            "in_sample",
            "fixed_180m_horizon_selected_after_observation",
            "simulated_cost_not_measured_slippage_or_funding",
            "global replay uses symbol priority because historical spread "
            "was not stored",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("blocked_opportunities_csv", type=Path)
    parser.add_argument(
        "--cost-bps",
        type=float,
        default=None,
        help="override the row cost assumption for stress testing",
    )
    args = parser.parse_args()
    documented, events = load_events(
        args.blocked_opportunities_csv,
        cost_bps=args.cost_bps,
    )
    print(
        json.dumps(
            build_report(documented, events),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

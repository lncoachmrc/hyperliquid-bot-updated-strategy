from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, median, stdev
from typing import Iterable

from hyperliquid_v2.domain.models import QuantEvidence


@dataclass(frozen=True)
class ComparableObservation:
    setup_family: str
    return_15m_pct: float | None
    return_60m_pct: float | None
    return_180m_pct: float | None
    mfe_r: float | None
    mae_r: float | None
    realized_net_r: float | None
    reached_green: bool
    finished_negative: bool


class QuantExpert:
    """Convert comparable historical cases into evidence, never a BUY score."""

    def __init__(self, minimum_operational_samples: int = 50) -> None:
        if minimum_operational_samples < 30:
            raise ValueError("minimum_operational_samples cannot be below 30")
        self.minimum_operational_samples = minimum_operational_samples

    def build(
        self,
        setup_family: str,
        observations: Iterable[ComparableObservation],
    ) -> QuantEvidence:
        comparable = [item for item in observations if item.setup_family == setup_family]
        sample_count = len(comparable)
        limitations: list[str] = []

        if sample_count < 30:
            quality = "insufficient"
            limitations.append("fewer_than_30_comparable_samples")
        elif sample_count < self.minimum_operational_samples:
            quality = "exploratory"
            limitations.append("below_operational_sample_threshold")
        elif sample_count < 100:
            quality = "moderate"
        else:
            quality = "strong"

        net_values = _values(comparable, "realized_net_r")
        confidence_interval = _mean_confidence_interval(net_values)
        green = [item for item in comparable if item.reached_green]
        green_to_red = (
            sum(1 for item in green if item.finished_negative) / len(green)
            if green
            else None
        )

        return QuantEvidence(
            setup_family=setup_family,
            comparable_samples=sample_count,
            probability_positive_15m=_positive_probability(_values(comparable, "return_15m_pct")),
            probability_positive_60m=_positive_probability(_values(comparable, "return_60m_pct")),
            probability_positive_180m=_positive_probability(_values(comparable, "return_180m_pct")),
            median_return_15m_pct=_median_or_none(_values(comparable, "return_15m_pct")),
            median_return_60m_pct=_median_or_none(_values(comparable, "return_60m_pct")),
            median_return_180m_pct=_median_or_none(_values(comparable, "return_180m_pct")),
            median_mfe_r=_median_or_none(_values(comparable, "mfe_r")),
            median_mae_r=_median_or_none(_values(comparable, "mae_r")),
            green_to_red_rate=green_to_red,
            expected_net_value_r=mean(net_values) if net_values else None,
            confidence_interval_r=confidence_interval,
            evidence_quality=quality,
            operational=sample_count >= self.minimum_operational_samples,
            limitations=tuple(limitations),
        )


def _values(items: list[ComparableObservation], attribute: str) -> list[float]:
    result: list[float] = []
    for item in items:
        value = getattr(item, attribute)
        if value is not None:
            result.append(float(value))
    return result


def _positive_probability(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(1 for value in values if value > 0) / len(values)


def _median_or_none(values: list[float]) -> float | None:
    return median(values) if values else None


def _mean_confidence_interval(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    centre = mean(values)
    standard_error = stdev(values) / sqrt(len(values))
    margin = 1.96 * standard_error
    return centre - margin, centre + margin

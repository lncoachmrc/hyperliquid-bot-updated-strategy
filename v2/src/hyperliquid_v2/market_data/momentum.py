from __future__ import annotations

import math
from dataclasses import dataclass

from hyperliquid_v2.market_data.features import FeatureSnapshot


@dataclass(frozen=True)
class PumpMomentum:
    phase: str
    continuation_probability: float
    reversal_probability: float
    price_velocity: float
    price_acceleration: float
    buy_aggression: float
    sell_aggression: float
    book_imbalance: float
    open_interest_confirmation: bool | None
    volume_climax_probability: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class PumpMomentumEngine:
    def assess(self, feature: FeatureSnapshot) -> PumpMomentum:
        buy = feature.buy_aggression if feature.buy_aggression is not None else 0.5
        sell = feature.sell_aggression if feature.sell_aggression is not None else 0.5
        book = feature.book_imbalance if feature.book_imbalance is not None else 0.0
        velocity = math.tanh(feature.price_velocity_bps_15s / 12.0)
        acceleration = math.tanh(feature.price_acceleration_bps / 8.0)
        aggression = 2 * buy - 1
        oi_signal = (
            0.0
            if feature.open_interest_change_pct is None
            else math.tanh(feature.open_interest_change_pct / 0.15)
        )
        continuation = _sigmoid(
            0.10
            + 1.1 * velocity
            + 0.9 * acceleration
            + 0.75 * aggression
            + 0.45 * book
            + 0.30 * oi_signal
        )
        exhaustion_raw = (
            max(0.0, velocity) * max(0.0, -acceleration)
            + max(0.0, sell - 0.5) * 2
            + max(0.0, -book)
        ) * 0.9
        reversal = _sigmoid(
            -0.55
            - 0.55 * velocity
            - 0.65 * acceleration
            + 1.05 * (2 * sell - 1)
            - 0.45 * book
            + exhaustion_raw
        )
        climax = min(
            1.0,
            max(
                0.0,
                0.45 * (feature.trade_notional_30s > 0) * abs(velocity)
                + 0.35 * max(0.0, -acceleration)
                + 0.20 * max(0.0, sell - 0.5) * 2,
            ),
        )
        if continuation >= 0.65 and acceleration > 0:
            phase = "expansion"
        elif velocity > 0 and acceleration < 0 and reversal > continuation:
            phase = "exhaustion"
        elif velocity > 0:
            phase = "continuation"
        elif velocity < 0 and reversal >= 0.60:
            phase = "reversal"
        else:
            phase = "neutral"
        oi_confirmation = (
            None
            if feature.open_interest_change_pct is None
            else (
                feature.open_interest_change_pct >= 0
                if velocity >= 0
                else feature.open_interest_change_pct <= 0
            )
        )
        return PumpMomentum(
            phase=phase,
            continuation_probability=continuation,
            reversal_probability=reversal,
            price_velocity=velocity,
            price_acceleration=acceleration,
            buy_aggression=buy,
            sell_aggression=sell,
            book_imbalance=book,
            open_interest_confirmation=oi_confirmation,
            volume_climax_probability=climax,
        )


def _sigmoid(value: float) -> float:
    if value >= 0:
        exp = math.exp(-value)
        return 1 / (1 + exp)
    exp = math.exp(value)
    return exp / (1 + exp)

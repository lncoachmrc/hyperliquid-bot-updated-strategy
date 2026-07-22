"""Non-operational Prophet policy evaluation for comparable shadow samples.

Forecasts are attached to strategy snapshots for audit only. The module never
changes recommended_action, execution_feasible, exposure, leverage, stop distance
or any live decision field. The LLM is intentionally not shown these values while
shadow mode is active.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping

from strategy_config import DEFAULT_STRATEGY_CONFIG, StrategyConfig


CANDIDATE_ACTIONS = {"long_candidate", "tactical_long_candidate"}


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "1.0", "yes"}
    return False


def _normalise_forecasts(raw: Any) -> list[Dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _horizon_minutes(item: Mapping[str, Any]) -> int | None:
    explicit = _as_int(item.get("Horizon Minutes") or item.get("horizon_minutes"))
    if explicit in {15, 60}:
        return explicit
    timeframe = str(item.get("Timeframe") or item.get("timeframe") or "").lower()
    if "15" in timeframe:
        return 15
    if "ora" in timeframe or "hour" in timeframe or "1h" in timeframe:
        return 60
    return None


def _forecast_map(raw: Any) -> Dict[str, Dict[int, Dict[str, Any]]]:
    result: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for item in _normalise_forecasts(raw):
        symbol = str(item.get("Ticker") or item.get("ticker") or "").upper()
        horizon = _horizon_minutes(item)
        if not symbol or horizon not in {15, 60}:
            continue
        result.setdefault(symbol, {})[horizon] = item
    return result


def _forecast_view(item: Mapping[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    return {
        "horizon_minutes": _horizon_minutes(item),
        "last_price": _as_float(item.get("Ultimo Prezzo") or item.get("last_price")),
        "prediction": _as_float(item.get("Previsione") or item.get("prediction")),
        "lower_bound": _as_float(
            item.get("Limite Inferiore") or item.get("lower_bound")
        ),
        "upper_bound": _as_float(
            item.get("Limite Superiore") or item.get("upper_bound")
        ),
        "change_pct": _as_float(item.get("Variazione %") or item.get("change_pct")),
        "forecast_generated_at_ms": _as_int(
            item.get("Forecast Generated At") or item.get("forecast_generated_at")
        ),
        "target_timestamp_ms": _as_int(
            item.get("Timestamp Previsione") or item.get("forecast_timestamp")
        ),
        "minutes_to_target": _as_float(
            item.get("Minutes To Target") or item.get("minutes_to_target")
        ),
        "source_price_timestamp_ms": _as_int(
            item.get("Source Price Timestamp") or item.get("source_price_timestamp")
        ),
        "error": item.get("error"),
    }


def _evaluate_shadow_policy(
    forecast_15m: Dict[str, Any] | None,
    forecast_1h: Dict[str, Any] | None,
    cfg: StrategyConfig,
) -> Dict[str, Any]:
    change_15m = forecast_15m.get("change_pct") if forecast_15m else None
    change_1h = forecast_1h.get("change_pct") if forecast_1h else None

    if change_1h is None:
        verdict = "unavailable"
        would_veto = False
        would_delay = False
        reason = "missing_normalized_1h_forecast"
    elif change_1h <= cfg.prophet_shadow_1h_veto_threshold_pct:
        verdict = "would_veto_1h_negative"
        would_veto = True
        would_delay = False
        reason = "normalized_1h_forecast_below_controlled_veto_threshold"
    elif change_1h < cfg.prophet_shadow_1h_positive_threshold_pct:
        verdict = "would_require_exceptional_setup_1h_neutral"
        would_veto = False
        would_delay = False
        reason = "normalized_1h_forecast_inside_neutral_band"
    elif (
        change_15m is not None
        and change_15m <= cfg.prophet_shadow_15m_timing_delay_threshold_pct
    ):
        verdict = "would_delay_entry_15m_timing"
        would_veto = False
        would_delay = True
        reason = "positive_1h_but_negative_15m_timing_forecast"
    else:
        verdict = "would_allow"
        would_veto = False
        would_delay = False
        reason = "prophet_shadow_filters_passed"

    return {
        "verdict": verdict,
        "reason": reason,
        "would_veto_entry": would_veto,
        "would_delay_entry": would_delay,
        "one_hour_change_pct": change_1h,
        "fifteen_minute_change_pct": change_15m,
        "thresholds": {
            "one_hour_veto_pct": cfg.prophet_shadow_1h_veto_threshold_pct,
            "one_hour_positive_pct": cfg.prophet_shadow_1h_positive_threshold_pct,
            "fifteen_minute_timing_delay_pct": cfg.prophet_shadow_15m_timing_delay_threshold_pct,
        },
    }


def attach_prophet_shadow_evaluations(
    indicators: Iterable[Dict[str, Any]],
    forecasts: Any,
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
    *,
    evaluated_at: datetime | None = None,
) -> Dict[str, Any]:
    """Attach hypothetical Prophet policy outcomes to executable candidates."""
    forecast_by_symbol = _forecast_map(forecasts)
    evaluation_time = evaluated_at or datetime.now(timezone.utc)
    if evaluation_time.tzinfo is None:
        evaluation_time = evaluation_time.replace(tzinfo=timezone.utc)
    evaluation_time = evaluation_time.astimezone(timezone.utc)

    summary: Dict[str, Any] = {
        "mode": "shadow",
        "operational": False,
        "policy_version": cfg.prophet_shadow_policy_version,
        "minimum_sample_size": cfg.prophet_shadow_min_sample_size,
        "preferred_sample_size": cfg.prophet_shadow_preferred_sample_size,
        "evaluated_at": evaluation_time.isoformat(),
        "observations": {},
    }

    for item in indicators:
        if not isinstance(item, dict) or not item.get("ticker"):
            continue
        symbol = str(item.get("ticker")).upper()
        strategy = item.get("strategy") or {}
        if not isinstance(strategy, dict):
            continue
        feasible_raw = strategy.get("execution_feasible")
        feasible = True if feasible_raw is None else _as_bool(feasible_raw)
        if strategy.get("recommended_action") not in CANDIDATE_ACTIONS or not feasible:
            continue

        horizons = forecast_by_symbol.get(symbol) or {}
        forecast_15m = _forecast_view(horizons.get(15))
        forecast_1h = _forecast_view(horizons.get(60))
        policy = _evaluate_shadow_policy(forecast_15m, forecast_1h, cfg)
        tactical = strategy.get("tactical_intraday") or {}
        bar_close = (
            tactical.get("completed_bar_close_time")
            if isinstance(tactical, Mapping)
            else None
        )
        sample_key = "|".join(
            [
                cfg.prophet_shadow_policy_version,
                symbol,
                str(bar_close or "unknown_bar"),
            ]
        )
        observation = {
            "mode": "shadow",
            "operational": False,
            "policy_version": cfg.prophet_shadow_policy_version,
            "sample_key": sample_key,
            "sample_eligible": bool(bar_close and forecast_15m and forecast_1h),
            "evaluated_at": evaluation_time.isoformat(),
            "ticker": symbol,
            "strategy_version": strategy.get("strategy_version") or cfg.version,
            "completed_15m_bar_close_time": bar_close,
            "live_recommended_action_unchanged": strategy.get("recommended_action"),
            "live_execution_feasible_unchanged": feasible,
            "live_effective_exposure_unchanged": strategy.get(
                "recommended_effective_exposure_before_drawdown"
            ),
            "live_exchange_leverage_unchanged": strategy.get(
                "recommended_exchange_leverage_before_drawdown"
            ),
            "forecast_15m": forecast_15m,
            "forecast_1h": forecast_1h,
            "hypothetical_policy": policy,
            "activation_rule": (
                "Remain non-operational until at least 30 comparable unique sample_keys; "
                "prefer 50 before considering live weight."
            ),
        }
        strategy["prophet_shadow"] = observation
        item["strategy"] = strategy
        summary["observations"][symbol] = deepcopy(observation)

    summary["observation_count"] = len(summary["observations"])
    return summary

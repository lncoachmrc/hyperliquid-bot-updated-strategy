"""Deterministic entry-quality controls applied after exchange feasibility.

The daily/tactical strategy may identify a candidate, but adverse-regime longs
must also pass stricter countertrend and anti-chase checks. This module can only
remove entry eligibility; it never creates a candidate or increases exposure,
leverage, stop distance or risk.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, Mapping

from strategy_config import DEFAULT_STRATEGY_CONFIG, StrategyConfig


CANDIDATE_ACTIONS = {"long_candidate", "tactical_long_candidate"}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "1.0", "yes"}
    return False


def _open_long_symbols(
    account_status: Mapping[str, Any], cfg: StrategyConfig
) -> set[str]:
    authorised = {symbol.upper() for symbol in cfg.symbols}
    result: set[str] = set()
    for position in account_status.get("open_positions") or []:
        symbol = str(position.get("symbol") or "").upper()
        side = str(position.get("side") or "").lower()
        size = _as_float(position.get("size"))
        if symbol in authorised and (side == "long" or size > 0):
            result.add(symbol)
    return result


def _zero_leverage_evidence(value: Any, reason: str) -> Any:
    if not isinstance(value, Mapping):
        return value
    updated = dict(value)
    for key in (
        "effective_exposure",
        "represented_effective_exposure",
        "target_portion_of_balance",
        "estimated_account_risk_at_stop",
    ):
        if key in updated:
            updated[key] = 0.0
    if "exchange_leverage" in updated:
        updated["exchange_leverage"] = 1
    updated["entry_quality_blocked"] = True
    updated["entry_quality_block_reason"] = reason
    return updated


def _block_entry(strategy: Dict[str, Any], reason: str) -> None:
    strategy["recommended_action"] = "hold_or_flat"
    strategy["recommended_effective_exposure_before_drawdown"] = 0.0
    strategy["represented_effective_exposure_before_drawdown"] = 0.0
    strategy["recommended_exchange_leverage_before_drawdown"] = 1
    strategy["recommended_balance_portion_before_drawdown"] = 0.0
    strategy["estimated_account_risk_at_stop_before_drawdown"] = 0.0
    strategy["execution_feasible"] = False

    feasibility = strategy.get("execution_feasibility") or {}
    if isinstance(feasibility, Mapping):
        feasibility = dict(feasibility)
        feasibility.update(
            {
                "candidate_action": False,
                "final_effective_exposure": 0.0,
                "final_exchange_leverage": 1,
                "final_target_portion_of_balance": 0.0,
                "estimated_account_risk_at_stop": 0.0,
                "recommended_order_notional_usd": 0.0,
                "reason": reason,
            }
        )
        strategy["execution_feasibility"] = feasibility

    strategy["dynamic_leverage_policy"] = _zero_leverage_evidence(
        strategy.get("dynamic_leverage_policy"), reason
    )
    strategy["final_dynamic_leverage"] = _zero_leverage_evidence(
        strategy.get("final_dynamic_leverage"), reason
    )


def _adverse_quality_snapshot(
    strategy: Mapping[str, Any], cfg: StrategyConfig
) -> Dict[str, Any]:
    tactical = strategy.get("tactical_intraday") or {}
    if not isinstance(tactical, Mapping):
        tactical = {}

    confirmations = _as_int(tactical.get("confirmations"))
    positive_votes = _as_int(strategy.get("donchian_positive_votes"))
    volume_ratio = _as_float(tactical.get("volume_ratio"))
    breakout = _as_bool(tactical.get("breakout_above_previous_1h_high"))
    price = _as_float(tactical.get("price"))
    ema20 = _as_float(tactical.get("ema20"))
    atr14 = _as_float(tactical.get("atr14"))
    momentum_1h_pct = _as_float(tactical.get("momentum_1h_pct"))
    bar_high = _as_float(tactical.get("bar_high"), price)
    bar_low = _as_float(tactical.get("bar_low"), price)
    previous_1h_high = _as_float(tactical.get("previous_1h_high"))
    stop_percent = _as_float(strategy.get("recommended_stop_loss_percent"))

    distance_from_ema20_atr = (
        max(0.0, price - ema20) / atr14 if price > 0 and ema20 > 0 and atr14 > 0 else None
    )
    one_hour_extension_atr = (
        max(0.0, momentum_1h_pct) / 100.0 * price / atr14
        if price > 0 and atr14 > 0
        else None
    )
    completed_bar_range_atr = (
        max(0.0, bar_high - bar_low) / atr14 if atr14 > 0 else None
    )

    overhead_resistance = bool(previous_1h_high > price > 0)
    room_to_resistance_pct = (
        (previous_1h_high / price - 1.0) * 100.0 if overhead_resistance else None
    )
    reward_to_risk = (
        room_to_resistance_pct / stop_percent
        if room_to_resistance_pct is not None and stop_percent > 0
        else None
    )

    if positive_votes <= 1:
        required_confirmations = cfg.adverse_weak_required_confirmations
        required_volume_ratio = cfg.adverse_weak_min_volume_ratio
        vote_class = "weak_1of3"
        structural_checks = {
            "confirmations_passed": confirmations >= required_confirmations,
            "volume_passed": volume_ratio >= required_volume_ratio,
            "breakout_passed": breakout,
        }
    else:
        required_confirmations = cfg.adverse_aligned_required_confirmations
        required_volume_ratio = cfg.adverse_aligned_min_volume_ratio
        vote_class = "aligned_2of3_or_3of3"
        structural_checks = {
            "confirmations_passed": confirmations >= required_confirmations,
            "volume_passed": volume_ratio >= required_volume_ratio,
            "breakout_passed": True,
        }

    anti_chase_checks = {
        "distance_from_ema20_passed": (
            distance_from_ema20_atr is not None
            and distance_from_ema20_atr <= cfg.adverse_max_distance_from_ema20_atr
        ),
        "one_hour_extension_passed": (
            one_hour_extension_atr is not None
            and one_hour_extension_atr <= cfg.adverse_max_one_hour_extension_atr
        ),
        "completed_bar_range_passed": (
            completed_bar_range_atr is not None
            and completed_bar_range_atr <= cfg.adverse_max_completed_bar_range_atr
        ),
        "reward_to_risk_passed": (
            not overhead_resistance
            or (
                reward_to_risk is not None
                and reward_to_risk >= cfg.adverse_min_reward_to_risk
            )
        ),
    }

    checks = {**structural_checks, **anti_chase_checks}
    failed_checks = [name for name, passed in checks.items() if not passed]
    return {
        "policy_version": "1.0",
        "regime": "adverse",
        "vote_class": vote_class,
        "donchian_positive_votes": positive_votes,
        "confirmations": confirmations,
        "required_confirmations": required_confirmations,
        "volume_ratio": volume_ratio,
        "required_volume_ratio": required_volume_ratio,
        "breakout_above_previous_1h_high": breakout,
        "distance_from_ema20_atr": distance_from_ema20_atr,
        "maximum_distance_from_ema20_atr": cfg.adverse_max_distance_from_ema20_atr,
        "one_hour_extension_atr": one_hour_extension_atr,
        "maximum_one_hour_extension_atr": cfg.adverse_max_one_hour_extension_atr,
        "completed_bar_range_atr": completed_bar_range_atr,
        "maximum_completed_bar_range_atr": cfg.adverse_max_completed_bar_range_atr,
        "overhead_resistance": overhead_resistance,
        "previous_1h_high": previous_1h_high or None,
        "room_to_resistance_pct": room_to_resistance_pct,
        "reward_to_risk": reward_to_risk,
        "minimum_reward_to_risk": cfg.adverse_min_reward_to_risk,
        "checks": checks,
        "failed_checks": failed_checks,
        "anti_chase_passed": all(anti_chase_checks.values()),
        "passed": not failed_checks,
    }


def apply_strict_adverse_entry_policy(
    indicators: Iterable[Dict[str, Any]],
    account_status: Mapping[str, Any],
    cfg: StrategyConfig = DEFAULT_STRATEGY_CONFIG,
) -> Dict[str, Any]:
    """Remove unsafe adverse candidates and return an audit summary.

    The function only evaluates flat symbols. Open positions retain their current
    management signal and are handled by the position-management policy.
    """
    open_longs = _open_long_symbols(account_status, cfg)
    summary: Dict[str, Any] = {
        "policy_version": "1.0",
        "strategy_version": cfg.version,
        "open_correlated_long_symbols": sorted(open_longs),
        "maximum_correlated_long_positions": cfg.adverse_max_correlated_long_positions,
        "evaluated": {},
        "blocked_symbols": [],
        "allowed_symbols": [],
    }

    for item in indicators:
        if not isinstance(item, dict) or not item.get("ticker"):
            continue
        symbol = str(item.get("ticker")).upper()
        strategy = item.get("strategy") or {}
        if not isinstance(strategy, dict):
            continue
        if symbol in open_longs:
            continue
        if str(strategy.get("regime") or "") != "adverse":
            continue
        if strategy.get("recommended_action") != "tactical_long_candidate":
            continue

        feasible_raw = strategy.get("execution_feasible")
        feasible = True if feasible_raw is None else _as_bool(feasible_raw)
        if not feasible:
            continue

        original = {
            "recommended_action": strategy.get("recommended_action"),
            "recommended_effective_exposure_before_drawdown": strategy.get(
                "recommended_effective_exposure_before_drawdown"
            ),
            "execution_feasible": feasible,
        }
        quality = _adverse_quality_snapshot(strategy, cfg)
        correlated_limit_reached = bool(
            len(open_longs) >= cfg.adverse_max_correlated_long_positions
        )
        quality["correlated_position_limit_reached"] = correlated_limit_reached
        quality["open_correlated_long_symbols"] = sorted(open_longs)
        quality["original_candidate"] = original

        block_reasons = list(quality.get("failed_checks") or [])
        if correlated_limit_reached:
            block_reasons.append("maximum_correlated_adverse_long_positions_reached")
        quality["block_reasons"] = block_reasons
        quality["passed"] = not block_reasons
        strategy["adverse_entry_quality"] = quality

        if block_reasons:
            reason = "adverse_entry_blocked:" + ",".join(block_reasons)
            _block_entry(strategy, reason)
            summary["blocked_symbols"].append(symbol)
        else:
            summary["allowed_symbols"].append(symbol)

        item["strategy"] = strategy
        summary["evaluated"][symbol] = deepcopy(quality)

    return summary


def executable_candidate_symbols(
    indicators: Iterable[Dict[str, Any]],
) -> list[str]:
    result: list[str] = []
    for item in indicators:
        if not isinstance(item, dict) or not item.get("ticker"):
            continue
        strategy = item.get("strategy") or {}
        if not isinstance(strategy, Mapping):
            continue
        feasible_raw = strategy.get("execution_feasible")
        feasible = True if feasible_raw is None else _as_bool(feasible_raw)
        if strategy.get("recommended_action") in CANDIDATE_ACTIONS and feasible:
            result.append(str(item.get("ticker")).upper())
    return sorted(set(result))

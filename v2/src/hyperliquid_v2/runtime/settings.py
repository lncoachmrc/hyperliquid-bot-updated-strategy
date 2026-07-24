from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int | None = None) -> int:
    value = int(os.getenv(name, str(default)))
    return max(minimum, value) if minimum is not None else value


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    database_url: str
    wallet_address: str
    symbols: tuple[str, ...]
    hyperliquid_http_url: str
    hyperliquid_ws_url: str
    feature_interval_seconds: int
    position_review_seconds: int
    entry_review_seconds: int
    entry_decision_cooldown_seconds: int
    default_stop_pct: float
    round_trip_cost_bps: float
    shadow_only: bool
    live_trading_enabled: bool
    primary_provider: str
    primary_model: str
    challenger_provider: str | None
    challenger_model: str | None
    observer_provider: str | None
    observer_model: str | None
    supervisor_provider: str
    supervisor_model: str
    supervisor_token: str | None
    github_token: str | None
    github_repository: str
    github_base_branch: str
    quant_minimum_samples: int
    max_risk_fraction: float
    max_effective_exposure: float
    failed_breakout_enabled: bool
    failed_breakout_scan_seconds: int
    failed_breakout_replay_enabled: bool
    failed_breakout_risk_fraction: float
    failed_breakout_max_effective_exposure: float

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = (os.getenv("DATABASE_URL") or os.getenv("V2_DATABASE_URL") or "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL or V2_DATABASE_URL is required")
        wallet = (os.getenv("WALLET_ADDRESS") or os.getenv("V2_WALLET_ADDRESS") or "").strip()
        if not wallet.startswith("0x") or len(wallet) != 42:
            raise RuntimeError("V2_WALLET_ADDRESS/WALLET_ADDRESS must be a 42-character address")
        symbols = tuple(
            item.strip().upper()
            for item in os.getenv("V2_SYMBOLS", "BTC,ETH,SOL").split(",")
            if item.strip()
        )
        shadow_only = _bool("V2_SHADOW_ONLY", True)
        live = _bool("V2_LIVE_TRADING_ENABLED", False)
        if not shadow_only or live:
            raise RuntimeError(
                "V2 operational release is shadow-only. Keep V2_SHADOW_ONLY=true and "
                "V2_LIVE_TRADING_ENABLED=false."
            )
        challenger_provider = (os.getenv("V2_CHALLENGER_PROVIDER") or "").strip().lower() or None
        challenger_model = (os.getenv("V2_CHALLENGER_MODEL") or "").strip() or None
        return cls(
            database_url=database_url,
            wallet_address=wallet.lower(),
            symbols=symbols,
            hyperliquid_http_url=os.getenv("V2_HL_HTTP_URL", "https://api.hyperliquid.xyz").rstrip("/"),
            hyperliquid_ws_url=os.getenv("V2_HL_WS_URL", "wss://api.hyperliquid.xyz/ws"),
            feature_interval_seconds=_int("V2_FEATURE_INTERVAL_SECONDS", 15, 5),
            position_review_seconds=_int("V2_POSITION_REVIEW_SECONDS", 60, 15),
            entry_review_seconds=_int("V2_ENTRY_REVIEW_SECONDS", 300, 60),
            entry_decision_cooldown_seconds=_int(
                "V2_ENTRY_DECISION_COOLDOWN_SECONDS",
                60,
                15,
            ),
            default_stop_pct=_float("V2_DEFAULT_STOP_PCT", 0.60),
            round_trip_cost_bps=_float("V2_ROUND_TRIP_COST_BPS", 10.0),
            shadow_only=shadow_only,
            live_trading_enabled=live,
            primary_provider=os.getenv("V2_PRIMARY_PROVIDER", "openai").strip().lower(),
            primary_model=os.getenv("V2_PRIMARY_MODEL", "gpt-5").strip(),
            challenger_provider=challenger_provider,
            challenger_model=challenger_model,
            observer_provider=(os.getenv("V2_OBSERVER_PROVIDER") or "").strip().lower() or None,
            observer_model=(os.getenv("V2_OBSERVER_MODEL") or "").strip() or None,
            supervisor_provider=os.getenv("V2_SUPERVISOR_PROVIDER", "openai").strip().lower(),
            supervisor_model=os.getenv("V2_SUPERVISOR_MODEL", os.getenv("V2_PRIMARY_MODEL", "gpt-5")).strip(),
            supervisor_token=(os.getenv("V2_SUPERVISOR_TOKEN") or "").strip() or None,
            github_token=(os.getenv("V2_GITHUB_TOKEN") or "").strip() or None,
            github_repository=os.getenv("V2_GITHUB_REPOSITORY", "lncoachmrc/hyperliquid-bot-updated-strategy").strip(),
            github_base_branch=os.getenv("V2_GITHUB_BASE_BRANCH", "main").strip(),
            quant_minimum_samples=_int("V2_QUANT_MINIMUM_SAMPLES", 50, 30),
            max_risk_fraction=_float("V2_MAX_RISK_FRACTION", 0.005),
            max_effective_exposure=_float("V2_MAX_EFFECTIVE_EXPOSURE", 0.50),
            failed_breakout_enabled=_bool("V2_FAILED_BREAKOUT_ENABLED", True),
            failed_breakout_scan_seconds=_int(
                "V2_FAILED_BREAKOUT_SCAN_SECONDS",
                15,
                5,
            ),
            failed_breakout_replay_enabled=_bool(
                "V2_FAILED_BREAKOUT_REPLAY_ENABLED",
                True,
            ),
            failed_breakout_risk_fraction=_float(
                "V2_FAILED_BREAKOUT_RISK_FRACTION",
                0.0015,
            ),
            failed_breakout_max_effective_exposure=_float(
                "V2_FAILED_BREAKOUT_MAX_EFFECTIVE_EXPOSURE",
                0.20,
            ),
        )

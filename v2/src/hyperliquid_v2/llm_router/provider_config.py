from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    api_key_env: str
    base_url: str
    enabled: bool
    task_roles: tuple[str, ...]


def load_provider_configs(path: str | Path) -> tuple[ProviderConfig, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    result: list[ProviderConfig] = []
    for item in payload["providers"]:
        model = os.getenv(item["model_env"], item.get("fallback_model", "")).strip()
        result.append(
            ProviderConfig(
                provider=item["provider"],
                model=model,
                api_key_env=item["api_key_env"],
                base_url=item["base_url"],
                enabled=bool(item.get("enabled", True) and model),
                task_roles=tuple(item.get("task_roles", [])),
            )
        )
    return tuple(result)

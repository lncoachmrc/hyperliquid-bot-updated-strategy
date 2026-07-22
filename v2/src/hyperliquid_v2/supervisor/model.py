from __future__ import annotations

import json
import os
from typing import Any

import httpx

from hyperliquid_v2.supervisor.policy import OptimizationProposal


class SupervisorProposalModel:
    """Ask one configured model for one falsifiable, bounded policy change."""

    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider.lower()
        self.model = model
        variable = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
        }.get(self.provider)
        self.api_key = (os.getenv(variable or "") or "").strip() or None
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10))

    async def close(self) -> None:
        await self.client.aclose()

    async def propose(self, evidence: dict[str, Any]) -> tuple[OptimizationProposal | None, dict[str, Any]]:
        if not self.api_key:
            return None, {"status": "no_provider_key", "provider": self.provider, "model": self.model}
        prompt = _prompt(evidence)
        if self.provider == "openai":
            raw = await self._openai(prompt)
        elif self.provider == "anthropic":
            raw = await self._anthropic(prompt)
        elif self.provider == "deepseek":
            raw = await self._deepseek(prompt)
        else:
            return None, {"status": "unsupported_provider", "provider": self.provider}
        if raw.get("outcome") != "PROPOSE_PR":
            return None, raw
        proposal = OptimizationProposal(
            hypothesis=str(raw["hypothesis"]),
            changed_files=("v2/config/experimental_policy.json",),
            comparable_samples=int(raw["comparable_samples"]),
            out_of_sample_samples=int(raw["out_of_sample_samples"]),
            expected_net_improvement_r=float(raw["expected_net_improvement_r"]),
            drawdown_delta_pct=float(raw["drawdown_delta_pct"]),
            depends_on_single_trade=bool(raw["depends_on_single_trade"]),
            affected_symbols=tuple(str(item).upper() for item in raw["affected_symbols"]),
            tests=tuple(str(item) for item in raw["tests"]),
        )
        return proposal, raw

    async def _openai(self, prompt: str) -> dict[str, Any]:
        response = await self.client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "store": False,
                "input": prompt,
                "text": {"format": {"type": "json_schema", "name": "v2_supervisor_proposal", "strict": True, "schema": _schema()}},
            },
        )
        response.raise_for_status()
        data = response.json()
        text = data.get("output_text")
        if not text:
            text = "".join(
                content.get("text", "")
                for item in data.get("output") or []
                for content in item.get("content") or []
                if content.get("type") in {"output_text", "text"}
            )
        return json.loads(text)

    async def _anthropic(self, prompt: str) -> dict[str, Any]:
        response = await self.client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": self.model,
                "max_tokens": 1800,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [{"name": "submit_supervisor_proposal", "description": "Return one bounded V2 optimization proposal or NO_CHANGE.", "input_schema": _schema()}],
                "tool_choice": {"type": "tool", "name": "submit_supervisor_proposal"},
            },
        )
        response.raise_for_status()
        for item in response.json().get("content") or []:
            if item.get("type") == "tool_use" and item.get("name") == "submit_supervisor_proposal":
                return item.get("input") or {}
        raise ValueError("Anthropic supervisor response missing forced tool output")

    async def _deepseek(self, prompt: str) -> dict[str, Any]:
        response = await self.client.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Return JSON only. Never authorize merge, deploy, live trading, risk-cap changes or execution changes."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 1800,
            },
        )
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])


def _prompt(evidence: dict[str, Any]) -> str:
    return (
        "You supervise a shadow-only Hyperliquid V2. Analyze the supplied database metrics. "
        "Return NO_CHANGE unless one causal parameter change is supported by adequate comparable and out-of-sample data, positive net expectancy, cross-symbol evidence, and no material drawdown deterioration. "
        "The only editable file is v2/config/experimental_policy.json. Include a replacement_policy object containing the complete valid JSON for that file. "
        "Never alter risk invariants, execution code, secrets, live trading, merge or deploy permissions.\nEVIDENCE:\n"
        + json.dumps(evidence, separators=(",", ":"), default=str)
    )


def _schema() -> dict[str, Any]:
    nullable_number = {"type": ["number", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "outcome", "hypothesis", "comparable_samples", "out_of_sample_samples",
            "expected_net_improvement_r", "drawdown_delta_pct", "depends_on_single_trade",
            "affected_symbols", "tests", "replacement_policy", "reasoning_summary"
        ],
        "properties": {
            "outcome": {"type": "string", "enum": ["NO_CHANGE", "PROPOSE_PR"]},
            "hypothesis": {"type": ["string", "null"]},
            "comparable_samples": {"type": "integer", "minimum": 0},
            "out_of_sample_samples": {"type": "integer", "minimum": 0},
            "expected_net_improvement_r": nullable_number,
            "drawdown_delta_pct": nullable_number,
            "depends_on_single_trade": {"type": "boolean"},
            "affected_symbols": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array", "items": {"type": "string"}},
            "replacement_policy": {"type": ["object", "null"]},
            "reasoning_summary": {"type": "string"},
        },
    }

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Protocol

import httpx

from hyperliquid_v2.domain.models import (
    DecisionAction,
    DecisionPacket,
    ModelDecision,
)


LOGGER = logging.getLogger(__name__)


DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "confidence",
        "expected_value_hold_r",
        "expected_value_close_r",
        "thesis_status",
        "main_reason",
        "evidence_used",
        "partial_fraction",
        "selected_leverage",
        "selected_effective_exposure",
        "selected_balance_portion",
        "selected_stop_distance_pct",
    ],
    "properties": {
        "action": {
            "type": "string",
            "enum": [action.value for action in DecisionAction],
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "expected_value_hold_r": {
            "type": ["number", "null"],
        },
        "expected_value_close_r": {
            "type": ["number", "null"],
        },
        "thesis_status": {"type": "string"},
        "main_reason": {"type": "string"},
        "evidence_used": {
            "type": "array",
            "items": {"type": "string"},
        },
        "partial_fraction": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 1,
        },
        "selected_leverage": {
            "type": ["integer", "null"],
            "minimum": 1,
        },
        "selected_effective_exposure": {
            "type": ["number", "null"],
            "minimum": 0,
        },
        "selected_balance_portion": {
            "type": ["number", "null"],
            "minimum": 0,
        },
        "selected_stop_distance_pct": {
            "type": ["number", "null"],
            "minimum": 0,
        },
    },
}


class AsyncDecisionProvider(Protocol):
    name: str
    model: str

    async def decide(
        self,
        packet: DecisionPacket,
    ) -> ModelDecision: ...


class ProviderUnavailable(RuntimeError):
    pass


class HttpDecisionProvider:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.name = provider.lower()
        self.model = model
        self.api_key = api_key or _api_key_for(self.name)
        if not self.api_key:
            raise ProviderUnavailable(
                f"missing API key for {self.name}"
            )
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=10.0,
            )
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def decide(
        self,
        packet: DecisionPacket,
    ) -> ModelDecision:
        started = time.perf_counter()
        if self.name == "openai":
            payload = await self._openai(packet)
        elif self.name == "anthropic":
            payload = await self._anthropic(packet)
        elif self.name == "deepseek":
            payload = await self._deepseek(packet)
        else:
            raise ProviderUnavailable(
                f"unsupported provider: {self.name}"
            )
        latency_ms = int(
            (time.perf_counter() - started) * 1000
        )
        return _decision_from_payload(
            self.name,
            self.model,
            payload,
            latency_ms,
        )

    async def _openai(
        self,
        packet: DecisionPacket,
    ) -> dict[str, Any]:
        response = await self.client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "store": False,
                "input": [
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": _system_prompt(),
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": json.dumps(
                                    packet.to_dict(),
                                    separators=(",", ":"),
                                ),
                            }
                        ],
                    },
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "trading_decision",
                        "strict": True,
                        "schema": DECISION_JSON_SCHEMA,
                    }
                },
            },
        )
        response.raise_for_status()
        data = response.json()
        text = data.get("output_text") or _openai_output_text(data)
        return json.loads(text)

    async def _anthropic(
        self,
        packet: DecisionPacket,
    ) -> dict[str, Any]:
        response = await self.client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1200,
                "system": _system_prompt(),
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(
                            packet.to_dict(),
                            separators=(",", ":"),
                        ),
                    }
                ],
                "tools": [
                    {
                        "name": "submit_trading_decision",
                        "description": (
                            "Return the final structured shadow "
                            "trading decision."
                        ),
                        "input_schema": DECISION_JSON_SCHEMA,
                    }
                ],
                "tool_choice": {
                    "type": "tool",
                    "name": "submit_trading_decision",
                },
            },
        )
        response.raise_for_status()
        for item in response.json().get("content") or []:
            if (
                item.get("type") == "tool_use"
                and item.get("name")
                == "submit_trading_decision"
            ):
                return item.get("input") or {}
        raise ValueError(
            "Anthropic response did not contain the forced decision tool"
        )

    async def _deepseek(
        self,
        packet: DecisionPacket,
    ) -> dict[str, Any]:
        schema_text = json.dumps(
            DECISION_JSON_SCHEMA,
            separators=(",", ":"),
        )
        messages = [
            {
                "role": "system",
                "content": (
                    _system_prompt()
                    + " Output JSON only. Return every required key "
                    "from this JSON Schema exactly once and do not "
                    "add any other key: "
                    + schema_text
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    packet.to_dict(),
                    separators=(",", ":"),
                ),
            },
        ]
        payload, content = await self._deepseek_request(messages)
        missing = _missing_required_keys(payload)
        if not missing:
            return payload

        LOGGER.warning(
            "DeepSeek response missing required decision fields; "
            "retrying once fields=%s",
            ",".join(missing),
        )
        repair_messages = [
            *messages,
            {
                "role": "assistant",
                "content": content,
            },
            {
                "role": "user",
                "content": (
                    "Your previous JSON omitted these required keys: "
                    + ", ".join(missing)
                    + ". Return one corrected JSON object only. "
                    "Preserve supported values from the prior answer, "
                    "do not invent market data, and include every "
                    "required key from the schema. Use null only where "
                    "the schema permits null."
                ),
            },
        ]
        repaired, _ = await self._deepseek_request(repair_messages)
        still_missing = _missing_required_keys(repaired)
        if still_missing:
            raise ValueError(
                "DeepSeek response missing required decision fields "
                "after one repair attempt: "
                + ", ".join(still_missing)
            )
        return repaired

    async def _deepseek_request(
        self,
        messages: list[dict[str, str]],
    ) -> tuple[dict[str, Any], str]:
        response = await self.client.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": messages,
                "response_format": {
                    "type": "json_object",
                },
                "temperature": 0,
                "max_tokens": 1200,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError(
                "DeepSeek response must be a JSON object"
            )
        return payload, content


class DeterministicShadowProvider:
    """Safe operational fallback when no provider key is configured."""

    name = "deterministic_shadow"
    model = "v2-economic-fallback"

    async def decide(
        self,
        packet: DecisionPacket,
    ) -> ModelDecision:
        if packet.position_state is not None:
            phase = str(packet.position_state.phase)
            current_r = packet.position_state.current_r
            reversal = float(
                packet.pump_momentum.get(
                    "reversal_probability"
                )
                or 0
            )
            continuation = float(
                packet.pump_momentum.get(
                    "continuation_probability"
                )
                or 0
            )
            close = phase in {
                "EXHAUSTION",
                "THESIS_INVALIDATED",
                "PROFIT_LOCKED",
            } or (
                current_r > 0
                and reversal > continuation + 0.10
            )
            action = (
                DecisionAction.CLOSE
                if close
                and DecisionAction.CLOSE
                in packet.allowed_actions
                else DecisionAction.HOLD
            )
            reason = (
                "deterministic_exit_review"
                if close
                else "deterministic_hold"
            )
            ev_close = current_r
            ev_hold = (
                continuation * 0.35
                - reversal
                * max(
                    0.25,
                    packet.position_state.mfe_r
                    - current_r,
                )
            )
        else:
            evidence = packet.quant_evidence
            supported = bool(
                evidence
                and evidence.operational
                and evidence.expected_net_value_r is not None
                and evidence.expected_net_value_r > 0
                and (
                    evidence.confidence_interval_r
                    or (0, 0)
                )[0]
                >= 0
            )
            action = (
                DecisionAction.OPEN
                if supported
                and DecisionAction.OPEN
                in packet.allowed_actions
                else DecisionAction.NO_TRADE
            )
            reason = (
                "quant_evidence_positive"
                if supported
                else "insufficient_operational_evidence"
            )
            ev_hold = (
                evidence.expected_net_value_r
                if evidence
                else None
            )
            ev_close = 0.0
        leverage = (
            min(packet.risk_envelope.allowed_leverage)
            if action is DecisionAction.OPEN
            else None
        )
        exposure = (
            min(
                packet.risk_envelope.maximum_effective_exposure,
                0.25,
            )
            if action is DecisionAction.OPEN
            else None
        )
        return ModelDecision(
            provider=self.name,
            model=self.model,
            action=action,
            confidence=0.55,
            expected_value_hold_r=ev_hold,
            expected_value_close_r=ev_close,
            thesis_status=(
                "valid"
                if action
                not in {
                    DecisionAction.CLOSE,
                    DecisionAction.NO_TRADE,
                }
                else "review"
            ),
            main_reason=reason,
            evidence_used=(
                "risk_envelope",
                "pump_momentum",
                "quant_evidence",
            ),
            partial_fraction=None,
            selected_leverage=leverage,
            selected_effective_exposure=exposure,
            selected_balance_portion=(
                exposure / leverage
                if exposure is not None
                and leverage is not None
                else None
            ),
            selected_stop_distance_pct=(
                (
                    packet.risk_envelope.minimum_stop_distance_pct
                    + packet.risk_envelope.maximum_stop_distance_pct
                )
                / 2
                if action is DecisionAction.OPEN
                else None
            ),
            raw={"fallback": True},
        )


def build_provider(
    provider: str,
    model: str,
) -> AsyncDecisionProvider:
    try:
        return HttpDecisionProvider(provider, model)
    except ProviderUnavailable:
        return DeterministicShadowProvider()


def _system_prompt() -> str:
    return (
        "You are the decision model for a SHADOW-ONLY "
        "Hyperliquid research system. The packet is immutable "
        "and already bounded by a deterministic risk envelope. "
        "Choose only an allowed action. Do not invent data. "
        "For entries, prefer NO_TRADE when evidence is weak. "
        "For open positions, compare the expected value of "
        "holding with closing, protect meaningful profit from "
        "green-to-red giveback, and distinguish continuation "
        "from exhaustion. For OPEN, choose leverage, effective "
        "exposure, balance portion and stop distance strictly "
        "inside the risk envelope. No order will be sent."
    )


def _openai_output_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            if (
                content.get("type")
                in {"output_text", "text"}
                and content.get("text")
            ):
                parts.append(content["text"])
    if not parts:
        raise ValueError(
            "OpenAI response has no output text"
        )
    return "".join(parts)


def _decision_from_payload(
    provider: str,
    model: str,
    payload: dict[str, Any],
    latency_ms: int,
) -> ModelDecision:
    action = DecisionAction(str(payload["action"]))
    return ModelDecision(
        provider=provider,
        model=model,
        action=action,
        confidence=float(payload["confidence"]),
        expected_value_hold_r=_optional_float(
            payload.get("expected_value_hold_r")
        ),
        expected_value_close_r=_optional_float(
            payload.get("expected_value_close_r")
        ),
        thesis_status=str(
            payload.get("thesis_status") or "unknown"
        ),
        main_reason=str(
            payload.get("main_reason") or "unspecified"
        ),
        evidence_used=tuple(
            str(item)
            for item in payload.get("evidence_used") or []
        ),
        partial_fraction=_optional_float(
            payload.get("partial_fraction")
        ),
        selected_leverage=(
            int(payload["selected_leverage"])
            if payload.get("selected_leverage")
            is not None
            else None
        ),
        selected_effective_exposure=_optional_float(
            payload.get("selected_effective_exposure")
        ),
        selected_balance_portion=_optional_float(
            payload.get("selected_balance_portion")
        ),
        selected_stop_distance_pct=_optional_float(
            payload.get("selected_stop_distance_pct")
        ),
        latency_ms=latency_ms,
        raw=payload,
    )


def _missing_required_keys(
    payload: dict[str, Any],
) -> tuple[str, ...]:
    required = DECISION_JSON_SCHEMA["required"]
    return tuple(
        str(key)
        for key in required
        if key not in payload
    )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _api_key_for(provider: str) -> str | None:
    variable = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }.get(provider)
    return (
        (os.getenv(variable) or "").strip() or None
        if variable
        else None
    )

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-5.6-terra").strip()

if not OPENAI_MODEL:
    raise RuntimeError("OPENAI_MODEL non può essere vuoto")

client = OpenAI(api_key=OPENAI_API_KEY)


def trade_operation_schema() -> dict:
    """Existing output contract with strategy-specific prudential bounds."""
    return {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "Type of trading operation to perform",
                "enum": ["open", "close", "hold"],
            },
            "symbol": {
                "type": "string",
                "description": "The cryptocurrency symbol to act on",
                "enum": ["BTC", "ETH", "SOL"],
            },
            "direction": {
                "type": "string",
                "description": (
                    "Trade direction. The active strategy permits no new short "
                    "positions; short is retained only for compatibility when "
                    "closing an already existing short."
                ),
                "enum": ["long", "short"],
            },
            "target_portion_of_balance": {
                "type": "number",
                "description": (
                    "Collateral fraction of available balance. Effective exposure "
                    "equals this fraction multiplied by leverage and must not exceed "
                    "the execution_feasibility limit."
                ),
                "minimum": 0,
                "maximum": 1,
            },
            "leverage": {
                "type": "integer",
                "description": (
                    "Integer exchange leverage. Use the final dynamic leverage tier; "
                    "higher leverage must reduce collateral portion proportionally "
                    "and must never increase approved effective exposure."
                ),
                "minimum": 1,
                "maximum": 10,
            },
            "stop_loss_percent": {
                "type": "number",
                "description": (
                    "Stop distance percentage. Use no wider stop than the ATR-derived "
                    "value supplied by the strategy snapshot."
                ),
                "minimum": 0.5,
                "maximum": 25,
            },
            "reason": {
                "type": "string",
                "description": "Brief explanation tied to the supplied strategy evidence",
                "minLength": 1,
                "maxLength": 300,
            },
        },
        "required": [
            "operation",
            "symbol",
            "direction",
            "target_portion_of_balance",
            "leverage",
            "reason",
            "stop_loss_percent",
        ],
        "additionalProperties": False,
    }


def previsione_trading_agent(prompt):
    print(f"[llm] OpenAI model={OPENAI_MODEL}")
    response = client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "trade_operation",
                "strict": True,
                "schema": trade_operation_schema(),
            },
            "verbosity": "medium",
        },
        reasoning={"effort": "medium", "summary": "auto"},
        tools=[],
        store=True,
        include=[
            "reasoning.encrypted_content",
            "web_search_call.action.sources",
        ],
    )
    return json.loads(response.output_text)

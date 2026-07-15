from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_entry_point_keeps_llm_then_execution_order():
    main = source("main.py")
    decision = main.index("out = previsione_trading_agent(system_prompt)")
    execution = main.index("bot.execute_signal(out)")
    assert decision < execution
    assert "HyperLiquidTrader(" in main
    assert "analyze_multiple_tickers" in main


def test_strategy_layer_cannot_place_orders():
    strategy = source("strategy_core.py")
    assert "hyperliquid.exchange" not in strategy
    assert ".market_open(" not in strategy
    assert ".order(" not in strategy
    assert "OpenAI" not in strategy


def test_llm_function_and_provider_call_remain_in_same_module():
    agent = source("trading_agent.py")
    tree = ast.parse(agent)
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "previsione_trading_agent" in functions
    assert "client.responses.create" in agent
    assert 'model="gpt-5.1"' in agent


def test_execution_public_interface_is_preserved():
    trader = source("hyperliquid_trader.py")
    tree = ast.parse(trader)
    klass = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "HyperLiquidTrader"
    )
    methods = {
        node.name for node in klass.body if isinstance(node, ast.FunctionDef)
    }
    assert {
        "execute_signal",
        "get_account_status",
        "set_leverage_for_symbol",
        "get_current_leverage",
        "debug_symbol_limits",
    }.issubset(methods)


def test_output_contract_keeps_existing_fields_and_caps_leverage():
    agent = source("trading_agent.py")
    for field in (
        "operation",
        "symbol",
        "direction",
        "target_portion_of_balance",
        "leverage",
        "stop_loss_percent",
        "reason",
    ):
        assert f'"{field}"' in agent
    assert '"maximum": 2' in agent


def test_prompt_keeps_llm_authority_and_no_parallel_decider():
    prompt = source("system_prompt.txt")
    assert "Your authority is unchanged" in prompt
    assert "you still choose one operation" in prompt
    assert "Do not open a new short position" in prompt

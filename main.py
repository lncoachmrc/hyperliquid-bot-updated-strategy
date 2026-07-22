from indicators import analyze_multiple_tickers
from news_feed import fetch_latest_news
from trading_agent import previsione_trading_agent
from utils import check_stop_loss
from sentiment import get_sentiment
from forecaster import get_crypto_forecasts
from hyperliquid_trader import HyperLiquidTrader
from runtime_config import env_bool
from candidate_upgrade import annotate_candidate_quality_upgrades
from decision_gate import deterministic_hold, should_invoke_llm
from decision_guard import apply_decision_guard
from entry_quality_policy import apply_strict_adverse_entry_policy
from profit_protection_overlay import apply_adverse_profit_protection
from prophet_shadow import attach_prophet_shadow_evaluations
from dashboard_forecast_cache import resolve_dashboard_forecasts
from shadow_candidate_selection import flat_account_shadow_candidates
from performance_observability import (
    ensure_performance_observability_schema,
    link_entry_opportunity_samples,
    reconcile_pending_external_closures,
    record_and_observe_entry_opportunities,
)
from execution_policy import (
    annotate_execution_feasibility,
    compact_execution_feasibility,
    enrich_constraints_with_live_leverage,
)
from position_management import (
    build_position_management_state,
    load_management_history,
)
from execution_audit import (
    attach_post_snapshot,
    ensure_execution_audit_schema,
    log_execution_result,
    normalize_execution_exception,
    normalize_execution_result,
)
import os
import json
import string
import db_utils
from dotenv import load_dotenv

load_dotenv()


def normalize_private_key(raw_key):
    """Validate a 32-byte EVM private key without ever logging its value."""
    if not raw_key:
        raise RuntimeError("PRIVATE_KEY mancante nelle variabili d'ambiente")

    value = raw_key.strip()
    if value.startswith(("0x", "0X")):
        value = value[2:]

    if len(value) != 64 or any(char not in string.hexdigits for char in value):
        raise RuntimeError(
            "PRIVATE_KEY non valida: deve contenere esattamente 64 caratteri "
            "esadecimali (prefisso 0x opzionale), senza virgolette o spazi"
        )

    return "0x" + value.lower()


# Railway/runtime controls the Hyperliquid environment. Testnet remains the
# safe default when TESTNET is missing.
TESTNET = env_bool("TESTNET", True)
VERBOSE = True
PRIVATE_KEY = normalize_private_key(os.getenv("PRIVATE_KEY"))
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS") or "").strip()

if not WALLET_ADDRESS:
    raise RuntimeError("WALLET_ADDRESS mancante nelle variabili d'ambiente")

try:
    network_name = "TESTNET" if TESTNET else "MAINNET"
    masked_account = (
        f"{WALLET_ADDRESS[:6]}...{WALLET_ADDRESS[-4:]}"
        if len(WALLET_ADDRESS) > 12
        else "configured"
    )
    print(f"[runtime] Hyperliquid network={network_name}, account={masked_account}")

    bot = HyperLiquidTrader(
        secret_key=PRIVATE_KEY,
        account_address=WALLET_ADDRESS,
        testnet=TESTNET,
    )

    # Additive schema used only for execution auditing. If it cannot be created,
    # the cycle fails before any live order can be sent.
    ensure_execution_audit_schema()

    # Performance observability is deliberately non-operational. A schema or
    # reconciliation failure must never block position management or a live order.
    performance_observability_schema_ready = False
    performance_observability_summary = {
        "mode": "audit_only",
        "operational": False,
        "schema_ready": False,
        "entry_opportunities": {},
        "external_closes": {},
    }
    try:
        ensure_performance_observability_schema()
        performance_observability_schema_ready = True
        performance_observability_summary["schema_ready"] = True
    except Exception as observability_schema_error:  # noqa: BLE001
        performance_observability_summary["schema_error"] = str(
            observability_schema_error
        )
        print(
            "[performance_observability] Schema non disponibile; "
            "il trading continua senza metriche aggiuntive: "
            f"{observability_schema_error}"
        )

    tickers = ["BTC", "ETH", "SOL"]
    indicators_txt, indicators_json = analyze_multiple_tickers(
        tickers,
        testnet=TESTNET,
    )

    account_status = bot.get_account_status()

    # Drawdown is calculated before the current snapshot is inserted, so the
    # historical peak is not distorted by the observation being evaluated.
    drawdown_state = db_utils.get_account_drawdown_state(
        current_balance=account_status["balance_usd"]
    )
    drawdown_factor_for_execution = drawdown_state.get("drawdown_factor")
    if drawdown_factor_for_execution is None:
        # Fail closed for new entries when drawdown cannot be verified. Existing
        # positions remain manageable through the position policy and LLM.
        drawdown_factor_for_execution = 0.0

    # Evaluate exchange minimums and current asset maxLeverage before the LLM is
    # called. Dynamic leverage changes collateral representation only; final
    # economic exposure remains bounded by stop risk, drawdown and strategy caps.
    execution_constraints = bot.get_execution_constraints(tickers)
    enrich_constraints_with_live_leverage(execution_constraints, bot.meta)
    annotate_execution_feasibility(
        indicators_json,
        execution_constraints,
        portfolio_drawdown_factor=drawdown_factor_for_execution,
    )

    # Live adverse-regime selection is deliberately stricter than the generic
    # 15m candidate: countertrend quality, anti-chase and the one-correlated-long
    # limit may only REMOVE entry eligibility. Risk and leverage are unchanged.
    entry_quality_summary = apply_strict_adverse_entry_policy(
        indicators_json,
        account_status,
    )
    execution_feasibility = compact_execution_feasibility(indicators_json)
    account_status["execution_constraints"] = execution_constraints
    account_status["entry_quality_policy"] = entry_quality_summary

    # Stop management must never wait for optional forecasting or audit work.
    stop_losses = check_stop_loss(account_status)

    # Record one counterfactual sample per symbol/completed 15m bar and reconcile
    # externally triggered closes. These results are intentionally NOT added to
    # account_status or the LLM prompt.
    if performance_observability_schema_ready:
        try:
            performance_observability_summary["entry_opportunities"] = (
                record_and_observe_entry_opportunities(
                    indicators_json,
                    entry_quality_summary,
                )
            )
        except Exception as entry_observation_error:  # noqa: BLE001
            performance_observability_summary["entry_opportunities"] = {
                "error": str(entry_observation_error)
            }
            print(
                "[performance_observability] Errore entry samples; "
                f"trading invariato: {entry_observation_error}"
            )

        performance_observability_summary["external_closes"] = (
            reconcile_pending_external_closures(
                bot,
                stop_losses,
            )
        )

    # Prophet is collected only when the account is completely flat and at least
    # one post-filter entry candidate is executable. Its values are persisted but
    # excluded from the LLM prompt and cannot change operation/risk/leverage.
    candidate_symbols = flat_account_shadow_candidates(
        indicators_json,
        account_status,
    )
    forecasts_txt = ""
    forecasts_json = None
    prophet_shadow_summary = {
        "mode": "shadow",
        "operational": False,
        "observation_count": 0,
        "observations": {},
    }
    if candidate_symbols:
        forecasts_txt, forecasts_json = get_crypto_forecasts(
            candidate_symbols,
            testnet=TESTNET,
        )
        prophet_shadow_summary = attach_prophet_shadow_evaluations(
            indicators_json,
            forecasts_json,
        )
    account_status["prophet_shadow_mode"] = {
        "operational": False,
        "flat_account_required": True,
        "candidate_symbols": candidate_symbols,
        "observation_count": prophet_shadow_summary.get("observation_count", 0),
    }

    # The live decision still uses only the current shadow sample above. For the
    # read-only dashboard, fill any missing cards with the latest stored 15m/1h
    # forecasts so a non-Prophet cycle cannot blank the entire forecast section.
    dashboard_forecasts_json, dashboard_forecast_source = (
        resolve_dashboard_forecasts(forecasts_json)
    )
    account_status["prophet_shadow_mode"]["dashboard_forecast_source"] = (
        dashboard_forecast_source
    )
    account_status["prophet_shadow_mode"]["dashboard_forecast_count"] = len(
        dashboard_forecasts_json
    )

    pre_snapshot_id = db_utils.log_account_status(account_status)
    print(f"[db_utils] Account snapshot pre-esecuzione id={pre_snapshot_id}")

    open_symbols = [
        position.get("symbol")
        for position in (account_status.get("open_positions") or [])
        if position.get("symbol")
    ]
    management_history = load_management_history(tickers, open_symbols)
    management_state = build_position_management_state(
        indicators_json,
        account_status,
        management_history,
    )
    management_state = apply_adverse_profit_protection(management_state)
    # Preserve the existing immediate-review behavior for materially improving
    # persistent candidates, now evaluated only after strict adverse filtering.
    annotate_candidate_quality_upgrades(
        indicators_json,
        account_status,
        management_history,
        management_state,
    )

    invoke_llm, gate_reason = should_invoke_llm(
        indicators_json,
        account_status,
        stop_losses,
        management_state,
    )
    print(f"[decision_gate] invoke_llm={invoke_llm}, reason={gate_reason}")

    news_txt = ""
    sentiment_txt = ""
    sentiment_json = None
    system_prompt = None

    if invoke_llm:
        news_txt = fetch_latest_news()
        sentiment_txt, sentiment_json = get_sentiment()

        msg_info = f"""<indicatori>\n{indicators_txt}\n</indicatori>\n\n
        <execution_feasibility>\n{json.dumps(execution_feasibility)}\n</execution_feasibility>\n\n
        <entry_quality_policy>\n{json.dumps(entry_quality_summary)}\n</entry_quality_policy>\n\n
        <news>\n{news_txt}</news>\n\n
        <sentiment>\n{sentiment_txt}</sentiment>\n\n
        <prophet_mode>SHADOW ONLY: forecast values are intentionally excluded from the live decision.</prophet_mode>\n\n"""

        portfolio_data = (
            f"{json.dumps(account_status)}\n"
            f"Portfolio drawdown state: {json.dumps(drawdown_state)}\n"
            f"Position management policy: {json.dumps(management_state)}\n"
            f"Stop Loss attivati 15 min fa: {stop_losses}"
        )

        with open("system_prompt.txt", "r", encoding="utf-8") as prompt_file:
            system_prompt = prompt_file.read()
        system_prompt = system_prompt.format(portfolio_data, msg_info)

        print("L'agente sta decidendo la sua azione!")
        llm_out = previsione_trading_agent(system_prompt)
        out = apply_decision_guard(
            llm_out,
            account_status,
            indicators_json,
            management_state,
        )
        out["decision_source"] = "llm"
        out["decision_gate_reason"] = gate_reason
    else:
        out = deterministic_hold(
            gate_reason,
            management_state=management_state,
        )
        out["decision_source"] = "deterministic_prefilter"
        out["decision_gate_reason"] = gate_reason
        print(
            "[decision_gate] LLM non chiamato: nessun evento azionabile o "
            "revisione posizione ancora dovuta. HOLD deterministico."
        )

    out["position_management"] = management_state
    out["entry_quality_policy"] = entry_quality_summary
    out["prophet_shadow"] = prophet_shadow_summary
    out["dashboard_forecasts"] = {
        "source": dashboard_forecast_source,
        "count": len(dashboard_forecasts_json),
    }
    out["performance_observability"] = performance_observability_summary

    # Persist the final executable decision BEFORE touching the exchange. Any LLM
    # decision adjusted by the safety guard retains the original in raw_payload.
    op_id = db_utils.log_bot_operation(
        out,
        system_prompt=system_prompt,
        indicators=indicators_json,
        news_text=news_txt,
        sentiment=sentiment_json,
        forecasts=dashboard_forecasts_json,
    )

    entry_observation_state = performance_observability_summary.get(
        "entry_opportunities"
    ) or {}
    sample_keys = entry_observation_state.get("sample_keys") or []
    if performance_observability_schema_ready and sample_keys:
        try:
            linked_samples = link_entry_opportunity_samples(
                sample_keys,
                bot_operation_id=op_id,
                decision=out,
            )
            entry_observation_state["linked_samples"] = linked_samples
        except Exception as sample_link_error:  # noqa: BLE001
            entry_observation_state["link_error"] = str(sample_link_error)
            print(
                "[performance_observability] Impossibile collegare i sample "
                f"all'operazione {op_id}: {sample_link_error}"
            )

    print(
        f"[db_utils] Decisione inserita con id={op_id}, "
        f"source={out.get('decision_source')}, "
        f"guard_adjusted={out.get('decision_guard_adjusted', False)}, "
        f"prophet_shadow_samples={prophet_shadow_summary.get('observation_count', 0)}, "
        f"entry_observation_samples={entry_observation_state.get('candidate_samples', 0)}, "
        f"dashboard_forecasts={len(dashboard_forecasts_json)} "
        f"({dashboard_forecast_source})"
    )

    execution_error = None
    try:
        raw_execution_response = bot.execute_signal(out)
        execution_result = normalize_execution_result(out, raw_execution_response)
    except Exception as exc:  # noqa: BLE001
        execution_error = exc
        execution_result = normalize_execution_exception(out, exc)

    execution_id = log_execution_result(
        operation_id=op_id,
        pre_snapshot_id=pre_snapshot_id,
        decision=out,
        execution_result=execution_result,
    )
    print(
        "[execution_audit] "
        f"id={execution_id}, status={execution_result.get('execution_status')}, "
        f"order_id={execution_result.get('order_id')}"
    )

    # Always read the account again after the attempted action, including failed
    # or locally skipped exchange calls, so the audit can compare state.
    account_status = bot.get_account_status()
    with open("account_status_old.json", "w", encoding="utf-8") as status_file:
        json.dump(account_status["open_positions"], status_file, indent=4)
    post_snapshot_id = db_utils.log_account_status(account_status)
    attach_post_snapshot(execution_id, post_snapshot_id)
    print(f"[db_utils] Account snapshot post-esecuzione id={post_snapshot_id}")

    if execution_error is not None:
        raise execution_error

except Exception as e:
    context = {
        "prompt": locals().get("system_prompt"),
        "tickers": locals().get("tickers"),
        "indicators": locals().get("indicators_json"),
        "execution_constraints": locals().get("execution_constraints"),
        "entry_quality_policy": locals().get("entry_quality_summary"),
        "position_management": locals().get("management_state"),
        "prophet_shadow": locals().get("prophet_shadow_summary"),
        "performance_observability": locals().get(
            "performance_observability_summary"
        ),
        "dashboard_forecasts": locals().get("dashboard_forecasts_json"),
        "dashboard_forecast_source": locals().get("dashboard_forecast_source"),
        "news": locals().get("news_txt"),
        "sentiment": locals().get("sentiment_json"),
        "forecasts": locals().get("forecasts_json"),
        "balance": locals().get("account_status"),
        "decision": locals().get("out"),
        "execution_result": locals().get("execution_result"),
        "decision_gate_reason": locals().get("gate_reason"),
    }
    try:
        db_utils.log_error(e, context=context, source="trading_agent")
    except Exception as logging_error:
        print(f"Errore durante il logging DB: {logging_error}")
    print(f"An error occurred: {e}")
    raise

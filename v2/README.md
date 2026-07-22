# Hyperliquid Bot V2 — operational shadow service

V2 is an isolated, read-only shadow system. It streams Hyperliquid market and wallet state, builds replayable features, evaluates the lifecycle of live positions, asks configurable OpenAI/Anthropic/DeepSeek models for **shadow** OPEN/HOLD/CLOSE/TAKE_PARTIAL decisions, and stores every packet and outcome in PostgreSQL.

## Safety boundary

- no private key;
- no Exchange/signing adapter;
- `V2_SHADOW_ONLY=true` and `V2_LIVE_TRADING_ENABLED=false` are mandatory;
- no order endpoint exists;
- the Supervisor can only change `config/experimental_policy.json` on a new branch and open a draft PR;
- merge and deploy remain human actions.

## Operational components

1. Hyperliquid WebSocket and HTTP read-only ingest for mids, trades, order book, candles, active asset context, account state, orders, fills and funding.
2. Feature engine with price velocity/acceleration, aggressive flow, book imbalance, realized volatility, EMA, ATR, RSI, volume ratio and multi-timeframe momentum.
3. Opportunity Engine that creates explicit long theses only when completed-candle structure, anti-chase and reward/risk checks pass.
4. Position Guardian with state transitions, MFE/MAE, green-to-red tracking, dynamic profit floor and `EV_HOLD` versus `EV_CLOSE`.
5. Quant Expert based on comparable outcomes and uncertainty, never a BUY score.
6. Multi-provider LLM router with a conservative challenger and deterministic fallback when API keys are absent.
7. Daily Supervisor endpoint for n8n, with evidence gates and draft-PR-only GitHub access.
8. PostgreSQL append-only audit ledger and service health endpoints.

## Local run

```bash
cp config/env.example .env
export $(grep -v '^#' .env | xargs)
python -m pip install -e '.[dev]'
python -m hyperliquid_v2.runtime.api
```

Endpoints:

- `GET /health`
- `GET /status`
- `POST /supervisor/run` with `X-Supervisor-Token`

## Railway

Create a new service from this repository with **Root Directory `/v2`**. Attach a dedicated PostgreSQL service and set the variables from `config/env.example`. Do not attach `PRIVATE_KEY`.

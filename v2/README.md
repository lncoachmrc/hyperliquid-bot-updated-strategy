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
3. Trend Opportunity Engine that creates explicit long theses only when completed-candle structure, anti-chase and reward/risk checks pass.
4. Bidirectional Failed Breakout Reversal engine:
   - upside breakout followed by a completed 15-minute close back below the level can create a **short** thesis;
   - downside breakout followed by a completed 15-minute close back above the level can create a **long** thesis;
   - at least two live microstructure confirmations are required;
   - retest-rejection and failure-continuation entries are supported;
   - stop placement is beyond the failed breakout extreme plus an ATR buffer;
   - every event is deduplicated and persisted in `v2_failed_breakout_events`.
5. Historical replay of blocked upside breakouts using completed 15-minute closes reconstructed from stored market-feature buckets, with stop, target, MFE, MAE, costs and net R.
6. Fixed tactical-fade research stream:
   - observes the existing adverse-regime `tactical_long_candidate` signature as a counterfactual **short**;
   - records every structural signal that also passes the original hard
     market-quality checks as a base-rate sample;
   - separately records the cost-first subset possible with one global BTC/ETH/SOL position;
   - uses a 180-minute horizon, 10 bps default round-trip cost and a 2-ATR stop for the executable subset;
   - has no `DecisionPacket`, LLM call, risk-enveloping or order interface.
7. Position Guardian with state transitions, MFE/MAE, green-to-red tracking, dynamic profit floor and `EV_HOLD` versus `EV_CLOSE`.
8. Quant Expert based on comparable outcomes and uncertainty, never a BUY score.
9. Multi-provider LLM router with a conservative challenger and deterministic fallback when API keys are absent.
10. Daily Supervisor endpoint for n8n, with evidence gates and draft-PR-only GitHub access.
11. PostgreSQL append-only audit ledger and service health endpoints.

Public `userFills` and `userFundings` events are normalized and deduplicated in
`v2_observed_fills` and `v2_observed_fundings`. The report
`sql/execution_cost_report.sql` measures actual fee bps, maker/taker mix,
funding and approximate slippage against the nearest prior V2 mid.

The two entry engines share an entry-decision lock and cooldown, so only one new-risk decision packet can be routed at a time. Failed-breakout short observations are matured directionally: a falling price produces a positive return for a short thesis rather than being measured as a long return.

## Failed-breakout controls

```bash
V2_FAILED_BREAKOUT_ENABLED=true
V2_FAILED_BREAKOUT_SCAN_SECONDS=15
V2_FAILED_BREAKOUT_REPLAY_ENABLED=true
V2_FAILED_BREAKOUT_RISK_FRACTION=0.0015
V2_FAILED_BREAKOUT_MAX_EFFECTIVE_EXPOSURE=0.20
V2_ENTRY_DECISION_COOLDOWN_SECONDS=60
```

These values only define the risk envelope supplied to the shadow LLM decision packet. V2 still has no signing or order-sending path.

## Tactical-fade research

```bash
V2_TACTICAL_FADE_SHADOW_ENABLED=true
V2_ROUND_TRIP_COST_BPS=10
```

The enable flag controls data collection only. Direction, signal definition,
portfolio lock and 180-minute horizon are deliberately not environment-tunable.
Results are split between `tactical_fade_base_rate` and
`tactical_fade_portfolio_selected`; only the latter is globally
non-overlapping. See `docs/TACTICAL_FADE_SHADOW.md` and
`sql/tactical_fade_shadow_report.sql`.

The implementation decision against the attached draft is documented in
`docs/DRAFT_REVIEW_2026-07-25.md`.

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

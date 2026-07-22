# Railway service layout

## Initial shadow deployment

1. `v2-market-ingestor` — Hyperliquid WebSocket/read-only API, no private key.
2. `v2-trading-core` — opportunity, Quant Expert, risk envelope and model router; shadow only.
3. `v2-position-guardian` — fast stateful position lifecycle worker; read-only account access.
4. `v2-supervisor-worker` — daily audit and PR proposal API; read-only trading DB.
5. `postgres-v2` — V2 append-only ledger and replay data.
6. `redis-v2` — market event stream and consumer coordination.
7. `n8n-supervisor` — scheduling, notifications and human approval workflow.
8. `postgres-n8n` — n8n internal state, separated from trading data.

## Future live-only service

9. `v2-execution-service` — the sole service with `PRIVATE_KEY`. It accepts only signed internal decision contracts that already passed immutable risk checks.

## Security boundaries

- n8n receives a read-only PostgreSQL role for `postgres-v2`.
- n8n has no Hyperliquid private key.
- Supervisor GitHub credentials can create branches/PRs but cannot merge protected `main`.
- Production Railway variables cannot be modified by the Supervisor.
- Redis channels separate market events from execution commands.

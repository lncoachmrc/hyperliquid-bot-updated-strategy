# Railway activation runbook — V2 Shadow

This procedure creates a **new Railway project**. It does not reuse or modify the V1 project, service, PostgreSQL database or environment variables.

## Preconditions

- Railway CLI installed and authenticated (`railway login`);
- repository cloned locally and updated to `main`;
- public Hyperliquid wallet address;
- at least one optional LLM API key for live model decisions. Without a key the service remains operational using the deterministic shadow fallback;
- optional fine-grained GitHub token for the daily Supervisor. Grant repository Contents read/write and Pull requests read/write only. Do not grant workflow or administration access.

## One command

From the repository root:

```bash
bash v2/railway/bootstrap_v2_shadow.sh
```

The script:

1. validates Railway authentication and the local V2 source;
2. asks for the public wallet and model configuration without echoing secrets;
3. creates a new Railway project named `hyperliquid-v2-shadow` by default;
4. creates a dedicated PostgreSQL service;
5. creates a `v2-shadow` application service;
6. sets `DATABASE_URL` through a Railway service-reference variable;
7. enforces `V2_SHADOW_ONLY=true` and `V2_LIVE_TRADING_ENABLED=false`;
8. uploads only the `/v2` directory using `--path-as-root`;
9. generates a Railway domain;
10. calls `/health` and reports success only after the endpoint responds.

The script never reads, asks for or transmits `PRIVATE_KEY`.

## Expected health response

```json
{
  "status": "ok",
  "mode": "shadow",
  "live_trading_enabled": false
}
```

## PostgreSQL verification

Open a PostgreSQL shell for the new project and run:

```sql
SELECT service_name, observed_at, status
FROM v2_service_heartbeats
ORDER BY service_name;

SELECT COUNT(*) AS market_features
FROM v2_market_features;

SELECT COUNT(*) AS shadow_decisions
FROM v2_shadow_actions;

SELECT COUNT(*) AS quant_samples,
       COUNT(*) FILTER (WHERE completed) AS completed_samples
FROM v2_quant_observations;
```

`market_features` should increase approximately every 15 seconds for BTC, ETH and SOL. Decision rows appear only when an entry candidate or live-position review is due.

## n8n activation

After `/health` is green:

1. deploy n8n in a separate Railway service and database;
2. import `v2/n8n/supervisor-workflow.json`;
3. set `V2_SUPERVISOR_URL` to the V2 public domain without a trailing slash;
4. set `V2_SUPERVISOR_TOKEN` to the token printed once by the bootstrap script;
5. keep the workflow inactive until the V2 database is collecting valid samples;
6. activate it only after confirming that `/supervisor/run` returns `no_change` or an evidence-gate rejection.

The Supervisor can create a branch and draft PR only. It cannot merge or deploy.

## Model configuration

The default prompt selects DeepSeek V4 Pro because its current official API model ID is `deepseek-v4-pro`. OpenAI and Anthropic model IDs remain configurable, and every model decision is persisted for later replay benchmarking. Model reputation never overrides measured V2 performance.

## Rollback

Because V2 is a separate project and shadow-only, rollback is simply:

```bash
railway down --service v2-shadow --yes
```

Deleting or stopping V2 has no effect on the V1 live bot.

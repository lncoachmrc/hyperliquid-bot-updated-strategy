# Hyperliquid Bot V2 — clean shadow foundation

This directory is a new project boundary. It does not import the legacy strategy or `main.py`, cannot send orders and is intended to be extracted into a dedicated repository.

## What is implemented in this foundation

- immutable domain contracts for trade thesis, position lifecycle, Quant evidence, risk envelope and LLM decisions;
- Quant Expert that returns distributions and uncertainty instead of a BUY score;
- event-driven position state machine;
- dynamic profit floor and `EV_HOLD` versus `EV_CLOSE` assessment;
- primary/challenger model router with conservative disagreement rules;
- Supervisor policy gate that permits only one evidence-backed change and never merge/deploy;
- append-only PostgreSQL shadow schema;
- importable but disabled n8n daily supervisor workflow;
- Railway service/security layout;
- deterministic regression tests.

## Explicitly not implemented yet

- Hyperliquid WebSocket ingestor;
- order-book and aggressive-flow feature engineering;
- provider HTTP adapters and API keys;
- historical replay engine;
- PostgreSQL repositories;
- FastAPI service endpoints;
- execution service;
- any live trading.

## Local tests

```bash
cd v2
python -m pip install -e '.[dev]'
pytest
```

## Next milestone

Implement the read-only Market Ingestor, event schema and replayable Feature Store. Only after deterministic replay exists should provider APIs or shadow decisions be connected.

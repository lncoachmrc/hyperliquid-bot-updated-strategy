-- Hyperliquid Bot V2: append-only shadow and audit schema.
-- No table below grants trading authority.

CREATE TABLE IF NOT EXISTS v2_decision_packets (
    id BIGSERIAL PRIMARY KEY,
    decision_id UUID NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    market_timestamp TIMESTAMPTZ NOT NULL,
    decision_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    packet JSONB NOT NULL,
    packet_sha256 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v2_decision_packets_symbol_time
    ON v2_decision_packets(symbol, market_timestamp DESC);

CREATE TABLE IF NOT EXISTS v2_model_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_id UUID NOT NULL REFERENCES v2_decision_packets(decision_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence NUMERIC(8, 6),
    latency_ms INTEGER,
    estimated_cost_usd NUMERIC(20, 10),
    response JSONB NOT NULL,
    UNIQUE(decision_id, provider, model, role)
);

CREATE TABLE IF NOT EXISTS v2_position_state_events (
    id BIGSERIAL PRIMARY KEY,
    position_key TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    previous_phase TEXT,
    current_phase TEXT NOT NULL,
    current_r NUMERIC(20, 10),
    mfe_r NUMERIC(20, 10),
    mae_r NUMERIC(20, 10),
    profit_retention_ratio NUMERIC(20, 10),
    giveback_ratio NUMERIC(20, 10),
    continuation_probability NUMERIC(20, 10),
    reversal_probability NUMERIC(20, 10),
    event_payload JSONB NOT NULL,
    UNIQUE(position_key, observed_at)
);

CREATE TABLE IF NOT EXISTS v2_quant_evidence (
    id BIGSERIAL PRIMARY KEY,
    evidence_key TEXT NOT NULL UNIQUE,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    setup_family TEXT NOT NULL,
    symbol TEXT,
    regime TEXT,
    comparable_samples INTEGER NOT NULL,
    out_of_sample_samples INTEGER NOT NULL DEFAULT 0,
    operational BOOLEAN NOT NULL DEFAULT FALSE,
    evidence JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_shadow_actions (
    id BIGSERIAL PRIMARY KEY,
    decision_id UUID NOT NULL REFERENCES v2_decision_packets(decision_id) ON DELETE CASCADE,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    final_shadow_action TEXT NOT NULL,
    resolver_source TEXT NOT NULL,
    resolver_reason TEXT NOT NULL,
    hypothetical_size NUMERIC(30, 10),
    hypothetical_price NUMERIC(30, 10),
    outcome_15m JSONB,
    outcome_60m JSONB,
    outcome_180m JSONB,
    outcome_closed JSONB,
    UNIQUE(decision_id)
);

CREATE TABLE IF NOT EXISTS v2_supervisor_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL UNIQUE,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    outcome TEXT NOT NULL,
    evidence_cutoff TIMESTAMPTZ NOT NULL,
    evidence_sha256 TEXT NOT NULL,
    proposal JSONB,
    policy_reasons JSONB NOT NULL,
    branch_name TEXT,
    pull_request_url TEXT,
    merge_authorized BOOLEAN NOT NULL DEFAULT FALSE,
    deploy_authorized BOOLEAN NOT NULL DEFAULT FALSE
);

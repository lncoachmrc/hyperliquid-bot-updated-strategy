-- Operational shadow-only schema. The runtime applies the same idempotent DDL.
CREATE TABLE IF NOT EXISTS v2_market_features (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    payload JSONB NOT NULL,
    UNIQUE (symbol, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_v2_market_features_symbol_time ON v2_market_features(symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS v2_account_states (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    wallet_address TEXT NOT NULL,
    equity_usd NUMERIC(30,10),
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_decision_packets (
    decision_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decision_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    packet JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_model_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES v2_decision_packets(decision_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence NUMERIC(12,8),
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_shadow_actions (
    id BIGSERIAL PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES v2_decision_packets(decision_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload JSONB NOT NULL,
    UNIQUE(decision_id)
);

CREATE TABLE IF NOT EXISTS v2_position_state_events (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    phase TEXT NOT NULL,
    current_r NUMERIC(20,10),
    mfe_r NUMERIC(20,10),
    mae_r NUMERIC(20,10),
    profit_floor_r NUMERIC(20,10),
    close_review BOOLEAN NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_quant_observations (
    id BIGSERIAL PRIMARY KEY,
    sample_key TEXT NOT NULL UNIQUE,
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    setup_family TEXT NOT NULL,
    baseline_price NUMERIC(30,10) NOT NULL,
    stop_distance_pct NUMERIC(20,10),
    decision_id TEXT,
    source TEXT NOT NULL,
    return_15m_pct NUMERIC(20,10),
    return_60m_pct NUMERIC(20,10),
    return_180m_pct NUMERIC(20,10),
    mfe_r NUMERIC(20,10),
    mae_r NUMERIC(20,10),
    realized_net_r NUMERIC(20,10),
    reached_green BOOLEAN,
    finished_negative BOOLEAN,
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_service_heartbeats (
    service_name TEXT PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_supervisor_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL,
    metrics JSONB NOT NULL,
    model_output JSONB,
    policy_output JSONB,
    github_output JSONB,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS v2_model_benchmarks (
    id BIGSERIAL PRIMARY KEY,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    utility_score NUMERIC(20,10),
    json_valid_rate NUMERIC(12,8),
    action_consistency NUMERIC(12,8),
    counterfactual_net_r NUMERIC(20,10),
    payload JSONB NOT NULL
);

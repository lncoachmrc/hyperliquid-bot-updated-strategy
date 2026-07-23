-- Audit-only performance observability report.
-- Read-only queries: no INSERT/UPDATE/DELETE/DDL.

-- 1. Readiness and horizon completion by policy outcome.
SELECT
    policy_outcome,
    COUNT(*) AS unique_opportunities,
    COUNT(actual_15m_price) AS completed_15m,
    COUNT(actual_60m_price) AS completed_60m,
    COUNT(actual_180m_price) AS completed_180m,
    COUNT(*) FILTER (WHERE executed_open_operation_id IS NOT NULL) AS executed_opens,
    ROUND(
        AVG((actual_15m_price / baseline_price - 1) * 100)
            FILTER (WHERE actual_15m_price IS NOT NULL AND baseline_price > 0),
        4
    ) AS avg_return_15m_pct,
    ROUND(
        AVG((actual_60m_price / baseline_price - 1) * 100)
            FILTER (WHERE actual_60m_price IS NOT NULL AND baseline_price > 0),
        4
    ) AS avg_return_60m_pct,
    ROUND(
        AVG((actual_180m_price / baseline_price - 1) * 100)
            FILTER (WHERE actual_180m_price IS NOT NULL AND baseline_price > 0),
        4
    ) AS avg_return_180m_pct,
    ROUND(
        AVG((max_price_180m / baseline_price - 1) * 100)
            FILTER (WHERE baseline_price > 0),
        4
    ) AS avg_mfe_180m_pct,
    ROUND(
        AVG((min_price_180m / baseline_price - 1) * 100)
            FILTER (WHERE baseline_price > 0),
        4
    ) AS avg_mae_180m_pct
FROM entry_opportunity_samples
GROUP BY policy_outcome
ORDER BY policy_outcome;

-- 2. Counterfactual results by individual block reason.
SELECT
    reason.block_reason,
    COUNT(*) AS unique_opportunities,
    COUNT(s.actual_60m_price) AS completed_60m,
    ROUND(
        AVG((s.actual_15m_price / s.baseline_price - 1) * 100)
            FILTER (WHERE s.actual_15m_price IS NOT NULL AND s.baseline_price > 0),
        4
    ) AS avg_return_15m_pct,
    ROUND(
        AVG((s.actual_60m_price / s.baseline_price - 1) * 100)
            FILTER (WHERE s.actual_60m_price IS NOT NULL AND s.baseline_price > 0),
        4
    ) AS avg_return_60m_pct,
    ROUND(
        AVG((s.actual_180m_price / s.baseline_price - 1) * 100)
            FILTER (WHERE s.actual_180m_price IS NOT NULL AND s.baseline_price > 0),
        4
    ) AS avg_return_180m_pct,
    ROUND(
        AVG((s.max_price_180m / s.baseline_price - 1) * 100)
            FILTER (WHERE s.baseline_price > 0),
        4
    ) AS avg_mfe_180m_pct,
    ROUND(
        AVG((s.min_price_180m / s.baseline_price - 1) * 100)
            FILTER (WHERE s.baseline_price > 0),
        4
    ) AS avg_mae_180m_pct
FROM entry_opportunity_samples s
CROSS JOIN LATERAL jsonb_array_elements_text(s.block_reasons) AS reason(block_reason)
WHERE s.policy_outcome = 'blocked'
GROUP BY reason.block_reason
ORDER BY unique_opportunities DESC, reason.block_reason;

-- 3. Detailed samples with observation lags.
SELECT
    sample_key,
    observed_at,
    symbol,
    strategy_version,
    policy_outcome,
    block_reasons,
    baseline_price,
    target_15m_at,
    actual_15m_at,
    EXTRACT(EPOCH FROM (actual_15m_at - target_15m_at)) AS lag_15m_seconds,
    actual_15m_price,
    target_60m_at,
    actual_60m_at,
    EXTRACT(EPOCH FROM (actual_60m_at - target_60m_at)) AS lag_60m_seconds,
    actual_60m_price,
    target_180m_at,
    actual_180m_at,
    EXTRACT(EPOCH FROM (actual_180m_at - target_180m_at)) AS lag_180m_seconds,
    actual_180m_price,
    max_price_180m,
    min_price_180m,
    first_live_operation,
    last_live_operation,
    executed_open_operation_id
FROM entry_opportunity_samples
ORDER BY observed_at DESC, symbol;

-- 4. External close/stop reconciliation coverage.
SELECT
    reconciliation_status,
    match_method,
    COUNT(*) AS closures,
    COUNT(avg_fill_price) AS closures_with_fill_price,
    COUNT(total_fee) AS closures_with_fee,
    ROUND(SUM(total_fee), 8) AS total_fee,
    ROUND(SUM(total_closed_pnl), 8) AS total_closed_pnl
FROM external_close_reconciliations
GROUP BY reconciliation_status, match_method
ORDER BY reconciliation_status, match_method;

-- 5. Detailed stop reconciliation audit.
SELECT
    r.detected_at,
    r.symbol,
    r.reconciliation_status,
    r.match_method,
    r.expected_stop_order_id,
    r.order_ids,
    r.expected_size,
    r.total_filled_size,
    r.avg_fill_price,
    r.total_fee,
    r.fee_token,
    r.total_closed_pnl,
    r.first_fill_at,
    r.last_fill_at,
    r.attempt_count,
    r.error_message,
    bo.raw_payload->>'reason' AS detected_reason
FROM external_close_reconciliations r
JOIN bot_operations bo ON bo.id = r.detected_operation_id
ORDER BY r.detected_at DESC;

-- 6. Minimum sample gate for adverse-entry optimization.
SELECT
    COUNT(*) AS unique_entry_opportunities,
    COUNT(*) FILTER (WHERE policy_outcome = 'blocked') AS blocked_opportunities,
    COUNT(*) FILTER (WHERE policy_outcome = 'allowed') AS allowed_opportunities,
    COUNT(*) FILTER (WHERE actual_180m_price IS NOT NULL) AS completed_180m,
    COUNT(*) >= 30 AS minimum_30_reached,
    COUNT(*) >= 50 AS preferred_50_reached
FROM entry_opportunity_samples;

-- 7. Last-moment weak-breakout revalidation outcomes.
SELECT
    created_at,
    symbol,
    operation,
    raw_payload->'pre_trade_revalidation'->>'vote_class' AS vote_class,
    raw_payload->'pre_trade_revalidation'->>'previous_1h_high' AS previous_1h_high,
    raw_payload->'pre_trade_revalidation'->>'live_mid' AS live_mid,
    raw_payload->'pre_trade_revalidation'->>'passed' AS passed,
    raw_payload->'pre_trade_revalidation'->>'block_reason' AS block_reason,
    raw_payload->>'pre_trade_revalidation_adjusted' AS adjusted,
    raw_payload->'pre_trade_original_decision'->>'operation' AS original_operation
FROM bot_operations
WHERE raw_payload ? 'pre_trade_revalidation'
ORDER BY created_at DESC;

-- 8. Severe-weakness exit shadow samples, deduplicated by sample key.
WITH observations AS (
    SELECT
        bo.created_at,
        bo.symbol,
        observation.value AS shadow,
        observation.value->>'sample_key' AS sample_key,
        ROW_NUMBER() OVER (
            PARTITION BY observation.value->>'sample_key'
            ORDER BY bo.created_at
        ) AS sample_rank
    FROM bot_operations bo
    CROSS JOIN LATERAL jsonb_each(
        COALESCE(
            bo.raw_payload->'severe_weakness_exit_shadow'->'observations',
            '{}'::jsonb
        )
    ) AS observation(symbol, value)
    WHERE COALESCE(
        (observation.value->>'triggered')::boolean,
        FALSE
    ) IS TRUE
)
SELECT
    created_at AS shadow_observed_at,
    symbol,
    sample_key,
    shadow->>'opened_at' AS opened_at,
    shadow->>'completed_15m_bar' AS completed_15m_bar,
    shadow->>'position_age_minutes' AS position_age_minutes,
    shadow->>'entry_price' AS entry_price,
    shadow->>'hypothetical_exit_price' AS hypothetical_exit_price,
    shadow->>'current_r' AS current_r,
    shadow->>'tactical_confirmations' AS tactical_confirmations,
    shadow->>'consecutive_weak_bars' AS consecutive_weak_bars,
    shadow->>'live_exit_authorized_unchanged' AS live_exit_authorized
FROM observations
WHERE sample_rank = 1
ORDER BY shadow_observed_at DESC;

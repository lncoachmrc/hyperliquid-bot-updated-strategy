-- Hyperliquid live V1 decision funnel audit.
-- Read-only queries. No strategy, order, position or database state is modified.
-- Default window: last 7 days. Change INTERVAL '7 days' where needed.

-- 1. Strategy snapshots: where symbols stop before reaching the LLM.
SELECT
    i.ticker,
    COALESCE(i.strategy->>'regime', 'unknown') AS regime,
    COALESCE(i.strategy->>'recommended_action', 'missing') AS recommended_action,
    COALESCE((i.strategy->>'execution_feasible')::boolean, FALSE) AS execution_feasible,
    COALESCE(i.strategy->'execution_feasibility'->>'reason', 'missing') AS feasibility_reason,
    COUNT(*) AS symbol_snapshots
FROM indicators_contexts i
JOIN ai_contexts a ON a.id = i.context_id
WHERE a.created_at >= NOW() - INTERVAL '7 days'
GROUP BY 1,2,3,4,5
ORDER BY symbol_snapshots DESC, i.ticker;

-- 2. Stage survival rate by symbol.
WITH snapshots AS (
    SELECT
        i.ticker,
        i.strategy,
        COALESCE(i.strategy->>'recommended_action', 'missing') AS action,
        COALESCE((i.strategy->>'execution_feasible')::boolean, FALSE) AS feasible,
        COALESCE(
            (i.strategy->'tactical_intraday'->>'candidate')::boolean,
            FALSE
        ) AS tactical_candidate,
        COALESCE(
            (i.strategy->'adverse_entry_quality'->>'passed')::boolean,
            CASE WHEN i.strategy->>'regime' = 'adverse' THEN FALSE ELSE TRUE END
        ) AS quality_passed
    FROM indicators_contexts i
    JOIN ai_contexts a ON a.id = i.context_id
    WHERE a.created_at >= NOW() - INTERVAL '7 days'
)
SELECT
    ticker,
    COUNT(*) AS total_snapshots,
    COUNT(*) FILTER (WHERE tactical_candidate) AS tactical_candidates,
    COUNT(*) FILTER (
        WHERE action IN ('long_candidate', 'tactical_long_candidate')
    ) AS strategy_candidates_after_overlay,
    COUNT(*) FILTER (
        WHERE action IN ('long_candidate', 'tactical_long_candidate')
          AND quality_passed
    ) AS candidates_after_quality,
    COUNT(*) FILTER (
        WHERE action IN ('long_candidate', 'tactical_long_candidate')
          AND quality_passed
          AND feasible
    ) AS executable_candidates,
    ROUND(
        100.0 * COUNT(*) FILTER (
            WHERE action IN ('long_candidate', 'tactical_long_candidate')
              AND quality_passed
              AND feasible
        ) / NULLIF(COUNT(*), 0),
        2
    ) AS executable_candidate_rate_pct
FROM snapshots
GROUP BY ticker
ORDER BY ticker;

-- 3. Tactical confirmation distribution, including setups that never became candidates.
SELECT
    i.ticker,
    COALESCE(i.strategy->>'regime', 'unknown') AS regime,
    COALESCE((i.strategy->>'donchian_positive_votes')::integer, -1) AS donchian_votes,
    COALESCE((i.strategy->'tactical_intraday'->>'confirmations')::integer, -1) AS confirmations,
    COALESCE((i.strategy->'tactical_intraday'->>'candidate')::boolean, FALSE) AS tactical_candidate,
    COUNT(*) AS observations
FROM indicators_contexts i
JOIN ai_contexts a ON a.id = i.context_id
WHERE a.created_at >= NOW() - INTERVAL '7 days'
GROUP BY 1,2,3,4,5
ORDER BY i.ticker, regime, donchian_votes, confirmations;

-- 4. Exact adverse-entry blockers and their counterfactual returns.
SELECT
    reason.block_reason,
    COUNT(*) AS unique_opportunities,
    COUNT(s.actual_15m_price) AS completed_15m,
    COUNT(s.actual_60m_price) AS completed_60m,
    COUNT(s.actual_180m_price) AS completed_180m,
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
        100.0 * COUNT(*) FILTER (
            WHERE s.actual_180m_price > s.baseline_price
        ) / NULLIF(COUNT(*) FILTER (WHERE s.actual_180m_price IS NOT NULL), 0),
        2
    ) AS positive_180m_rate_pct
FROM entry_opportunity_samples s
CROSS JOIN LATERAL jsonb_array_elements_text(s.block_reasons) AS reason(block_reason)
WHERE s.observed_at >= NOW() - INTERVAL '30 days'
  AND s.policy_outcome = 'blocked'
GROUP BY reason.block_reason
ORDER BY unique_opportunities DESC, reason.block_reason;

-- 5. Decision gate: how often the LLM is skipped and why.
SELECT
    COALESCE(raw_payload->>'decision_source', 'legacy_or_missing') AS decision_source,
    COALESCE(raw_payload->>'decision_gate_reason', 'missing') AS gate_reason,
    operation,
    COUNT(*) AS cycles
FROM bot_operations
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY 1,2,3
ORDER BY cycles DESC, decision_source, gate_reason;

-- 6. Conversion once the LLM was actually called.
SELECT
    operation,
    COUNT(*) AS llm_decisions,
    ROUND(
        100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0),
        2
    ) AS share_pct
FROM bot_operations
WHERE created_at >= NOW() - INTERVAL '7 days'
  AND raw_payload->>'decision_source' = 'llm'
GROUP BY operation
ORDER BY llm_decisions DESC;

-- 7. LLM OPEN decisions downgraded by the deterministic guard.
SELECT
    COALESCE(raw_payload->>'decision_guard_reason', 'not_adjusted') AS guard_reason,
    COUNT(*) AS decisions,
    COUNT(*) FILTER (
        WHERE raw_payload->'llm_original_decision'->>'operation' = 'open'
          AND operation = 'hold'
    ) AS open_to_hold_downgrades
FROM bot_operations
WHERE created_at >= NOW() - INTERVAL '30 days'
  AND COALESCE((raw_payload->>'decision_guard_adjusted')::boolean, FALSE)
GROUP BY 1
ORDER BY decisions DESC;

-- 8. Last-moment breakout revalidation downgrades.
SELECT
    COALESCE(raw_payload->'pre_trade_revalidation'->>'block_reason', 'passed_or_not_applicable') AS outcome,
    COUNT(*) AS decisions,
    COUNT(*) FILTER (
        WHERE raw_payload->'pre_trade_original_decision'->>'operation' = 'open'
          AND operation = 'hold'
    ) AS open_to_hold_downgrades
FROM bot_operations
WHERE created_at >= NOW() - INTERVAL '30 days'
  AND raw_payload ? 'pre_trade_revalidation'
GROUP BY 1
ORDER BY decisions DESC;

-- 9. Execution adapter outcomes after the final decision.
SELECT
    er.requested_operation,
    er.execution_status,
    COALESCE(er.exchange_status, 'none') AS exchange_status,
    COALESCE(er.error_message, 'none') AS error_message,
    COUNT(*) AS attempts
FROM execution_results er
WHERE er.created_at >= NOW() - INTERVAL '30 days'
GROUP BY 1,2,3,4
ORDER BY attempts DESC, requested_operation, execution_status;

-- 10. Hidden fail-closed drawdown blocker. A factor of zero removes all new exposure.
SELECT
    i.ticker,
    COALESCE(i.strategy->'execution_feasibility'->>'portfolio_drawdown_factor', 'missing') AS drawdown_factor,
    COALESCE(i.strategy->'execution_feasibility'->>'reason', 'missing') AS feasibility_reason,
    COUNT(*) AS snapshots
FROM indicators_contexts i
JOIN ai_contexts a ON a.id = i.context_id
WHERE a.created_at >= NOW() - INTERVAL '7 days'
GROUP BY 1,2,3
ORDER BY snapshots DESC, i.ticker;

-- 11. Candidate-to-order conversion by symbol.
WITH candidates AS (
    SELECT DISTINCT
        i.context_id,
        i.ticker,
        i.strategy->>'recommended_action' AS recommended_action,
        COALESCE((i.strategy->>'execution_feasible')::boolean, FALSE) AS execution_feasible
    FROM indicators_contexts i
    JOIN ai_contexts a ON a.id = i.context_id
    WHERE a.created_at >= NOW() - INTERVAL '30 days'
      AND i.strategy->>'recommended_action' IN ('long_candidate', 'tactical_long_candidate')
),
operations AS (
    SELECT
        context_id,
        operation,
        symbol,
        raw_payload->>'decision_source' AS decision_source,
        raw_payload->>'decision_gate_reason' AS gate_reason
    FROM bot_operations
    WHERE created_at >= NOW() - INTERVAL '30 days'
)
SELECT
    c.ticker,
    COUNT(*) AS candidate_snapshots,
    COUNT(*) FILTER (WHERE c.execution_feasible) AS executable_candidate_snapshots,
    COUNT(*) FILTER (
        WHERE c.execution_feasible AND o.decision_source = 'llm'
    ) AS candidates_reaching_llm,
    COUNT(*) FILTER (
        WHERE c.execution_feasible AND o.operation = 'open' AND o.symbol = c.ticker
    ) AS final_open_decisions,
    COUNT(*) FILTER (
        WHERE c.execution_feasible
          AND o.decision_source = 'llm'
          AND o.operation = 'hold'
    ) AS llm_holds_during_candidate_cycles
FROM candidates c
LEFT JOIN operations o ON o.context_id = c.context_id
GROUP BY c.ticker
ORDER BY c.ticker;

-- 12. Current diagnosis in one row.
WITH symbol_funnel AS (
    SELECT
        COUNT(*) AS snapshots,
        COUNT(*) FILTER (
            WHERE i.strategy->>'recommended_action' IN ('long_candidate', 'tactical_long_candidate')
        ) AS strategy_candidates,
        COUNT(*) FILTER (
            WHERE i.strategy->>'recommended_action' IN ('long_candidate', 'tactical_long_candidate')
              AND COALESCE((i.strategy->>'execution_feasible')::boolean, FALSE)
        ) AS executable_candidates
    FROM indicators_contexts i
    JOIN ai_contexts a ON a.id = i.context_id
    WHERE a.created_at >= NOW() - INTERVAL '7 days'
),
cycle_funnel AS (
    SELECT
        COUNT(*) AS cycles,
        COUNT(*) FILTER (WHERE raw_payload->>'decision_source' = 'llm') AS llm_cycles,
        COUNT(*) FILTER (
            WHERE raw_payload->>'decision_source' = 'llm' AND operation = 'open'
        ) AS llm_open_decisions,
        COUNT(*) FILTER (WHERE operation = 'open') AS final_open_decisions
    FROM bot_operations
    WHERE created_at >= NOW() - INTERVAL '7 days'
),
execution_funnel AS (
    SELECT
        COUNT(*) FILTER (
            WHERE requested_operation = 'open'
        ) AS open_execution_attempts,
        COUNT(*) FILTER (
            WHERE requested_operation = 'open' AND execution_status = 'success'
        ) AS successful_opens
    FROM execution_results
    WHERE created_at >= NOW() - INTERVAL '7 days'
)
SELECT *
FROM symbol_funnel
CROSS JOIN cycle_funnel
CROSS JOIN execution_funnel;

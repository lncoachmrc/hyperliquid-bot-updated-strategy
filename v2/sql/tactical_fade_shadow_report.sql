\pset pager off
\pset null '—'

-- Common completed-sample view. Multiplying net R by stop distance expresses
-- both streams in comparable account-independent return percentage.
CREATE TEMP VIEW tactical_fade_completed AS
SELECT
    observed_at,
    symbol,
    source,
    stop_distance_pct::double precision AS stop_distance_pct,
    return_15m_pct::double precision AS return_15m_pct,
    return_60m_pct::double precision AS return_60m_pct,
    return_180m_pct::double precision AS return_180m_pct,
    realized_net_r::double precision AS realized_net_r,
    (
        realized_net_r::double precision
        * stop_distance_pct::double precision
    ) AS realized_net_return_pct,
    (
        payload#>>'{constant_risk_model,modeled_risk_at_stop_usd}'
    )::double precision AS modeled_risk_at_stop_usd,
    (
        realized_net_r::double precision
        * (
            payload#>>'{constant_risk_model,modeled_risk_at_stop_usd}'
          )::double precision
    ) AS modeled_pnl_usd,
    payload->'assessment'->>'policy_fingerprint' AS policy_fingerprint,
    payload->>'exit_policy' AS exit_policy
FROM v2_quant_observations
WHERE completed IS TRUE
  AND source IN (
      'tactical_fade_base_rate',
      'tactical_fade_portfolio_selected'
  );

-- Primary denominator comparison.
SELECT
    source,
    policy_fingerprint,
    COUNT(*) AS n,
    ROUND(AVG(realized_net_return_pct)::numeric, 6)
        AS mean_net_return_pct,
    ROUND(
        AVG(
            CASE WHEN realized_net_return_pct > 0 THEN 1.0 ELSE 0.0 END
        )::numeric,
        6
    ) AS win_rate,
    ROUND(STDDEV_SAMP(realized_net_return_pct)::numeric, 6)
        AS sample_stddev_pct,
    ROUND(SUM(modeled_pnl_usd)::numeric, 4) AS modeled_pnl_usd,
    ROUND(
        (
            AVG(realized_net_return_pct)
            / NULLIF(
                STDDEV_SAMP(realized_net_return_pct) / SQRT(COUNT(*)),
                0
            )
        )::numeric,
        4
    ) AS t_stat
FROM tactical_fade_completed
GROUP BY source, policy_fingerprint
ORDER BY source, policy_fingerprint;

-- Stability by symbol and UTC day. The portfolio source remains the decision
-- denominator; the base-rate rows diagnose where an aggregate result comes from.
SELECT
    source,
    symbol,
    observed_at::date AS utc_day,
    COUNT(*) AS n,
    ROUND(AVG(realized_net_return_pct)::numeric, 6)
        AS mean_net_return_pct,
    ROUND(MIN(realized_net_return_pct)::numeric, 6)
        AS worst_net_return_pct,
    ROUND(MAX(realized_net_return_pct)::numeric, 6)
        AS best_net_return_pct
FROM tactical_fade_completed
GROUP BY source, symbol, observed_at::date
ORDER BY source, utc_day, symbol;

-- Prospective collection gate. This is review readiness, never permission to
-- enable trading.
WITH portfolio AS (
    SELECT *
    FROM tactical_fade_completed
    WHERE source='tactical_fade_portfolio_selected'
),
summary AS (
    SELECT
        COUNT(*) AS n,
        COUNT(DISTINCT symbol) AS symbols,
        AVG(realized_net_return_pct) AS mean_return,
        STDDEV_SAMP(realized_net_return_pct) AS sample_stddev
    FROM portfolio
)
SELECT
    n,
    symbols,
    ROUND(mean_return::numeric, 6) AS mean_net_return_pct,
    ROUND(
        (
            mean_return
            - 1.96 * sample_stddev / NULLIF(SQRT(n), 0)
        )::numeric,
        6
    ) AS approximate_95pct_lower_bound,
    CASE
        WHEN n < 30 THEN 'COLLECTING'
        WHEN n < 50 THEN 'REVIEWABLE_SMALL_SAMPLE'
        WHEN symbols < 3 THEN 'INSUFFICIENT_CROSS_SYMBOL_COVERAGE'
        WHEN mean_return <= 0 THEN 'NO_EDGE'
        WHEN mean_return
             - 1.96 * sample_stddev / NULLIF(SQRT(n), 0) <= 0
            THEN 'UNCERTAIN_EDGE'
        ELSE 'HUMAN_REVIEW_REQUIRED'
    END AS review_status
FROM summary;

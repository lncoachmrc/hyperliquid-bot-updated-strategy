-- Prophet shadow-mode report.
-- Each sample_key is counted once, even when repeated worker cycles saw the same
-- completed 15m bar. Actual prices use the first stored indicator observation at
-- or after the exact +15m/+60m target and expose observation lag for audit.

WITH raw_samples AS (
    SELECT
        a.created_at AS sampled_at,
        i.ticker,
        i.price::numeric AS indicator_price,
        i.strategy->'prophet_shadow' AS shadow,
        i.strategy->'prophet_shadow'->>'sample_key' AS sample_key
    FROM indicators_contexts i
    JOIN ai_contexts a ON a.id = i.context_id
    WHERE i.strategy->'prophet_shadow'->>'mode' = 'shadow'
      AND COALESCE(
            (i.strategy->'prophet_shadow'->>'sample_eligible')::boolean,
            FALSE
          ) IS TRUE
),
unique_samples AS (
    SELECT DISTINCT ON (sample_key)
        sampled_at,
        ticker,
        indicator_price,
        COALESCE(
            NULLIF(shadow->'forecast_1h'->>'last_price', '')::numeric,
            NULLIF(shadow->'forecast_15m'->>'last_price', '')::numeric,
            indicator_price
        ) AS baseline_price,
        sample_key,
        shadow,
        shadow->'hypothetical_policy'->>'verdict' AS shadow_verdict,
        NULLIF(shadow->'forecast_15m'->>'change_pct', '')::numeric
            AS forecast_15m_pct,
        NULLIF(shadow->'forecast_1h'->>'change_pct', '')::numeric
            AS forecast_1h_pct,
        NULLIF(shadow->'forecast_15m'->>'target_timestamp_ms', '')::bigint
            AS target_15m_ms,
        NULLIF(shadow->'forecast_1h'->>'target_timestamp_ms', '')::bigint
            AS target_1h_ms
    FROM raw_samples
    WHERE sample_key IS NOT NULL
    ORDER BY sample_key, sampled_at
),
realized AS (
    SELECT
        s.*,
        p15.actual_at AS actual_15m_at,
        p15.actual_price AS actual_price_15m,
        EXTRACT(EPOCH FROM (
            p15.actual_at - TO_TIMESTAMP(s.target_15m_ms / 1000.0)
        )) AS observation_lag_15m_seconds,
        p60.actual_at AS actual_1h_at,
        p60.actual_price AS actual_price_1h,
        EXTRACT(EPOCH FROM (
            p60.actual_at - TO_TIMESTAMP(s.target_1h_ms / 1000.0)
        )) AS observation_lag_1h_seconds
    FROM unique_samples s
    LEFT JOIN LATERAL (
        SELECT
            a2.created_at AS actual_at,
            i2.price::numeric AS actual_price
        FROM indicators_contexts i2
        JOIN ai_contexts a2 ON a2.id = i2.context_id
        WHERE i2.ticker = s.ticker
          AND i2.price IS NOT NULL
          AND a2.created_at >= TO_TIMESTAMP(s.target_15m_ms / 1000.0)
        ORDER BY a2.created_at
        LIMIT 1
    ) p15 ON s.target_15m_ms IS NOT NULL
    LEFT JOIN LATERAL (
        SELECT
            a2.created_at AS actual_at,
            i2.price::numeric AS actual_price
        FROM indicators_contexts i2
        JOIN ai_contexts a2 ON a2.id = i2.context_id
        WHERE i2.ticker = s.ticker
          AND i2.price IS NOT NULL
          AND a2.created_at >= TO_TIMESTAMP(s.target_1h_ms / 1000.0)
        ORDER BY a2.created_at
        LIMIT 1
    ) p60 ON s.target_1h_ms IS NOT NULL
)
SELECT
    shadow_verdict,
    COUNT(*) AS unique_opportunities,
    COUNT(actual_price_15m) AS completed_15m_samples,
    ROUND(
        AVG((actual_price_15m / baseline_price - 1) * 100)
            FILTER (
                WHERE actual_price_15m IS NOT NULL
                  AND baseline_price > 0
            ),
        4
    ) AS average_actual_15m_pct,
    COUNT(actual_price_1h) AS completed_1h_samples,
    ROUND(
        AVG((actual_price_1h / baseline_price - 1) * 100)
            FILTER (
                WHERE actual_price_1h IS NOT NULL
                  AND baseline_price > 0
            ),
        4
    ) AS average_actual_1h_pct,
    ROUND(AVG(forecast_15m_pct), 4) AS average_forecast_15m_pct,
    ROUND(AVG(forecast_1h_pct), 4) AS average_forecast_1h_pct,
    ROUND(AVG(observation_lag_15m_seconds), 1) AS avg_15m_observation_lag_seconds,
    ROUND(AVG(observation_lag_1h_seconds), 1) AS avg_1h_observation_lag_seconds
FROM realized
GROUP BY shadow_verdict
ORDER BY unique_opportunities DESC, shadow_verdict;

-- Readiness gate: do not consider live Prophet weighting before at least 30
-- unique comparable samples; 50 is preferred.
WITH unique_keys AS (
    SELECT DISTINCT i.strategy->'prophet_shadow'->>'sample_key' AS sample_key
    FROM indicators_contexts i
    WHERE i.strategy->'prophet_shadow'->>'mode' = 'shadow'
      AND COALESCE(
            (i.strategy->'prophet_shadow'->>'sample_eligible')::boolean,
            FALSE
          ) IS TRUE
)
SELECT
    COUNT(*) AS comparable_unique_samples,
    COUNT(*) >= 30 AS minimum_30_reached,
    COUNT(*) >= 50 AS preferred_50_reached
FROM unique_keys
WHERE sample_key IS NOT NULL;

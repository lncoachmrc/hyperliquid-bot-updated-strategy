-- News & sentiment shadow report.
-- Live weight is always 0. Do not operationalize before >=30 unique news events,
-- preferably 50, and review results by horizon/category/asset.

WITH event_totals AS (
    SELECT
        COUNT(*) FILTER (WHERE event_kind = 'news') AS news_unique_events,
        COALESCE(SUM(exact_duplicate_count) FILTER (WHERE event_kind = 'news'), 0)
            AS news_exact_duplicates,
        COALESCE(SUM(semantic_duplicate_count) FILTER (WHERE event_kind = 'news'), 0)
            AS news_semantic_duplicates,
        COUNT(*) FILTER (WHERE event_kind = 'sentiment') AS sentiment_unique_events,
        COALESCE(SUM(exact_duplicate_count) FILTER (WHERE event_kind = 'sentiment'), 0)
            AS sentiment_exact_duplicates
    FROM news_sentiment_shadow_events
), completed AS (
    SELECT
        e.event_kind,
        o.horizon_minutes,
        COUNT(DISTINCT e.id) AS completed_unique_events,
        COUNT(*) AS completed_asset_observations,
        AVG(o.realized_return_pct) AS avg_return_pct,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY o.realized_return_pct)
            AS median_return_pct,
        AVG(CASE WHEN o.expected_direction <> 0 AND o.direction_correct THEN 1.0
                 WHEN o.expected_direction <> 0 THEN 0.0 END) AS directional_hit_rate,
        AVG(o.mfe_pct) AS avg_mfe_pct,
        AVG(o.mae_pct) AS avg_mae_pct
    FROM news_sentiment_shadow_observations o
    JOIN news_sentiment_shadow_events e ON e.id = o.event_id
    WHERE o.status = 'complete'
    GROUP BY e.event_kind, o.horizon_minutes
)
SELECT
    t.news_unique_events,
    30 AS minimum_unique_news_target,
    50 AS preferred_unique_news_target,
    ROUND(LEAST(100.0, t.news_unique_events * 100.0 / 30.0), 2)
        AS progress_to_minimum_pct,
    t.news_exact_duplicates,
    t.news_semantic_duplicates,
    t.sentiment_unique_events,
    t.sentiment_exact_duplicates,
    c.event_kind,
    c.horizon_minutes,
    c.completed_unique_events,
    c.completed_asset_observations,
    ROUND(c.avg_return_pct::numeric, 4) AS avg_return_pct,
    ROUND(c.median_return_pct::numeric, 4) AS median_return_pct,
    ROUND((c.directional_hit_rate * 100.0)::numeric, 2) AS directional_hit_rate_pct,
    ROUND(c.avg_mfe_pct::numeric, 4) AS avg_mfe_pct,
    ROUND(c.avg_mae_pct::numeric, 4) AS avg_mae_pct,
    0 AS live_weight_pct
FROM event_totals t
LEFT JOIN completed c ON TRUE
ORDER BY c.event_kind, c.horizon_minutes;

-- Breakdown by news category, asset and horizon.
SELECT
    e.event_category,
    o.symbol,
    o.horizon_minutes,
    COUNT(DISTINCT e.id) AS unique_events,
    COUNT(*) AS observations,
    ROUND(AVG(o.realized_return_pct)::numeric, 4) AS avg_return_pct,
    ROUND(
        (AVG(CASE WHEN o.expected_direction <> 0 AND o.direction_correct THEN 1.0
                  WHEN o.expected_direction <> 0 THEN 0.0 END) * 100.0)::numeric,
        2
    ) AS directional_hit_rate_pct,
    ROUND(AVG(o.mfe_pct)::numeric, 4) AS avg_mfe_pct,
    ROUND(AVG(o.mae_pct)::numeric, 4) AS avg_mae_pct
FROM news_sentiment_shadow_observations o
JOIN news_sentiment_shadow_events e ON e.id = o.event_id
WHERE e.event_kind = 'news'
  AND o.status = 'complete'
GROUP BY e.event_category, o.symbol, o.horizon_minutes
ORDER BY unique_events DESC, e.event_category, o.symbol, o.horizon_minutes;

-- Recent unique events and their deduplication counters.
SELECT
    id,
    event_kind,
    provider,
    first_seen_at,
    published_at,
    event_category,
    assets,
    relevance_score,
    direction_score,
    confidence,
    exact_duplicate_count,
    semantic_duplicate_count,
    title,
    sentiment_value,
    sentiment_classification
FROM news_sentiment_shadow_events
ORDER BY first_seen_at DESC
LIMIT 100;

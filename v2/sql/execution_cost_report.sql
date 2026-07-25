\pset pager off
\pset null '—'

-- Fee truth from public Hyperliquid fills. A positive funding_usd is retained
-- with the exchange sign; interpret payment/receipt using the raw payload.
SELECT
    COUNT(*) AS fills,
    COUNT(*) FILTER (WHERE is_maker) AS maker_fills,
    COUNT(*) FILTER (WHERE is_maker IS FALSE) AS taker_fills,
    ROUND(AVG(fee_bps)::numeric, 6) AS avg_fee_bps,
    ROUND(
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fee_bps)::numeric,
        6
    ) AS median_fee_bps,
    ROUND(SUM(fee_usd)::numeric, 6) AS total_fee_usd,
    ROUND(SUM(builder_fee_usd)::numeric, 6) AS total_builder_fee_usd,
    MIN(exchange_time) AS first_fill,
    MAX(exchange_time) AS last_fill
FROM v2_observed_fills;

-- Approximate execution slippage against the most recent persisted V2 mid.
-- Values are omitted if the reference feature is more than 60 seconds old.
WITH references AS (
    SELECT
        fill.*,
        feature.observed_at AS reference_time,
        CASE
            WHEN feature.observed_at >= fill.exchange_time
                                      - INTERVAL '60 seconds'
            THEN (feature.payload->>'mid_price')::double precision
        END AS reference_mid
    FROM v2_observed_fills fill
    LEFT JOIN LATERAL (
        SELECT observed_at, payload
        FROM v2_market_features
        WHERE symbol=fill.symbol
          AND observed_at <= fill.exchange_time
        ORDER BY observed_at DESC
        LIMIT 1
    ) feature ON TRUE
),
costs AS (
    SELECT
        *,
        CASE
            WHEN reference_mid IS NULL OR reference_mid <= 0 THEN NULL
            WHEN UPPER(side) IN ('B', 'BUY')
                THEN (price / reference_mid - 1.0) * 10_000
            WHEN UPPER(side) IN ('A', 'SELL')
                THEN (reference_mid - price) / reference_mid * 10_000
        END AS slippage_bps
    FROM references
)
SELECT
    symbol,
    is_maker,
    COUNT(*) AS fills,
    COUNT(slippage_bps) AS fills_with_reference_mid,
    ROUND(AVG(fee_bps)::numeric, 6) AS avg_fee_bps,
    ROUND(AVG(slippage_bps)::numeric, 6) AS avg_slippage_bps,
    ROUND(AVG(fee_bps + slippage_bps)::numeric, 6)
        AS avg_observed_one_way_cost_bps
FROM costs
GROUP BY symbol, is_maker
ORDER BY symbol, is_maker;

SELECT
    symbol,
    COUNT(*) AS funding_events,
    ROUND(SUM(funding_usd)::numeric, 6) AS total_funding_usd,
    MIN(exchange_time) AS first_event,
    MAX(exchange_time) AS last_event
FROM v2_observed_fundings
GROUP BY symbol
ORDER BY symbol;

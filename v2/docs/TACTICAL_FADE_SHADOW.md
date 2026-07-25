# Tactical Fade — fixed V2 shadow experiment

## Decision

The period-optimized decision tree is not part of this repository. The only
new strategy hypothesis is the unfiltered observation that an adverse-regime
15-minute tactical rally may be better treated as a short fade than as a long
entry. It remains research telemetry, not a trading action.

The historical audit reproduced the following result at a fixed 10 bps:

| Historical view | Samples | Mean net return |
| --- | ---: | ---: |
| All matured signals | 136 | +0.28049% |
| After removing the best 10 | 126 | +0.20249% |
| Non-overlap inside each symbol | 28 | +0.15584% |
| One global correlated position, fixed prior tie-break | 12 | +0.08965% |

These figures are in-sample and simulated. The last row is the closest to an
implementable portfolio and is far smaller than the apparent all-signal edge.
Across all six fixed symbol priorities its 10 bps mean ranged from +0.07052%
to +0.15511%; at 20 bps the range becomes -0.02948% to +0.05511%. Measured
costs and the predeclared tie-break therefore matter enough to change the sign.
Only the 180-minute horizon was positive in the audit; 15 and 60 minutes were
negative. The horizon therefore also needs prospective validation.

## Frozen signal

The signal mirrors the V1 `tactical_long_candidate` signature using only
completed bars:

- completed daily regime is adverse: close below MA200 and MA100 below MA200;
- completed 15-minute close is above EMA20;
- four-bar 15-minute momentum is positive;
- at least five of seven checks pass:
  price above EMA20, EMA20 above EMA50, MACD positive, MACD rising, RSI in
  `[50, 80]`, volume ratio at least `0.80`, and positive four-bar momentum.
- the V1-style hard quality inputs are available and below their halt levels:
  spread below 20 bps, absolute funding below `0.0030`, and absolute
  mark/oracle dislocation below 50 bps; the book is no older than 30 seconds
  and asset context no older than five minutes.

It records a counterfactual short with:

- 180-minute fixed horizon;
- configured round-trip costs, 10 bps by default;
- base-rate stream with horizon-only exit;
- portfolio stream with stop at `2 × ATR14`, clipped to `[0.5%, 5%]`, or the
  180-minute time exit;
- constant-risk outcome measurement in R;
- a theoretical constant-risk notional capped by the existing V2 maximum
  effective exposure, recorded with its equity source but never submitted;
- one global BTC/ETH/SOL position at a time.

The portfolio tie-break is fixed before prospective collection: lowest spread,
then most confirmations, then data quality, then BTC/ETH/SOL. Every alternative
is retained in the selected sample payload.

## Two denominators

`tactical_fade_base_rate` records every structural signal that also passes the
predeclared hard market-quality checks. These observations may overlap across
time and symbols and answer only whether the broad base rate persists.

`tactical_fade_portfolio_selected` enforces one global correlated position and
answers what the shadow portfolio could actually have taken. It is the primary
denominator for any future promotion decision.

## Data integrity and safety

- Feature snapshots reject mids, books, trades and candles timestamped after
  the observation time.
- Daily and 15-minute indicators use completed candles only.
- The module has no `packet()` or execution method.
- It never invokes an LLM and never creates a shadow action.
- V2 remains read-only, carries no private key and rejects live settings.
- The V1 live strategy and sizing are unchanged.

The portfolio stop is detected from the observed mid path and booked at the
stop threshold. It does not model a gap through the stop, queue position or
exit slippage. Treat its PnL as optimistic until real execution costs exist.

V2 now normalizes public wallet fills and funding into
`v2_observed_fills`/`v2_observed_fundings`. Use
`sql/execution_cost_report.sql` to replace the provisional 10 bps with
fee, maker/taker, funding and nearest-prior-mid slippage evidence. Changing
the cost convention creates a new policy fingerprint rather than rewriting
older observations.

## Review gate

Do not change direction, horizon, thresholds, stop, cost convention or
tie-break during collection. Review after at least 30 globally non-overlapping
completed portfolio samples; prefer 50. A positive mean alone is insufficient:
inspect costs, stop incidence, symbol/day stability, tail dependence and the
confidence interval. No database result promotes the strategy automatically.

Run:

```bash
psql "$V2_DATABASE_URL" -f sql/tactical_fade_shadow_report.sql
```

This is an empirical software experiment, not a promise of profit or financial
advice.

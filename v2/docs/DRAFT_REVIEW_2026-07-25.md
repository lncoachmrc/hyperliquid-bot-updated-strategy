# Review of the attached repository-change draft

## Accepted and integrated

### Account-mode-aware equity

The draft correctly identified the V2 zero-risk failure: reading only
`marginSummary.accountValue` is insufficient for Unified/Portfolio Margin.
The implementation now:

- calls the public `userAbstraction`, `clearinghouseState` and
  `spotClearinghouseState` endpoints;
- caches account mode for five minutes;
- selects one collateral view without double-counting perp and spot;
- falls back to spot only when account mode is unknown and perp equity is zero;
- persists equity, available balance, source, mode and warnings;
- preserves the enriched resolution across WebSocket perp-state updates.

### Cost instrumentation

The proposed SQL was not sufficient on its own because no runtime writer
populated its columns or tables. Instead, V2 now normalizes and deduplicates
the public `userFills` and `userFundings` streams into:

- `v2_observed_fills`, including fee bps, builder fee, maker/taker,
  notional, closed PnL and exchange timestamp;
- `v2_observed_fundings`, including funding rate, position size and USDC
  amount.

`sql/execution_cost_report.sql` joins fills to the nearest prior V2 mid to
estimate one-way slippage. The reference is rejected when older than 60
seconds.

### Constant-risk sizing and correlation guard

No live V1 sizing authority was changed. The globally locked tactical-fade
shadow sample stores a theoretical constant-risk notional, capped by the
existing V2 maximum effective exposure. Only one BTC/ETH/SOL counterfactual
position can be active at a time.

## Improved strategy decision

The optimized eight-trade tree and any `enforce` switch remain absent. The
only strategy addition is a frozen, non-executable research hypothesis:

- fade eligible adverse-regime tactical long candidates with a short;
- record all eligible signals as the overlapping base-rate denominator;
- separately select the globally executable denominator with a predeclared
  cost-first tie-break;
- use horizon-only results for the base rate and stop-or-180-minute results
  for the portfolio;
- never create a DecisionPacket, invoke an LLM or call an exchange method.

## Additional correction found during review

The generic V2 feature snapshot previously consumed the newest buffered mid
and candles even if their exchange timestamps were later than the observation
time. The implementation now:

- selects mids, books, trades, OI, funding and asset context at or before the
  observation timestamp;
- deduplicates and sorts candles;
- computes indicators from completed candles only;
- rejects stale market-quality inputs for the tactical-fade portfolio.

This is a data-integrity correction, not a profitability claim.

## Not implemented

- No V1 live strategy, leverage, sizing or execution path was changed.
- No private key, signing client or order adapter was added to V2.
- No period-optimized or mean-reversion prototype was merged.
- No deploy, GitHub push or live order was performed.
- The draft’s standalone migration was not copied into V1 because dead schema
  without capture code would create the appearance of measured costs without
  producing them.

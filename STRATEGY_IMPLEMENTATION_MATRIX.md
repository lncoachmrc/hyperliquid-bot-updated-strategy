# Strategy Implementation Matrix

| Requisito della strategia | Fonte nel rapporto/revisione | Implementazione | File | Test | Stato |
|---|---|---|---|---|---|
| Trend following / TSMOM | Conclusione convergente | Ensemble Donchian daily | `strategy_core.py` | `test_valid_long_candidate...` | Implementato |
| No look-ahead | Protocollo quantitativo | Canali con `shift(1)` | `strategy_core.py` | `test_donchian_uses_previous_channel...` | Implementato |
| Bias long | Conclusione prudenziale | Nessun nuovo short nel prompt | `system_prompt.txt` | test prompt invariance | Implementato |
| BTC/ETH + SOL cautela | Universo vincente | Cap specifici | `strategy_config.py` | test cap SOL | Implementato |
| Volatility targeting | Formula prioritaria | RV30 annualizzata, target 18% | `strategy_core.py` | test alta volatilità | Implementato |
| Leva dinamica | Formula ricerca | esposizione → leva intera + quota | `strategy_core.py` | test esposizione 1,5× | Implementato |
| Leva massima 2× | Esito prudenziale | schema JSON e config | `trading_agent.py` | test schema | Implementato |
| Risk per trade | Regole 0,25–1% | default 0,5% / stop ATR | `strategy_core.py` | snapshot tests | Implementato |
| Stop | Specifica operativa | 3×ATR20, campo esistente | strategy + executor originale | test snapshot | Implementato |
| Regime | H8 / conclusione | MA100/MA200 | `strategy_core.py` | test regime avverso | Implementato |
| Funding | H4 | riduzione/halt | `strategy_core.py` | test funding estremo | Implementato |
| Spread/liquidità | H4 | L2 spread + volume | indicatori/strategy | test spread | Implementato |
| Mark/index dislocation | Protocollo | mark/oracle bps | indicatori/strategy | pure factor coverage indiretta | Implementato |
| Correlazione | H3/H7 | media pairwise 60g, fattore 0,75 | strategy/indicators | calcolo puro importato | Implementato |
| Drawdown deleveraging | Formula leva | fattore lineare da snapshot DB | db/main/prompt | test boundaries | Implementato |
| Gross cap 1,5× | Conclusione | regola hard nel prompt | prompt/config | invariance test | Implementato, non nuovo risk engine |
| Dati stale | Regole sospensione | max 36h | `indicators.py` | documentato | Implementato |
| Costi | H4 | fee e funding esposti al modello | indicatori/prompt | import/format checks | Parziale |
| Slippage/liquidazione | Protocollo | warning e cap leva; executor originale | prompt | N/A offline | Parziale |
| Walk-forward/backtest | Protocollo | non incorporato nel live bot | repository separata | N/A | Da eseguire esternamente |
| Paper trading | Verifica finale | `TESTNET=True` mantenuto | `main.py` | non avviato | Richiede credenziali |

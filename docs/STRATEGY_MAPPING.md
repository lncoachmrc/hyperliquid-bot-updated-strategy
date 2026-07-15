# Strategy Mapping

## Fonte strategica disponibile

L'implementazione segue la conclusione congiunta della Deep Research e della successiva revisione indipendente: famiglia trend following / time-series momentum con bias long, volatility targeting, filtri di regime e liquidità, leva prudenziale massima 1,5–2×, BTC/ETH come major e SOL con cautela.

Il rapporto disponibile nella conversazione non definiva in modo univoco ogni parametro esecutivo. In conformità al principio “non inventare risultati della ricerca”, i valori operativi non direttamente dimostrati sono raccolti in `strategy_config.py`, etichettati come default da sottoporre a backtest indipendente e facilmente modificabili senza cambiare architettura.

| Elemento della ricerca | Valore implementato | Componente originale coinvolto | File | Tipo di modifica |
|---|---|---|---|---|
| Famiglia | Donchian trend following / TSMOM | Indicatori forniti all'LLM | `strategy_core.py`, `indicators.py` | Strategia |
| Universo | BTC, ETH, SOL prudenziale | Elenco ticker e schema LLM | `main.py`, `trading_agent.py` | Configurazione strategica |
| Timeframe primario | 1 giorno, barre completate | Raccolta OHLCV | `indicators.py` | Strategia |
| Ensemble | Donchian 20/55/120 | Nuovo calcolo puro | `strategy_core.py` | Strategia configurabile |
| Regime | prezzo e MA100 sopra MA200 | Nuovo calcolo puro | `strategy_core.py` | Strategia configurabile |
| Volatility targeting | target 18%, RV 30 giorni | Evidenza e cap per LLM | `strategy_core.py` | Strategia configurabile |
| Position sizing | min(vol target, rischio 0,5%/stop, cap asset) | Quota già scelta dall'LLM | `strategy_core.py`, prompt | Strategia; autorità LLM invariata |
| Stop | 3 × ATR20, espresso in percentuale | Campo stop esistente | `strategy_core.py`, `trading_agent.py` | Parametro strategico |
| Leva | exchange 1–2×; esposizione frazionaria tramite quota | Campo leva esistente | `trading_agent.py`, prompt | Limite strategico |
| Drawdown | fattore lineare 1→0 tra -5% e -15% | PostgreSQL esistente + prompt | `db_utils.py`, `main.py` | Adattamento minimo |
| Funding | riduzione da 0,15%; stop da 0,30% per intervallo | Dato già disponibile | `strategy_core.py` | Filtro strategico |
| Spread | pieno ≤5 bps, stop ≥20 bps | Order book già accessibile | `indicators.py`, `strategy_core.py` | Filtro strategico |
| Dislocazione | mark/oracle: riduzione ≥20 bps, stop ≥50 bps | Contesto Hyperliquid | `indicators.py`, `strategy_core.py` | Filtro strategico |
| Volume | riduzione sotto decile mobile | OHLCV | `strategy_core.py` | Filtro strategico |
| Correlazione | riduzione oltre 0,75 media 60g | Serie dei tre asset | `strategy_core.py`, `indicators.py` | Limite aggregato |
| Direzione | nessuna nuova posizione short | Prompt e schema compatibile | `system_prompt.txt`, `trading_agent.py` | Strategia |
| Sospensione | dati mancanti/stale, spread, funding, dislocazione, regime avverso | Prompt LLM | `strategy_core.py`, `system_prompt.txt` | Strategia |
| News/forecast/sentiment | contesto secondario, mai override hard halt | Workflow originale | `system_prompt.txt` | Solo priorità strategica |

## Assunzioni esplicite da validare

- Lookback Donchian 20/55/120.
- Target di volatilità 18%.
- ATR20 × 3.
- Rischio per trade 0,5%.
- Soglie di spread, funding e dislocazione.
- Fattore drawdown lineare e soglie -5%/-15%.
- Cap specifici BTC/ETH/SOL.

Questi valori non sono presentati come prova di superiorità. Sono una specifica configurabile coerente con la famiglia vincente, da confrontare con regioni parametriche alternative mediante il motore di backtest separato.

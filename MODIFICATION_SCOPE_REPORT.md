# Modification Scope Report

## File strategici nuovi

| File | Motivo | Necessità | Rischio regressione | Test |
|---|---|---|---|---|
| `strategy_config.py` | Parametri isolati e configurabili | Alta | Basso | strategy tests |
| `strategy_core.py` | Calcoli puri della nuova strategia | Alta | Medio | 17 test puri e filtri |
| `tests/test_strategy_core.py` | Verifica segnali, filtri e leva | Alta | Nessuno runtime | pytest |
| `tests/test_architecture_invariance.py` | Prova di non regressione del flusso | Alta | Nessuno runtime | pytest |
| `tests/conftest.py` | Import locale dei moduli | Tecnica | Basso | pytest |
| `pytest.ini` | Esclude script operativo originale | Alta | Basso | pytest |

## File modificati per la strategia

| File | Motivo | Classificazione | Necessità | Rischio |
|---|---|---|---|---|
| `indicators.py` | OHLCV daily completato, Donchian/vol, spread, mark/oracle | Strategia | Alta | Medio |
| `system_prompt.txt` | Regole della nuova strategia, autorità LLM invariata | Prompt strategico | Alta | Medio |
| `trading_agent.py` | Schema massimo 2× e stop ATR compatibile | Parsing/config strategica | Alta | Basso |
| `main.py` | Aggiunta drawdown state al contesto | Adattamento indispensabile | Alta | Basso |
| `db_utils.py` | Riutilizzo snapshot esistenti e log strategia | Adattamento indispensabile | Media | Medio |
| `README.md` | Istruzioni e strategia | Documentazione | Alta | Nessuno |
| `requirements.txt` | Aggiunta pytest/requests espliciti | Test/compatibilità | Bassa | Basso |

## File operativi mantenuti nel medesimo ruolo

- `hyperliquid_trader.py`: stesso client, firma, sizing, leverage setter, order flow, stop e status API. Nel pacchetto è stato reidratato dalla sorgente analizzata; non contiene logica della nuova strategia.
- `forecaster.py`, `news_feed.py`, `sentiment.py`, `utils.py`, `whalealert.py`: restano fornitori di contesto o utility; non possiedono autorità decisionale.
- `railway.json`: stesso start command.
- `test_trading.py`: preservato come script manuale separato; non raccolto da pytest.

## Modifiche escluse

Non sono stati introdotti:

- nuovo orchestratore;
- nuovo risk engine con autorità superiore;
- secondo agente LLM;
- nuovo provider LLM;
- nuovo adapter Hyperliquid;
- nuovo order manager;
- scheduler parallelo;
- credenziali o file `.env`;
- backtest presentato come prova di redditività.

## Nota sul diff

Non essendo disponibile un clone Git nel runtime, la classificazione del diff è basata sul confronto semantico con i file del commit GitHub letto tramite connettore. Questa limitazione è riportata anche nel Build Report.

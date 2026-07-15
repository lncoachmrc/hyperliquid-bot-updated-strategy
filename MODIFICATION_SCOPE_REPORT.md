# Modification Scope Report

## File strategici

| File | Motivo | Necessità | Rischio regressione | Test |
|---|---|---|---|---|
| `strategy_config.py` | Parametri isolati e configurabili | Alta | Basso | strategy tests |
| `strategy_core.py` | Calcoli puri della nuova strategia | Alta | Medio | strategy tests |
| `indicators.py` | Donchian, volatilità e filtri di mercato | Alta | Medio | pytest |
| `system_prompt.txt` | Regole strategiche mantenendo l’autorità LLM | Alta | Medio | invariance tests |
| `trading_agent.py` | Contratto output e limiti strategici | Alta | Basso | schema tests |

## Adattamenti Railway/PostgreSQL

| File | Motivo | Cambia chi decide il trade? | Rischio | Test |
|---|---|---:|---|---|
| `worker.py` | Esegue periodicamente il `main.py` originale | No | Basso | Railway runtime tests |
| `db_init.py` | Inizializzazione idempotente PostgreSQL con retry | No | Basso | static/config tests |
| `runtime_config.py` | Parsing dell’intervallo e dei flag del worker | No | Basso | parser tests |
| `railway.json` | Railpack, pre-deploy DB, worker, replica singola | No | Basso | config tests |
| `.env.example` | Elenco variabili senza segreti | No | Nessuno | secret placeholder test |
| `README.md` | Procedura operativa Railway/Postgres | No | Nessuno | review |
| `tests/test_railway_runtime.py` | Regressione della configurazione di hosting | No | Nessuno | pytest |

## Componenti operativi non modificati dal deploy

- `main.py` conserva il ciclo dati → LLM → `HyperLiquidTrader.execute_signal`.
- `hyperliquid_trader.py` conserva client, sizing, leva, ordine, stop e status.
- `db_utils.py` continua a possedere schema e logging PostgreSQL.
- provider e modello LLM restano invariati.
- non è stato aggiunto un secondo decisore, risk engine o order manager.

## Delimitazione del worker

Il worker può soltanto:

- inizializzare il database;
- acquisire/rilasciare un advisory lock;
- avviare `main.py` come child process;
- attendere il successivo intervallo;
- rispondere ai segnali di arresto Railway.

Non importa né chiama direttamente il modello LLM, la strategia o l’execution engine.

## Segreti

Nessuna credenziale reale è stata aggiunta. `.env.example` contiene campi vuoti; i valori devono essere configurati nelle Variables di Railway.

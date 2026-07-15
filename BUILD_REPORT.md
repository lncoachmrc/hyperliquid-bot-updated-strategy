# Build Report

## Strategia implementata

Donchian trend following / time-series momentum a bias long, su BTC, ETH e SOL prudenziale, con volatility targeting, sizing per rischio, filtri di regime/liquidità/funding/correlazione/drawdown e leva exchange massima 2×.

## Hosting implementato

La repository è configurata per Railway come worker persistente con PostgreSQL:

- build Railpack;
- `python -u db_init.py` come pre-deploy;
- `python -u worker.py` come start command;
- intervallo predefinito di 600 secondi;
- una replica;
- advisory lock PostgreSQL contro cicli simultanei;
- ciclo decisionale originale eseguito tramite `main.py` in un child process.

## Commit sorgente

Repository strategica derivata dal commit originale `ce86f4c717d96ab334c5872ae2eddea59e9f5ff0` di `Rizzo-AI-Academy/rizzo-trading-agent`.

## Validazione Railway/PostgreSQL

La verifica è stata eseguita su una copia pulita del pacchetto strategico, sovrapponendo i file effettivamente pubblicati sul branch GitHub `main`.

```bash
python -m compileall -q .
pytest -q
```

## Risultati

| Controllo | Esito |
|---|---|
| Compilazione Python | Superata |
| Test pytest offline complessivi | 28 superati, 0 falliti |
| Test strategici | Superati |
| Test di non regressione architetturale | Superati |
| Test Railway/PostgreSQL | 5 superati |
| Parsing `railway.json` | Superato |
| Worker senza chiamate dirette a LLM/executor | Verificato |
| Advisory lock PostgreSQL presente | Verificato |
| Placeholder segreti vuoti in `.env.example` | Verificato |
| Ordini live/testnet | Nessuno inviato |
| Avvio Railway end-to-end | Non eseguito: richiede progetto, database e segreti dell’utente |

## Test Railway coperti

- builder Railpack;
- pre-deploy PostgreSQL;
- start command del worker;
- replica singola e deploy senza overlap;
- esecuzione del `main.py` originale come child process;
- assenza nel worker di chiamate all’LLM e all’execution engine;
- advisory lock PostgreSQL;
- validazione delle variabili runtime;
- assenza di valori reali nei placeholder dei segreti.

## Variabili richieste su Railway

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
PRIVATE_KEY
WALLET_ADDRESS
OPENAI_API_KEY
CMC_PRO_API_KEY
BOT_INTERVAL_SECONDS=600
BOT_RUN_IMMEDIATELY=true
BOT_LOCK_ID=7260315
DB_INIT_ATTEMPTS=30
DB_INIT_RETRY_SECONDS=2
```

Il nome `Postgres` nel riferimento deve corrispondere al nome reale del servizio database Railway.

## Problemi residui

1. Nessun backtest 2022–2026 è incluso in questa repository.
2. Nessuna prova end-to-end è stata eseguita con un account Hyperliquid testnet e un progetto Railway reale.
3. `TESTNET=True` resta codificato in `main.py`; il deploy non abilita la mainnet.
4. Il file locale `account_status_old.json` non è persistenza durevole: può essere ricreato o perso durante un redeploy; gli snapshot principali restano invece in PostgreSQL.
5. `main.py` intercetta internamente alcune eccezioni: il child process può terminare con codice 0 anche dopo aver stampato un errore operativo. I log Railway e la tabella `errors` devono quindi essere controllati.
6. Il gross cap resta una regola impartita al decisore LLM, non un nuovo blocco deterministico, per preservare l’architettura originale.
7. Stop, slippage, liquidazioni e ADL non sono modellati o garantiti dal deployment.
8. I parametri strategici richiedono backtest indipendente, walk-forward e paper trading.

## Avvio locale

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python db_init.py
pytest -q
python worker.py
```

Non inserire segreti nel repository e non attivare capitale reale sulla base dei soli test offline.

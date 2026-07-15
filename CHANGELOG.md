# Changelog

## 1.1.0-railway — 2026-07-15

### Railway e PostgreSQL

- configurata la build Railway con Railpack;
- aggiunto `db_init.py` come pre-deploy idempotente con retry PostgreSQL;
- aggiunto `worker.py` come processo persistente con intervallo configurabile;
- impostata una sola replica e disabilitata la sovrapposizione dei deploy;
- aggiunto advisory lock PostgreSQL per impedire cicli simultanei sullo stesso wallet;
- aggiunto `.env.example` senza segreti;
- documentato il riferimento Railway `DATABASE_URL=${{Postgres.DATABASE_URL}}`;
- mantenuto `main.py` come ciclo operativo originale richiamato dal worker;
- mantenuto `TESTNET=True` senza attivazione automatica della mainnet.

### Test

- aggiunti cinque test specifici per configurazione Railway, worker, lock PostgreSQL, variabili runtime e placeholder dei segreti;
- verifica complessiva: 28 test superati, 0 falliti;
- compilazione Python superata;
- nessun ordine live o testnet inviato.

## 1.0.0-strategy — 2026-07-15

### Strategia

- sostituita la strategia intraday generica con Donchian TSMOM daily a bias long;
- aggiunti volatility targeting, sizing per rischio, regime, funding, spread, volume, mark/oracle, correlazione e drawdown;
- limitata la leva exchange strategica a 2× e l’esposizione lorda indicata a 1,5×;
- introdotti cap più conservativi per SOL;
- vietate nuove aperture short nel prompt strategico.

### Architettura

- decisione finale ancora affidata allo stesso LLM;
- stesso modello OpenAI e stessa funzione `previsione_trading_agent`;
- stesso `HyperLiquidTrader` come unico componente di execution;
- nessuna operazione live eseguita.

### Test

- aggiunta suite pytest offline;
- aggiunti test di non regressione architetturale;
- aggiunta compilazione di tutti i moduli;
- preservato lo script operativo originale fuori dalla raccolta pytest.

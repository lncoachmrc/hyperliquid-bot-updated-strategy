# Trading Agent — Deep Research Strategy Edition

![](/img.jpg)

Questo progetto conserva l’architettura LLM-driven del repository originale di Rizzo AI Academy e sostituisce esclusivamente la logica strategica con una strategia **Donchian trend following / time-series momentum a bias long**, con volatility targeting e filtri prudenziali.

## Architettura invariata

```text
Hyperliquid market data
        ↓
indicators.py / strategy_core.py
        ↓
system_prompt.txt
        ↓
trading_agent.previsione_trading_agent (LLM decision maker)
        ↓
HyperLiquidTrader.execute_signal
        ↓
Hyperliquid testnet/mainnet
```

L’LLM continua a scegliere `open`, `close` o `hold`, il simbolo, la quota, la leva e lo stop. Il codice strategico calcola evidenze e limiti prudenziali; non invia ordini e non introduce un secondo decisore.

## Strategia implementata

- asset: BTC, ETH e SOL, con cap più basso per SOL;
- timeframe strategico: candele giornaliere completate;
- Donchian ensemble: 20, 55 e 120 giorni, canali spostati di una barra;
- filtro di regime: prezzo e MA100 rispetto a MA200;
- volatility target: 18% annualizzato, finestra 30 giorni;
- rischio monetario predefinito: 0,5% per trade;
- stop: 3 ATR su 20 giorni;
- filtri: spread, volume, funding, mark/oracle dislocation, correlazione e drawdown;
- leva exchange massima: 2×;
- esposizione lorda di portafoglio: massimo 1,5×;
- nessuna nuova posizione short.

I parametri sono raccolti in `strategy_config.py`; i calcoli puri e testabili sono in `strategy_core.py`.

## Installazione locale

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python db_init.py
pytest -q
```

Avvio di un singolo ciclo:

```bash
python main.py
```

Avvio come worker persistente:

```bash
python worker.py
```

## Deploy su Railway con PostgreSQL

Il file `railway.json` configura:

- build con **Railpack**;
- inizializzazione/migrazione PostgreSQL tramite `python -u db_init.py` prima del deploy;
- avvio del worker persistente con `python -u worker.py`;
- una sola replica;
- nessuna sovrapposizione tra vecchio e nuovo deploy;
- 300 secondi per terminare correttamente un ciclo durante il redeploy.

Il worker esegue il `main.py` originale in un processo separato ogni 600 secondi. Un advisory lock PostgreSQL impedisce che due repliche o deploy sovrapposti elaborino contemporaneamente lo stesso wallet.

### 1. Crea il progetto Railway

Crea un nuovo progetto da questa repository GitHub e aggiungi nello stesso progetto un servizio **PostgreSQL**.

### 2. Collega PostgreSQL al bot

Nel servizio del bot, sezione **Variables**, crea la variabile:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

`Postgres` deve coincidere con il nome effettivo del servizio database. Usa l’autocompletamento di Railway se il servizio ha un nome differente.

### 3. Inserisci le variabili del bot

```text
PRIVATE_KEY=<Hyperliquid API wallet private key>
WALLET_ADDRESS=<Hyperliquid account address>
OPENAI_API_KEY=<OpenAI API key>
CMC_PRO_API_KEY=<CoinMarketCap API key>

BOT_INTERVAL_SECONDS=600
BOT_RUN_IMMEDIATELY=true
BOT_LOCK_ID=7260315
DB_INIT_ATTEMPTS=30
DB_INIT_RETRY_SECONDS=2
```

Il file `.env.example` contiene soltanto nomi e valori vuoti. Non salvare mai chiavi reali su GitHub. Per i segreti Railway può utilizzare variabili sigillate.

### 4. Deploy

Il primo deploy inizializza automaticamente le tabelle PostgreSQL. Il servizio non espone un server HTTP, quindi non richiede un dominio pubblico o un healthcheck web.

Mantieni **una sola replica**. Il lock PostgreSQL è una protezione aggiuntiva contro esecuzioni duplicate, non un motivo per scalare lo stesso wallet su più worker.

## Modalità operativa

`TESTNET=True` resta impostato in `main.py`, come nella versione verificata. Non è stata abilitata automaticamente la mainnet e non sono stati eseguiti ordini durante la preparazione del deploy.

## Test offline

```bash
pytest -q
python -m compileall .
```

`test_trading.py` è lo script manuale originale: richiede credenziali e può inviare ordini sul testnet configurato. Non viene eseguito da pytest.

## Documentazione di verifica

- `BASELINE_REPORT.md`
- `ARCHITECTURE_INVARIANCE_REPORT.md`
- `MODIFICATION_SCOPE_REPORT.md`
- `STRATEGY_IMPLEMENTATION_MATRIX.md`
- `BUILD_REPORT.md`
- `docs/STRATEGY.md`
- `docs/STRATEGY_MAPPING.md`

## Licenza

MIT. Progetto originale sviluppato da Rizzo AI Academy.

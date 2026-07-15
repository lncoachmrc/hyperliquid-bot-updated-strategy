# Trading Agent — Deep Research Strategy Edition

![](/img.jpg)

Questo progetto conserva l’architettura LLM-driven del repository originale di Rizzo AI Academy e sostituisce esclusivamente la logica strategica con una strategia **Donchian trend following / time-series momentum a bias long**, con volatility targeting e filtri prudenziali.

## Architettura invariata

Il flusso resta:

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

I parametri sono raccolti in `strategy_config.py`. I calcoli puri e testabili sono in `strategy_core.py`.

## Installazione

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Variabili d’ambiente richieste dal progetto originale:

```text
PRIVATE_KEY
WALLET_ADDRESS
OPENAI_API_KEY
DATABASE_URL
CMC_PRO_API_KEY
```

Non inserire chiavi reali nel repository.

## Database

Prima del primo avvio:

```bash
python db_utils.py
```

## Test offline

```bash
pytest -q
python -m compileall .
```

`test_trading.py` è lo script manuale originale: richiede credenziali e può inviare ordini sul testnet configurato. Non viene eseguito da pytest.

## Avvio

```bash
python main.py
```

`TESTNET=True` resta il default nel file `main.py`, come nel repository originale. Non è stata verificata l’esecuzione live e il progetto non garantisce profitti o sicurezza operativa.

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

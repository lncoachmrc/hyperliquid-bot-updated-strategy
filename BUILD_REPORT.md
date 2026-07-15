# Build Report

## Strategia implementata

Donchian trend following / time-series momentum a bias long, su BTC, ETH e SOL prudenziale, con volatility targeting, sizing per rischio, filtri di regime/liquidità/funding/correlazione/drawdown e leva exchange massima 2×.

## Commit sorgente

`ce86f4c717d96ab334c5872ae2eddea59e9f5ff0` (`main`, versione dichiarata nel commit: 0.0.2).

## Comandi eseguiti

```bash
python -m pip install -r requirements.txt
python -m compileall -q .
OPENAI_API_KEY=test python -c "import ..."
pytest -q
python -m pip check
```

## Risultati

| Controllo | Esito |
|---|---|
| Installazione `requirements.txt` | Superata nel runtime |
| Compilazione Python | Superata |
| Import moduli principali senza rete/order | Superato |
| Test pytest offline | 23 superati, 0 falliti |
| Test architetturali | Superati |
| Avvio `main.py` | Non eseguito: richiede segreti, DB e API esterne |
| `test_trading.py` | Non eseguito: può inviare ordini testnet |
| Ordini live/testnet | Nessuno inviato |
| Lint Ruff/Black/Mypy | Non eseguito: tool non presenti nel progetto/runtime |
| `pip check` globale | Due conflitti dell'ambiente preinstallato, non introdotti dal codice: Pillow/MoviePy e cryptography/PyOpenSSL |

## Test coperti

- canali Donchian senza barra corrente;
- long valido;
- regime avverso;
- funding estremo;
- spread estremo;
- alta volatilità;
- deleveraging drawdown;
- rappresentazione 1,5× con leva intera;
- cap SOL;
- asset non autorizzato;
- dati insufficienti;
- ordine LLM → execution;
- assenza di order placement nel modulo strategico;
- modello/funzione LLM invariati;
- API pubblica dell'executor;
- schema output originale con cap 2×;
- policy prompt e assenza di decisore parallelo.

## Problemi preesistenti

- Script di test manuale dipendente da segreti.
- Nessuna suite offline originale.
- Dipendenza operativa da PostgreSQL e molte API.
- `TESTNET=True` hardcoded.
- Nessun paper mode completamente locale.

## Problemi residui

1. Nessun backtest 2022–2026 incluso in questa repository.
2. Nessuna prova end-to-end con account Hyperliquid testnet.
3. Nessuna verifica reale di compatibilità con schema DB già in produzione.
4. Il gross cap di portafoglio resta un vincolo impartito al decisore LLM, non un nuovo blocco deterministico, per preservare l'architettura.
5. Lo stop può subire slippage e non modella ADL o liquidazione.
6. La repo finale è ricostruita dai file letti su GitHub; non include `.git` né prova di identità byte-per-byte dell'intero commit.
7. I default strategici devono essere validati con backtest, walk-forward e paper trading.

## Installazione

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python db_utils.py
pytest -q
```

## Avvio previsto dal progetto

```bash
python main.py
```

Richiede `PRIVATE_KEY`, `WALLET_ADDRESS`, `OPENAI_API_KEY`, `DATABASE_URL` e, per il sentiment, `CMC_PRO_API_KEY`. Verificare sempre che `TESTNET=True` prima di qualsiasi prova operativa.

# Baseline Report

## Fonte analizzata

- Repository: `Rizzo-AI-Academy/rizzo-trading-agent`
- Branch originale: `main`
- Commit di riferimento: `ce86f4c717d96ab334c5872ae2eddea59e9f5ff0`
- Messaggio commit: `- v 0.0.2`
- Linguaggio: Python
- Deploy originale: Railway/Nixpacks, comando `python main.py`

La sorgente è stata letta tramite l'integrazione GitHub. Nell'ambiente di lavoro non era disponibile un clone Git con metadati `.git`; pertanto il pacchetto finale è una ricostruzione completa del contenuto applicativo analizzato, non un clone che conserva la cronologia Git.

## Flusso originale ricostruito

```text
Hyperliquid market data + news + sentiment + forecast
                         ↓
                   indicators.py
                         ↓
                   system_prompt.txt
                         ↓
      trading_agent.previsione_trading_agent (LLM)
                         ↓
             HyperLiquidTrader.execute_signal
                         ↓
                    Hyperliquid
```

## Ruoli originali

| Funzione | Componente originale |
|---|---|
| Raccolta dati di mercato | `indicators.py`, `forecaster.py`, feed esterni |
| Decisione `open/close/hold` | LLM in `trading_agent.py` |
| Scelta simbolo, direzione, quota, leva e stop | LLM tramite schema JSON |
| Validazione strutturale del segnale | `HyperLiquidTrader._validate_order_input` |
| Calcolo della quantità | `HyperLiquidTrader.execute_signal` |
| Impostazione leva e invio ordine | `HyperLiquidTrader` |
| Stop-loss | `HyperLiquidTrader._place_stop_loss` |
| Logging | `db_utils.py` |
| Entry point | `main.py` |

## Strategia originale

La strategia non era isolata in una classe dedicata. Era espressa soprattutto attraverso:

- indicatori a 15 minuti: EMA, MACD, RSI, ATR e pivot;
- funding e open interest;
- forecast Prophet a 15 minuti e un'ora;
- news e Fear & Greed;
- prompt LLM generico, con leva consentita da 1× a 10×;
- stop percentuale tra 1% e 3%.

## Test originali

`test_trading.py` non è una suite unit test isolata. È uno script manuale che:

- richiede `PRIVATE_KEY` e `WALLET_ADDRESS` all'import;
- costruisce il client Hyperliquid;
- può chiamare `execute_signal` su testnet;
- nel commit analizzato contiene un segnale impostato come `close` pur essendo etichettato come test di apertura.

Non è stato eseguito perché avrebbe richiesto credenziali e accesso operativo al testnet. Non è stato modificato per trasformarlo artificialmente in un test verde.

## Problemi preesistenti osservati

1. Mancanza di unit test offline.
2. `TESTNET=True` codificato direttamente in `main.py`.
3. Schema LLM con leva fino a 10×, incompatibile con l'esito prudenziale della ricerca.
4. Strategia molto dipendente da segnali intraday e forecast, senza un unico contratto quantitativo riproducibile.
5. Mancanza di calcolo esplicito del drawdown di portafoglio.
6. Script `test_trading.py` operativo, non adatto alla raccolta automatica pytest.
7. Avvio impossibile senza database e credenziali, per scelta architetturale originale.

## Baseline eseguibile nell'ambiente

- Lettura e analisi statica dei file: completata.
- Compilazione baseline originale: non riprodotta separatamente, perché il repository non era disponibile come clone locale prima della ricostruzione.
- Test originali: non eseguiti per rischio di operazioni testnet e dipendenza da segreti.
- Problemi attribuiti alla modifica: soltanto quelli rilevati dopo la creazione della baseline descritta sopra.

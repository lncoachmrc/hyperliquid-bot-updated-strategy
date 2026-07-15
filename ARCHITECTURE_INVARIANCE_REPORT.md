# Architecture Invariance Report

## Conclusione

La catena decisionale resta **LLM-driven**. Non è stato introdotto un secondo decisore, non è stato cambiato il provider LLM e l'ordine viene ancora inviato esclusivamente da `HyperLiquidTrader.execute_signal`.

| Elemento architetturale | Prima | Dopo | Modificato? | Motivazione |
|---|---|---|---|---|
| Entry point | `main.py` | `main.py` | No | Stesso comando Railway |
| Raccolta dati | moduli separati | stessi moduli + evidenza daily | Solo strategia | Nuovi indicatori richiesti |
| Decisore finale | LLM | LLM | No | Autorità invariata |
| Provider/modello | OpenAI Responses, `gpt-5.1` | uguale | No | Vincolo non negoziabile |
| Contratto output | JSON open/close/hold | stessi campi | Compatibile | Solo cap leva/stop strategici |
| Scelta operazione | LLM | LLM | No | Nessun override deterministico |
| Scelta asset | LLM | LLM, universo originale | No | BTC/ETH/SOL invariati |
| Scelta direzione | LLM | LLM con policy long bias nel prompt | Ruolo No | Strategia cambiata, autorità no |
| Scelta quota | LLM | LLM entro raccomandazione | No | Stesso campo |
| Scelta leva | LLM | LLM, massimo schema 2× | Ruolo No | Limite prudenziale strategico |
| Calcolo size | `HyperLiquidTrader` | `HyperLiquidTrader` | No | Formula di execution preservata |
| Impostazione leva | adapter Hyperliquid | stesso adapter | No | Stesso metodo |
| Invio market order | adapter Hyperliquid | stesso adapter | No | Nessun order sender aggiuntivo |
| Stop-loss | adapter Hyperliquid | stesso adapter | No | Solo distanza proposta cambia |
| Chiusura | `market_close` | `market_close` | No | Invariato |
| Credenziali | `.env` | `.env` | No | Nessun segreto incluso |
| Persistenza | PostgreSQL `db_utils.py` | stesso DB + campo strategia/drawdown | Adattamento minimo | Tracciabilità e fattore drawdown |
| Scheduler/deploy | Railway `python main.py` | uguale | No | `railway.json` invariato nel comportamento |
| Modalità operativa | `TESTNET=True` nel main | uguale | No | Non forzata mainnet |

## Verifiche automatiche

`tests/test_architecture_invariance.py` controlla che:

- la chiamata all'LLM preceda `bot.execute_signal(out)`;
- il modulo strategico non importi OpenAI o l'exchange;
- il modello e la funzione LLM rimangano in `trading_agent.py`;
- l'interfaccia pubblica principale di `HyperLiquidTrader` sia presente;
- il contratto JSON mantenga i campi originali;
- il prompt dichiari esplicitamente l'autorità invariata dell'LLM;
- non venga creato un percorso parallelo di invio ordini.

## Limite della verifica

L'ambiente non ha fornito un clone Git originale, quindi non è disponibile un `git diff` nativo con cronologia e modalità file. Il confronto è stato eseguito contro il commit GitHub indicato, mediante lettura dei file originali e test di flusso/API. Il pacchetto non deve essere interpretato come prova di identità byte-per-byte di tutti i file non strategici; dimostra invece l'invarianza della catena operativa e delle responsabilità osservabili.

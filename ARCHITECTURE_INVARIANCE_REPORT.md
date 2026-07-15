# Architecture Invariance Report

## Conclusione

La catena decisionale resta **LLM-driven**. Non è stato introdotto un secondo decisore, non è stato cambiato il provider LLM e l’ordine viene ancora inviato esclusivamente da `HyperLiquidTrader.execute_signal` dentro il ciclo originale `main.py`.

| Elemento architetturale | Prima | Dopo | Modificato? | Motivazione |
|---|---|---|---|---|
| Ciclo di trading | `python main.py` una volta | `worker.py` esegue `main.py` periodicamente | Solo hosting | Servizio Railway persistente |
| Flusso interno | dati → prompt → LLM → executor | identico | No | `main.py` non è stato riscritto per il deploy |
| Decisore finale | LLM | LLM | No | Autorità invariata |
| Provider/modello | OpenAI Responses, `gpt-5.1` | uguale | No | Nessuna modifica |
| Contratto output | JSON open/close/hold | stessi campi | No | Nessuna modifica di deploy |
| Scelta operazione, asset, direzione, quota e leva | LLM | LLM | No | Il worker non interpreta il mercato |
| Calcolo size | `HyperLiquidTrader` | `HyperLiquidTrader` | No | Formula di execution preservata |
| Invio ordine e stop | adapter Hyperliquid | stesso adapter | No | Nessun order sender aggiuntivo |
| Persistenza | PostgreSQL tramite `db_utils.py` | uguale | No | Schema inizializzato in pre-deploy |
| Protezione da doppia esecuzione | assente | advisory lock PostgreSQL nel worker | Hosting | Impedisce cicli simultanei, non decide trade |
| Modalità operativa | `TESTNET=True` in `main.py` | uguale | No | Il deploy non abilita mainnet |

## Ruolo del worker

`worker.py` svolge esclusivamente funzioni operative:

1. verifica e inizializza PostgreSQL;
2. acquisisce un advisory lock PostgreSQL;
3. avvia `main.py` in un processo Python separato;
4. attende l’intervallo configurato;
5. gestisce l’arresto del servizio Railway.

Non importa né richiama direttamente `previsione_trading_agent`, `execute_signal` o componenti strategici.

## Verifiche automatiche

- la chiamata all’LLM precede `bot.execute_signal(out)` nel main originale;
- il modulo strategico non può inviare ordini;
- il worker esegue il main originale come child process;
- il worker non contiene chiamate all’LLM o all’executor;
- Railway usa una sola replica e nessuna sovrapposizione di deploy;
- il lock PostgreSQL impedisce esecuzioni simultanee accidentali.

## Limite della verifica

Non è stato eseguito un ciclo end-to-end con credenziali, account Hyperliquid testnet e progetto Railway reale. L’invarianza riguarda responsabilità, flusso e interfacce osservabili; non certifica redditività o sicurezza live.

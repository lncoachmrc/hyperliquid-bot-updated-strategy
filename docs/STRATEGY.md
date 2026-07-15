# Strategia implementata

## Sintesi

La nuova logica è una strategia **Donchian trend following / time-series momentum a bias long**, applicata a BTC, ETH e SOL. Il segnale strategico usa esclusivamente candele giornaliere completate; indicatori intraday, news, sentiment e Prophet restano nel workflow come contesto secondario.

## Formula dell'esposizione

Per ogni asset al tempo `t`:

```text
vol_base(t) = vol_target / realized_vol_30d(t)

risk_cap(t) = risk_per_trade / (3 × ATR20(t) / close(t))

raw_exposure(t) = min(
    vol_base(t),
    risk_cap(t),
    asset_cap
)

exposure_before_drawdown(t) = raw_exposure(t)
    × regime_factor(t)
    × funding_factor(t)
    × spread_factor(t)
    × volume_factor(t)
    × dislocation_factor(t)
    × correlation_factor(t)

final_exposure(t) = exposure_before_drawdown(t)
    × portfolio_drawdown_factor(t)
```

La leva exchange Hyperliquid è intera:

```text
se final_exposure <= 1:
    leverage = 1
    portion = final_exposure
altrimenti:
    leverage = 2
    portion = final_exposure / 2
```

L'esposizione rappresentata è `leverage × portion`. Il prompt vieta all'LLM di superare la raccomandazione, i cap per asset e il limite lordo di portafoglio 1,5×.

## Segnale Donchian

I canali sono calcolati con dati fino alla barra precedente (`shift(1)`), evitando che la barra corrente definisca il proprio livello. L'implementazione usa un ensemble sui lookback configurati e produce un punteggio aggregato. I parametri sono volutamente concentrati in `strategy_config.py` perché devono essere validati sull'intera finestra 2022–2026.

## Entrata long

Una nuova posizione long è candidabile soltanto quando:

- asset autorizzato;
- dati giornalieri sufficienti e non stale;
- punteggio Donchian sopra la soglia di entrata;
- regime non avverso;
- funding, spread, volume e dislocazione non attivano uno stop;
- esposizione risultante positiva;
- assenza di una posizione già aperta sullo stesso asset.

L'LLM continua a scegliere l'azione finale nel formato originale.

## Uscita

Il prompt richiede la chiusura di un long esistente quando:

- il punteggio raggiunge la soglia di uscita;
- il regime diventa avverso;
- interviene una sospensione per dati, funding, spread o mark/oracle;
- il drawdown factor azzera l'esposizione.

Non è stato aggiunto un take profit fisso: il vantaggio della famiglia trend following dipende dalla capacità di lasciare correre i trend e uscire per inversione/invalidazione. Lo stop ATR rimane una protezione d'emergenza, non una garanzia di esecuzione.

## Posizioni short

Il campo `short` rimane nello schema per consentire la chiusura di eventuali short già presenti e preservare il contratto originale. Il prompt vieta nuove aperture short nella versione operativa prudenziale.

## Dati Hyperliquid

- OHLCV 1d completato;
- OHLCV 15m legacy;
- funding corrente;
- open interest corrente;
- mark price e oracle price;
- snapshot L2 per spread e depth.

La semplice API corrente non fornisce necessariamente serie storiche complete di funding, OI e profondità. Per questo il bot usa tali dati come filtro contemporaneo; la validazione storica resta compito del backtest separato.

## Condizioni di sospensione

- meno di 221 barre giornaliere completate;
- candela daily più vecchia di 36 ore;
- funding assoluto almeno 0,30% per intervallo;
- spread almeno 20 bps;
- dislocazione mark/oracle almeno 50 bps;
- volume storico non verificabile;
- errore di market data;
- regime avverso;
- drawdown pari o superiore alla soglia hard configurata.

## Limitazioni

- Non è stato eseguito un backtest dentro questa repository.
- Il motore LLM può produrre errori di interpretazione; lo schema limita il formato, non dimostra correttezza economica.
- Il limite lordo aggregato è impartito nel prompt, non imposto da un nuovo risk engine, perché introdurlo avrebbe cambiato l'architettura originale.
- Stop e market order possono subire slippage, gap, ADL, indisponibilità o problemi dell'exchange.
- I parametri richiedono walk-forward e paper trading prima di qualsiasi capitale reale.

# Changelog

## 1.0.0-strategy — 2026-07-15

### Strategia

- sostituita la strategia intraday generica con Donchian TSMOM daily a bias long;
- aggiunti volatility targeting, sizing per rischio, regime, funding, spread, volume, mark/oracle, correlazione e drawdown;
- limitata la leva exchange strategica a 2× e l'esposizione lorda indicata a 1,5×;
- introdotti cap più conservativi per SOL;
- vietate nuove aperture short nel prompt strategico.

### Architettura

- decisione finale ancora affidata allo stesso LLM;
- stesso modello OpenAI e stessa funzione `previsione_trading_agent`;
- stesso `HyperLiquidTrader` come unico componente di execution;
- stesso entry point e stesso comando Railway;
- nessuna operazione live eseguita.

### Test

- aggiunta suite pytest offline;
- aggiunti test di non regressione architetturale;
- aggiunta compilazione di tutti i moduli;
- preservato lo script operativo originale fuori dalla raccolta pytest.

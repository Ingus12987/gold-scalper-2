# BTC + Gold dual demo bot

Two independent 1-minute trend scalp **paper** bots, using public BingX market data. No API key required. **No real orders** are sent.

- BTC/USDT (`BTC/USDT:USDT`) and tokenized gold (XAUT; BingX/CCXT symbol `XAUT/USDT:USDT`)
- Each: 20 USDT paper margin, 20x simulated leverage (400 USDT notional)
- EMA(9/21) crossover, 0.3% price stop, 0.6% price take profit
- One simulated position per instrument; independent cooldowns
- Approximate fee 0.05% per side, no slippage or funding; **not** an exchange demo account or a profitability guarantee
- Logs in `logs/btc_trades.csv` and `logs/gold_trades.csv`

## Run (Termux or Python 3)

```sh
python -m pip install -r requirements.txt
python dual_demo.py
```

Stop with Ctrl+C. Replit/CCXT uses slash-separated symbols; BingX's public REST
API uses `BASE-QUOTE` (for example, CCXT `XAUT/USDT:USDT` maps to REST
`XAUT-USDT`). The paper bot reads public 1-minute swap candles from
`GET https://open-api.bingx.com/openApi/swap/v3/quote/klines`; no API key is
needed. To choose another listed CCXT market:

```sh
python dual_demo.py --gold-symbol 'SYMBOL_FROM_CCXT'
```

Unavailable symbols are reported and skipped; the other bot can still run. Startup stops if neither symbol is available. The program does not place exchange orders. Stops and takes are simulated against candle highs/lows, and gap/slippage risks are not modeled. Use historical backtesting and paper forward-testing before considering live trading.

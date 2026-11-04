# Running on Replit

This is a Python 3.12 console application, not a website. Keep its existing
structure and paper-only behavior; no exchange orders or API keys are needed.

## Run and stop

- Use **Run** to start the **Paper trading bot** workflow.
- The workflow runs `python -u dual_demo.py`. Output appears in the Console;
  there is no web preview or listening port.
- Use **Stop** to stop the bot, or Ctrl+C when running it in the Shell.
- Dependencies are declared in `requirements.txt`.

## Checks

- Unit tests: `python -m unittest -v`
- One live-data pass for both markets: `python -u dual_demo.py --once`
- Polling defaults to 15 seconds; `--poll` must be at least 5.

## Markets and logs

The defaults are BTC `BTC/USDT:USDT` and tokenized gold XAUT `XAUT/USDT:USDT`.
BingX's REST API calls these `BTC-USDT` and `XAUT-USDT`; the bot uses CCXT's
slash-separated unified symbols and the public swap klines endpoint
`/openApi/swap/v3/quote/klines`. If a symbol is unavailable, it is reported and
skipped; if neither is available, the bot exits. To use a different listed
market, pass `--gold-symbol 'EXACT_CCXT_SYMBOL'` or set `GOLD_SYMBOL`.

Completed simulated trades append to `logs/btc_trades.csv` and
`logs/gold_trades.csv`; files are created only when trades close.
Open positions, cooldowns, and cumulative P&L are in memory and reset on restart.
CSV files are ignored by Git. These are local paper simulations, not an exchange
demo account; fees are approximate and slippage/funding are not modeled.

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
- One live-data pass: `python -u dual_demo.py --once`
- Polling defaults to 15 seconds; `--poll` must be at least 5.

## Markets and logs

The default symbols are `BTC/USDT:USDT` and `XAU/USDT:USDT`.
At setup, BingX provided BTC but did not list the default gold symbol.
Unavailable symbols are reported and skipped; if neither exists, the bot exits.
Do not silently substitute a tokenized-gold asset or a different instrument.
To use another confirmed symbol, pass `--gold-symbol 'EXACT_CCXT_SYMBOL'`
or use the existing `GOLD_SYMBOL` environment-variable support.

Completed simulated trades append to `logs/btc_trades.csv` and
`logs/gold_trades.csv`; files are created only when trades close.
Open positions, cooldowns, and cumulative P&L are in memory and reset on restart.
CSV files are ignored by Git. These are local paper simulations, not an exchange
demo account; fees are approximate and slippage/funding are not modeled.

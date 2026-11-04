# Running on Replit

This is a Python 3.12 paper-trading bot with a read-only mobile dashboard. Keep
its existing paper-only behavior; no exchange orders or API keys are needed.

## Run and stop

- Use **Run** to start the **Paper trading bot** workflow.
- The workflow runs `python -u dual_demo.py`, serves the dashboard on port 5000,
  and writes bot status to the Console.
- Use **Stop** to stop the bot, or Ctrl+C when running it in the Shell.
- Dependencies are declared in `requirements.txt`.

## Checks

- Unit tests: `python -m unittest -v`
- One live-data pass for both markets: `python -u dual_demo.py --once`
- Normal public market-data polling defaults to once per minute per market;
  `--poll` must be at least 60 seconds.
- Dashboard GET requests read local persisted state and do not call BingX.

## Markets and logs

The defaults are BTC `BTC/USDT:USDT` and tokenized gold XAUT `XAUT/USDT:USDT`.
BingX's REST API calls these `BTC-USDT` and `XAUT-USDT`; the bot uses CCXT's
slash-separated unified symbols and the public swap klines endpoint
`/openApi/swap/v3/quote/klines`. If a symbol is unavailable, it is reported and
skipped; if neither is available, the bot exits. To use a different listed
market, pass `--gold-symbol 'EXACT_CCXT_SYMBOL'` or set `GOLD_SYMBOL`.

Completed simulated trades are recorded in `logs/btc_trades.csv` and
`logs/gold_trades.csv`; files are created only when trades close.
Each bot also saves its open position, closed trades, cumulative P&L, cooldown,
bar count, and last processed candle atomically in `logs/*_state.json`. On
restart, missed candles available from the market response are replayed for an
open position. If an open position is older than the returned candle history,
that bot stops rather than guessing whether its stop or take was reached.
Existing CSV history is imported if no state file exists; the CSV
is regenerated from state after a close or restart. Invalid or mismatched state
stops the bot rather than silently resetting it. These local files are ignored
by Git and are not an exchange demo account; fees are approximate and
slippage/funding are not modeled.

Transient BingX/rate-limit errors, including `100410`, are retried with
independent per-market exponential backoff (up to five minutes). Stale or
malformed candles do not reach the strategy. The first successful fetch logs the
UTC close time of the newest fresh 1-minute candle. The dashboard marks stale
prices and withholds unrealized PnL when a fresh mark is unavailable.
Use `python -u dual_demo.py --once` to verify both market feeds without starting
the continuous polling loop.

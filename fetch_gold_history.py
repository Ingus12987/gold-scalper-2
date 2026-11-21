"""Download PUBLIC BingX XAUT-USDT 1m candles to CSV; never places orders.

Usage: python fetch_gold_history.py
API availability and instrument support depend on BingX.
"""
import csv
import time
import requests

URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
SYMBOL = "XAUT-USDT"

def download(limit=1000):
    response = requests.get(
        URL, params={"symbol": SYMBOL, "interval": "1m", "limit": limit},
        timeout=25)
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("code")) != "0":
        raise RuntimeError(f"BingX response: {payload.get('code')} {payload.get('msg')}")
    raw = payload.get("data")
    if not isinstance(raw, list) or len(raw) < 31:
        raise ValueError("No usable historical candles returned")
    candles = []
    for item in raw:
        candle = {key: item[key] for key in ("time", "open", "high", "low", "close")}
        candles.append(candle)
    candles.sort(key=lambda x: int(x["time"]))
    unique = {int(c["time"]): c for c in candles}
    return [unique[k] for k in sorted(unique)]

def main():
    candles = download()
    with open("gold_history_1m.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=("time", "open", "high", "low", "close"))
        writer.writeheader()
        writer.writerows(candles)
    print(f"Downloaded {len(candles)} candles for {SYMBOL}; first={candles[0]['time']}, last={candles[-1]['time']}")

if __name__ == "__main__":
    main()

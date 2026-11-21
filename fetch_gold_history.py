"""Download up to 10,000 PUBLIC BingX XAUT-USDT 1m candles. No orders.
Usage: python fetch_gold_history.py
"""
import csv
import time
import requests

URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
SYMBOL = "XAUT-USDT"

def download(pages=10, limit=1000):
    all_candles = {}
    end_time = None
    for page in range(pages):
        params = {"symbol": SYMBOL, "interval": "1m", "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time
        response = requests.get(URL, params=params, timeout=25)
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code")) != "0":
            raise RuntimeError(f"BingX response: {payload.get('code')} {payload.get('msg')}")
        raw = payload.get("data")
        if not isinstance(raw, list) or not raw:
            break
        batch = []
        for item in raw:
            candle = {key: item[key] for key in ("time", "open", "high", "low", "close")}
            batch.append(candle)
            all_candles[int(candle["time"])] = candle
        oldest = min(int(c["time"]) for c in batch)
        if end_time is not None and oldest >= end_time:
            break
        end_time = oldest - 1
        print(f"Page {page + 1}: {len(batch)} candles; total unique={len(all_candles)}", flush=True)
        time.sleep(0.2)
    candles = [all_candles[k] for k in sorted(all_candles)]
    if len(candles) < 31:
        raise ValueError("No usable historical candles returned")
    return candles

def main():
    candles = download()
    with open("gold_history_1m.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=("time", "open", "high", "low", "close"))
        writer.writeheader()
        writer.writerows(candles)
    print(f"Downloaded {len(candles)} candles for {SYMBOL}; first={candles[0]['time']}, last={candles[-1]['time']}")

if __name__ == "__main__":
    main()

"""Download historical PUBLIC BingX XAUT-USDT 1m candles. No orders.
Usage: python fetch_gold_history.py
"""
import csv
import time
import requests

URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
SYMBOL = "XAUT-USDT"

def download(pages=350, limit=1000):
    all_candles = {}
    end_time = None
    for page in range(pages):
        params = {"symbol": SYMBOL, "interval": "1m", "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time
        for attempt in range(6):
            try:
                response = requests.get(URL, params=params, timeout=25)
                response.raise_for_status()
                payload = response.json()
                if str(payload.get("code")) == "0":
                    break
                if str(payload.get("code")) not in ("100410", "100429"):
                    raise RuntimeError(f"BingX response: {payload.get('code')} {payload.get('msg')}")
                raise requests.exceptions.RequestException(f"Temporary BingX error {payload.get('code')}")
            except requests.exceptions.RequestException as exc:
                if attempt == 5:
                    raise RuntimeError(f"BingX unavailable after retries: {exc}") from exc
                delay = min(60, 3 * 2 ** attempt)
                print(f"API issue: {exc}; retry in {delay}s", flush=True)
                time.sleep(delay)
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
        time.sleep(1.5)
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

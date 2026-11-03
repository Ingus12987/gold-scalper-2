import requests
import pandas as pd
import time

URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
SYMBOL = "XAUT-USDT"

def check_signal():
    response = requests.get(
        URL,
        params={
            "symbol": SYMBOL,
            "interval": "1m",
            "limit": 100
        },
        timeout=15
    )
    response.raise_for_status()
    result = response.json()

    if result.get("code") != 0:
        raise RuntimeError(str(result))

    df = pd.DataFrame(result["data"])
    df["time"] = pd.to_numeric(df["time"])
    df["close"] = pd.to_numeric(df["close"])

    df = df.sort_values("time").reset_index(drop=True)

    # Izmantojam tikai noslēgtās sveces
    current_minute = int(time.time() // 60) * 60000
    df = df[df["time"] < current_minute]

    if len(df) < 30:
        print("Nepietiek sveču")
        return

    df["ema9"] = df["close"].ewm(
        span=9, adjust=False
    ).mean()

    df["ema21"] = df["close"].ewm(
        span=21, adjust=False
    ).mean()


    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1/14, adjust=False
    ).mean()
    avg_loss = loss.ewm(
        alpha=1/14, adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["rsi"] = 100 - 100 / (1 + rs)

    df["high"] = pd.to_numeric(df["high"])
    df["low"] = pd.to_numeric(df["low"])

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)

    df["atr"] = tr.ewm(
        alpha=1/14, adjust=False
    ).mean()

    last = df.iloc[-1]
    print("RSI:", round(last["rsi"], 2))
    print("ATR:", round(last["atr"], 4))


    print("ZELTS:", SYMBOL)
    print("Cena:", round(last["close"], 2))
    print("EMA9:", round(last["ema9"], 2))
    print("EMA21:", round(last["ema21"], 2))

    if last["ema9"] > last["ema21"]:
        print("TENDENCE: LONG")
    elif last["ema9"] < last["ema21"]:
        print("TENDENCE: SHORT")
    else:
        print("TENDENCE: NEITRĀLA")


    rsi = last["rsi"]
    ema9 = last["ema9"]
    ema21 = last["ema21"]

    if pd.isna(rsi):
        signal = "GAIDĪT"
    elif ema9 > ema21 and 50 < rsi < 70:
        signal = "LONG"
    elif ema9 < ema21 and 30 < rsi < 50:
        signal = "SHORT"
    else:
        signal = "GAIDĪT"

    print("SIGNĀLS:", signal)
    print("Darījums netiek atvērts.")


check_signal()

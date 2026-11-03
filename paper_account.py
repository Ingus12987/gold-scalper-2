import json
from pathlib import Path
from demo_config import (
    START_BALANCE,
    MARGIN,
    LEVERAGE,
    MAX_POSITIONS,
    MAX_RISK_USDT,
    TAKER_FEE
)

STATE_FILE = Path("paper_state.json")

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())

    return {
        "balance": START_BALANCE,
        "position": None,
        "trades": [],
        "mode": "PAPER_DEMO"
    }

def save_state(state):
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2))
    temp.replace(STATE_FILE)

def show_status():
    state = load_state()
    print("\nGOLD SCALPER DEMO")
    print("Bilance:", round(state["balance"], 4), "USDT")
    print("Pozīcija:", state["position"] or "NAV")
    print("Darījumi:", len(state["trades"]))
    print("Režīms:", state["mode"])

if __name__ == "__main__":
    show_status()

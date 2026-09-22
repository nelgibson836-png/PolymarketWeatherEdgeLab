import json
import os
from datetime import datetime, timezone

import requests

ACTIVE_FILE = "data/active_temperature_markets.json"
OUT_DIR = "data/edge/execution"
LATEST_FILE = os.path.join(OUT_DIR, "latest_clob_books.json")
CLOB_API = "https://clob.polymarket.com"
TIMEOUT = 10
MAX_MARKETS = 200
LEVELS = 10


def safe_json(value):
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    payload = {}
    if os.path.exists(ACTIVE_FILE):
        with open(ACTIVE_FILE, encoding="utf-8") as h:
            payload = json.load(h)

    markets = payload.get("markets", [])
    books = {}
    checked = 0
    nonempty = 0
    errors = 0

    for market in markets[:MAX_MARKETS]:
        token = market.get("yes_token")
        market_id = str(market.get("market_id") or "")
        if not token or not market_id:
            continue
        checked += 1
        try:
            r = requests.get(
                f"{CLOB_API}/book",
                params={"token_id": token},
                timeout=TIMEOUT,
                headers={"User-Agent":"PolymarketWeatherEdgeLab/1.8-research"},
            )
            r.raise_for_status()
            book = r.json()
            bids = (book.get("bids") or [])[:LEVELS]
            asks = (book.get("asks") or [])[:LEVELS]
            if bids or asks:
                nonempty += 1
            books[market_id] = {
                "market_id": market_id,
                "token_id": token,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "bids": bids,
                "asks": asks,
                "best_bid": float(bids[0]["price"]) if bids else None,
                "best_ask": float(asks[0]["price"]) if asks else None,
                "depth_ask_at_best": float(asks[0]["size"]) if asks else 0.0,
                "source": "Polymarket CLOB /book",
                "status": "NONEMPTY" if (bids or asks) else "EMPTY",
            }
        except Exception as exc:
            errors += 1
            books[market_id] = {
                "market_id": market_id,
                "token_id": token,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "bids": [],
                "asks": [],
                "best_bid": None,
                "best_ask": None,
                "depth_ask_at_best": 0.0,
                "source": "Polymarket CLOB /book",
                "status": "ERROR",
                "error": str(exc),
            }

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Polymarket CLOB /book",
        "checked_markets": checked,
        "nonempty_books": nonempty,
        "errors": errors,
        "markets": books,
    }
    tmp = LATEST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as h:
        json.dump(output, h, indent=2)
    os.replace(tmp, LATEST_FILE)
    print(json.dumps({k: output[k] for k in ("generated_at","checked_markets","nonempty_books","errors")}, indent=2))


if __name__ == "__main__":
    main()

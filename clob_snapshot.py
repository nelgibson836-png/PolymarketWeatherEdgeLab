import json
import os
from datetime import datetime, timezone

from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

ACTIVE_FILE = "data/active_temperature_markets.json"
OUT_DIR = "data/edge/execution"
LATEST_FILE = os.path.join(OUT_DIR, "latest_clob_books.json")
CLOB_API = "https://clob.polymarket.com"
TIMEOUT = 10
MAX_MARKETS = 500
MAX_WORKERS = 12
LEVELS = 10
PRIORITY_MAX_ASK = 0.60


def safe_json(value):
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def select_markets(markets, max_markets=MAX_MARKETS):
    """Prioritize markets most likely to contain tradeable low-price edges."""
    eligible = [
        m for m in markets
        if m.get("yes_token") and str(m.get("market_id") or "")
    ]
    priority = []
    remainder = []
    for market in eligible:
        try:
            ask = float(market.get("best_ask"))
        except (TypeError, ValueError):
            ask = None
        if ask is not None and 0.0 < ask <= PRIORITY_MAX_ASK:
            priority.append((ask, str(market.get("market_date") or ""), market))
        else:
            remainder.append(market)
    priority.sort(key=lambda x: (x[0], x[1]))
    selected = [item[2] for item in priority[:max_markets]]
    if len(selected) < max_markets:
        selected.extend(remainder[: max_markets - len(selected)])
    return selected


def fetch_book(market):
    token = market.get("yes_token")
    market_id = str(market.get("market_id") or "")
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
        return market_id, {
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
        }, None
    except Exception as exc:
        return market_id, {
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
        }, str(exc)


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

    selected = select_markets(markets)
    checked = len(selected)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(fetch_book, market) for market in selected]
        for future in as_completed(futures):
            market_id, book, error = future.result()
            books[market_id] = book
            if error:
                errors += 1
            elif book["status"] == "NONEMPTY":
                nonempty += 1

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

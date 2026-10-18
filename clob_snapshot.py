import json
import math
import os
from datetime import datetime, timezone

from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

ACTIVE_FILE = "data/active_temperature_markets.json"
OUT_DIR = "data/edge/execution"
LATEST_FILE = os.path.join(OUT_DIR, "latest_clob_books.json")
SIGNALS_FILE = os.path.join("data", "edge", "latest_signals_v18.json")
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


def select_markets(markets, max_markets=MAX_MARKETS, priority_market_ids=None):
    """Select current signal markets first, then low-ask candidates."""
    if max_markets <= 0:
        return []

    eligible = [
        m for m in markets
        if isinstance(m, dict)
        and m.get("yes_token")
        and str(m.get("market_id") or "")
    ]
    by_id = {str(m.get("market_id")): m for m in eligible}
    selected = []
    seen = set()

    for market_id in priority_market_ids or []:
        key = str(market_id or "")
        market = by_id.get(key)
        if market is not None and key not in seen:
            selected.append(market)
            seen.add(key)
            if len(selected) >= max_markets:
                return selected[:max_markets]

    priority = []
    remainder = []
    for market in eligible:
        market_id = str(market.get("market_id") or "")
        if market_id in seen:
            continue
        try:
            ask = float(market.get("best_ask"))
        except (TypeError, ValueError):
            ask = None
        if ask is not None and math.isfinite(ask) and 0.0 < ask <= PRIORITY_MAX_ASK:
            priority.append((ask, str(market.get("market_date") or ""), market))
        else:
            remainder.append(market)

    priority.sort(key=lambda x: (x[0], x[1]))
    selected.extend([item[2] for item in priority[:max_markets - len(selected)]])
    if len(selected) < max_markets:
        selected.extend(remainder[:max_markets - len(selected)])
    return selected


def normalize_levels(levels, descending=False):
    """Validate and sort CLOB levels before selecting the best prices."""
    normalized = []
    for level in levels or []:
        if not isinstance(level, dict):
            continue
        try:
            price = float(level["price"])
            size = float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not math.isfinite(price)
            or not math.isfinite(size)
            or not (0.0 < price <= 1.0)
            or size <= 0.0
        ):
            continue
        normalized.append(level)

    normalized.sort(key=lambda level: float(level["price"]), reverse=descending)
    return normalized


def fetch_book(market):
    token = market.get("yes_token")
    market_id = str(market.get("market_id") or "")
    try:
        r = requests.get(
            f"{CLOB_API}/book",
            params={"token_id": token},
            timeout=TIMEOUT,
            headers={"User-Agent": "PolymarketWeatherEdgeLab/1.8-research"},
        )
        r.raise_for_status()
        book = r.json()
        bids = normalize_levels(book.get("bids"), descending=True)[:LEVELS]
        asks = normalize_levels(book.get("asks"), descending=False)[:LEVELS]
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


def load_priority_signal_ids():
    """Return IDs from the last V1.8 signal snapshot, if it is readable."""
    if not os.path.exists(SIGNALS_FILE):
        return []
    try:
        with open(SIGNALS_FILE, encoding="utf-8") as h:
            previous_signals = json.load(h)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: could not load previous V1.8 signal IDs: {exc}")
        return []

    if not isinstance(previous_signals, dict):
        return []
    signals = previous_signals.get("signals", [])
    if not isinstance(signals, list):
        return []

    return [
        str(signal["market_id"])
        for signal in signals
        if isinstance(signal, dict)
        and signal.get("market_id")
        and signal.get("signal") in ("PAPER_BUY_V18", "POSSIBLE_EDGE_V18")
    ]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    payload = {}
    if os.path.exists(ACTIVE_FILE):
        with open(ACTIVE_FILE, encoding="utf-8") as h:
            payload = json.load(h)

    markets = payload.get("markets", []) if isinstance(payload, dict) else []
    priority_market_ids = load_priority_signal_ids()
    selected = select_markets(markets, priority_market_ids=priority_market_ids)

    books = {}
    checked = len(selected)
    nonempty = 0
    errors = 0

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
    print(json.dumps({
        k: output[k]
        for k in ("generated_at", "checked_markets", "nonempty_books", "errors")
    }, indent=2))


if __name__ == "__main__":
    main()

import csv
import json
import os
import re
import time
from datetime import datetime, timezone

import requests


# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Temperature Collector V7.0
# ============================================================

API_BASE = "https://gamma-api.polymarket.com"
TAG_ID = 84
TAG_SLUG = "weather"

DATA_DIR = "data"
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")

LATEST_FILE = os.path.join(DATA_DIR, "temperature_markets_latest.json")
HISTORY_FILE = os.path.join(DATA_DIR, "temperature_markets.csv")

REQUEST_TIMEOUT = 30
PAGE_SIZE = 100

HEADERS = {
    "User-Agent": "PolymarketWeatherEdgeLab/7.0"
}


# ============================================================
# UTILIDADES
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def safe_json(value):
    if value is None:
        return None

    if isinstance(value, (list, dict)):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None

    return None


def request_json(url, params=None):
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()
    return response.json()


def ensure_directories():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)


# ============================================================
# DESCARGA DE EVENTOS
# ============================================================

def get_weather_events():
    print("Consultando eventos Weather...")

    events = []
    offset = 0

    while True:
        params = {
            "tag_id": TAG_ID,
            "limit": PAGE_SIZE,
            "offset": offset
        }

        try:
            batch = request_json(
                f"{API_BASE}/events",
                params=params
            )
        except Exception as e:
            print(f"ERROR descargando eventos: {e}")
            break

        if not batch:
            break

        events.extend(batch)

        print(f"  Eventos descargados: {len(events)}")

        if len(batch) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(0.15)

    return events


# ============================================================
# DETECCIÓN DE CIUDAD
# ============================================================

TEMPERATURE_PATTERNS = [
    r"highest temperature in (.+?) on",
    r"highest temperature in (.+?) be",
    r"temperature in (.+?) on",
    r"temperature in (.+?) be",
]


def extract_city(question):
    if not question:
        return None

    for pattern in TEMPERATURE_PATTERNS:
        match = re.search(pattern, question, re.IGNORECASE)

        if match:
            city = match.group(1).strip()

            city = re.sub(
                r"\s+on\s+.*$",
                "",
                city,
                flags=re.IGNORECASE
            )

            city = re.sub(
                r"\s+be$",
                "",
                city,
                flags=re.IGNORECASE
            )

            return city.strip()

    return None


# ============================================================
# DETECCIÓN DE FECHA
# ============================================================

def extract_market_date(market):
    end_date = market.get("endDateIso")

    if end_date:
        return end_date

    end_date = market.get("endDate")

    if end_date:
        try:
            return end_date[:10]
        except Exception:
            pass

    return None


# ============================================================
# DETECCIÓN DE TIPO
# ============================================================

def detect_market_type(question):
    if not question:
        return "unknown"

    q = question.lower()

    if "highest temperature" in q:
        return "highest_temperature"

    if "lowest temperature" in q:
        return "lowest_temperature"

    if "temperature" in q:
        return "temperature"

    return "unknown"


# ============================================================
# FILTRO DE TEMPERATURA
# ============================================================

def is_temperature_market(question):
    if not question:
        return False

    q = question.lower()

    return (
        "temperature" in q
        and (
            "°c" in q
            or "°f" in q
            or "degrees" in q
        )
    )


# ============================================================
# NORMALIZACIÓN DE MERCADO
# ============================================================

def normalize_market(event, market):
    question = market.get("question", "")

    outcomes = safe_json(market.get("outcomes"))
    prices = safe_json(market.get("outcomePrices"))
    tokens = safe_json(market.get("clobTokenIds"))

    if not isinstance(outcomes, list):
        outcomes = None

    if not isinstance(prices, list):
        prices = None

    if not isinstance(tokens, list):
        tokens = None

    yes_price = None
    no_price = None

    if prices and len(prices) >= 2:
        yes_price = safe_float(prices[0])
        no_price = safe_float(prices[1])

    yes_token = None
    no_token = None

    if tokens and len(tokens) >= 2:
        yes_token = tokens[0]
        no_token = tokens[1]

    best_bid = safe_float(market.get("bestBid"))
    best_ask = safe_float(market.get("bestAsk"))

    spread = None

    if best_bid is not None and best_ask is not None:
        spread = best_ask - best_bid

    event_title = event.get("title")
    event_slug = event.get("slug")

    city = extract_city(question)

    record = {
        "collected_at": utc_iso(),

        "event_id": str(event.get("id")) if event.get("id") else None,
        "event_title": event_title,
        "event_slug": event_slug,

        "market_id": str(market.get("id")) if market.get("id") else None,

        "city": city,
        "market_date": extract_market_date(market),

        "market_type": detect_market_type(question),

        "question": question,
        "slug": market.get("slug"),

        "group_title": market.get("groupItemTitle"),
        "group_threshold": safe_float(
            market.get("groupItemThreshold")
        ),

        "outcomes": outcomes,

        "yes_price": yes_price,
        "no_price": no_price,

        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,

        "yes_token": yes_token,
        "no_token": no_token,

        "volume": safe_float(market.get("volume")),
        "volume_24h": safe_float(market.get("volume24hr")),

        "liquidity": safe_float(market.get("liquidity")),
        "liquidity_clob": safe_float(
            market.get("liquidityClob")
        ),

        "active": bool(market.get("active")),
        "closed": bool(market.get("closed")),
        "accepting_orders": bool(
            market.get("acceptingOrders")
        ),
        "enable_order_book": bool(
            market.get("enableOrderBook")
        ),

        "approved": bool(market.get("approved")),
        "archived": bool(market.get("archived")),

        "resolution_source": market.get(
            "resolutionSource"
        ),

        "start_date": market.get("startDate"),
        "end_date": market.get("endDate"),

        "condition_id": market.get("conditionId"),

        "order_min_size": safe_float(
            market.get("orderMinSize")
        ),

        "tick_size": safe_float(
            market.get("orderPriceMinTickSize")
        ),

        "spread_pct": (
            spread / best_ask
            if spread is not None
            and best_ask
            and best_ask > 0
            else None
        ),
    }

    return record


# ============================================================
# CLASIFICACIÓN DE MERCADO ACTIVO
# ============================================================

def is_future_market(record):
    market_date = record.get("market_date")

    if not market_date:
        return False

    try:
        today = utc_now().date()
        target = datetime.strptime(
            market_date,
            "%Y-%m-%d"
        ).date()

        return target >= today

    except Exception:
        return False


def has_valid_prices(record):
    return (
        record.get("yes_price") is not None
        and record.get("no_price") is not None
    )


def has_valid_tokens(record):
    return (
        bool(record.get("yes_token"))
        and bool(record.get("no_token"))
    )


def is_current_candidate(record):
    return (
        record.get("active") is True
        and record.get("closed") is False
        and record.get("accepting_orders") is True
        and record.get("enable_order_book") is True
        and record.get("city") is not None
        and is_future_market(record)
        and has_valid_prices(record)
        and has_valid_tokens(record)
    )


# ============================================================
# EXTRACCIÓN DE MERCADOS
# ============================================================

def collect_temperature_markets(events):

    all_markets = []

    for event in events:

        markets = event.get("markets", [])

        if not markets:
            continue

        for market in markets:

            question = market.get("question", "")

            if not is_temperature_market(question):
                continue

            record = normalize_market(
                event,
                market
            )

            all_markets.append(record)

    return all_markets


# ============================================================
# ESTADÍSTICAS
# ============================================================

def calculate_stats(markets):

    cities = sorted({
        m["city"]
        for m in markets
        if m.get("city")
    })

    with_prices = [
        m for m in markets
        if has_valid_prices(m)
    ]

    with_tokens = [
        m for m in markets
        if has_valid_tokens(m)
    ]

    with_bid_ask = [
        m for m in markets
        if (
            m.get("best_bid") is not None
            and m.get("best_ask") is not None
        )
    ]

    active = [
        m for m in markets
        if is_current_candidate(m)
    ]

    return {
        "temperature_markets": len(markets),
        "cities": len(cities),
        "markets_with_prices": len(with_prices),
        "markets_with_tokens": len(with_tokens),
        "markets_with_bid_ask": len(with_bid_ask),
        "active_candidates": len(active),
        "city_names": cities,
    }


# ============================================================
# CSV HISTÓRICO
# ============================================================

CSV_FIELDS = [
    "collected_at",
    "event_id",
    "event_title",
    "event_slug",
    "market_id",
    "city",
    "market_date",
    "market_type",
    "question",
    "slug",
    "group_title",
    "group_threshold",
    "yes_price",
    "no_price",
    "best_bid",
    "best_ask",
    "spread",
    "spread_pct",
    "yes_token",
    "no_token",
    "volume",
    "volume_24h",
    "liquidity",
    "liquidity_clob",
    "active",
    "closed",
    "accepting_orders",
    "enable_order_book",
    "approved",
    "archived",
    "resolution_source",
    "start_date",
    "end_date",
    "condition_id",
    "order_min_size",
    "tick_size",
]


def append_history(markets):

    exists = os.path.exists(HISTORY_FILE)

    with open(
        HISTORY_FILE,
        "a",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=CSV_FIELDS,
            extrasaction="ignore"
        )

        if not exists:
            writer.writeheader()

        for market in markets:
            writer.writerow(market)


# ============================================================
# GUARDADO JSON
# ============================================================

def save_latest(markets, stats):

    payload = {
        "collector_version": "7.0",
        "collected_at": utc_iso(),

        "source": {
            "exchange": "Polymarket",
            "api": API_BASE,
            "tag_id": TAG_ID,
            "tag_slug": TAG_SLUG,
        },

        "stats": stats,

        "markets": markets,
    }

    with open(
        LATEST_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            payload,
            f,
            indent=2,
            ensure_ascii=False
        )


def save_snapshot(markets, stats):

    now = utc_now()

    day_dir = os.path.join(
        SNAPSHOT_DIR,
        now.strftime("%Y-%m-%d")
    )

    os.makedirs(day_dir, exist_ok=True)

    filename = now.strftime(
        "%H%M%S"
    ) + ".json"

    filepath = os.path.join(
        day_dir,
        filename
    )

    payload = {
        "collector_version": "7.0",
        "collected_at": utc_iso(),

        "stats": stats,

        "markets": markets,
    }

    with open(
        filepath,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            payload,
            f,
            indent=2,
            ensure_ascii=False
        )

    return filepath


# ============================================================
# VALIDACIÓN INTELIGENTE
# ============================================================

def validation_sort_key(market):

    volume = market.get("volume") or 0
    liquidity = market.get("liquidity") or 0

    return (
        volume,
        liquidity
    )


def print_validation(markets):

    candidates = [
        m for m in markets
        if is_current_candidate(m)
    ]

    candidates.sort(
        key=validation_sort_key,
        reverse=True
    )

    print()
    print("=" * 60)
    print("VALIDATION")
    print("=" * 60)

    if not candidates:
        print("No se encontraron mercados activos válidos.")
        return

    for market in candidates[:10]:

        print()
        print(
            f"{market['city']} | "
            f"{market['market_date']} | "
            f"{market['group_title']}"
        )

        print(
            f"  Question: {market['question']}"
        )

        print(
            f"  YES: {market['yes_price']} | "
            f"NO: {market['no_price']}"
        )

        print(
            f"  Bid: {market['best_bid']} | "
            f"Ask: {market['best_ask']} | "
            f"Spread: {market['spread']}"
        )

        print(
            f"  Volume: {market['volume']} | "
            f"Liquidity: {market['liquidity']}"
        )

        print(
            "  YES token: "
            f"{str(market['yes_token'])[:20]}..."
        )

        print(
            "  NO token: "
            f"{str(market['no_token'])[:20]}..."
        )

        print(
            f"  Active: {market['active']} | "
            f"Accepting: {market['accepting_orders']}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("Polymarket Weather Edge Lab")
    print("Temperature Collector V7.0")
    print("=" * 60)

    print(
        f"UTC: {utc_iso()}"
    )

    ensure_directories()

    print(
        f"Weather tag: {TAG_ID} ({TAG_SLUG})"
    )

    events = get_weather_events()

    print()
    print(
        f"Weather events: {len(events)}"
    )

    print("=" * 60)

    markets = collect_temperature_markets(
        events
    )

    stats = calculate_stats(
        markets
    )

    print()
    print("=" * 60)
    print("COLLECTION COMPLETE")
    print("=" * 60)

    print(
        f"Events: {len(events)}"
    )

    print(
        f"Temperature markets: "
        f"{stats['temperature_markets']}"
    )

    print(
        f"Cities: "
        f"{stats['cities']}"
    )

    print(
        f"Markets with prices: "
        f"{stats['markets_with_prices']}"
    )

    print(
        f"Markets with YES/NO tokens: "
        f"{stats['markets_with_tokens']}"
    )

    print(
        f"Markets with bid/ask: "
        f"{stats['markets_with_bid_ask']}"
    )

    print(
        f"Active candidates: "
        f"{stats['active_candidates']}"
    )

    save_latest(
        markets,
        stats
    )

    snapshot = save_snapshot(
        markets,
        stats
    )

    append_history(
        markets
    )

    print()
    print(
        f"Latest: {LATEST_FILE}"
    )

    print(
        f"Snapshot: {snapshot}"
    )

    print(
        f"History: {HISTORY_FILE}"
    )

    print_validation(
        markets
    )

    print()
    print("=" * 60)
    print("COLLECTOR V7 COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()

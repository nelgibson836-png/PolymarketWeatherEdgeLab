import csv
import json
import os
import re
import time
from datetime import datetime, timezone

import requests


# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Temperature Collector V8.0
# ============================================================

API_BASE = "https://gamma-api.polymarket.com"

TAG_ID = 84
TAG_SLUG = "weather"

DATA_DIR = "data"
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")

LATEST_FILE = os.path.join(
    DATA_DIR,
    "temperature_markets_latest.json"
)

ACTIVE_FILE = os.path.join(
    DATA_DIR,
    "active_temperature_markets.json"
)

HISTORY_FILE = os.path.join(
    DATA_DIR,
    "temperature_market_history.csv"
)

REQUEST_TIMEOUT = 30

EVENT_PAGE_SIZE = 100

MAX_EVENT_PAGES = 20

HEADERS = {
    "User-Agent": "PolymarketWeatherEdgeLab/8.0"
}


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


# ============================================================
# SAFE CONVERSIONS
# ============================================================

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


# ============================================================
# HTTP
# ============================================================

def request_json(url, params=None):

    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# DIRECTORIES
# ============================================================

def ensure_directories():

    os.makedirs(DATA_DIR, exist_ok=True)

    os.makedirs(
        SNAPSHOT_DIR,
        exist_ok=True
    )


# ============================================================
# EVENTS
# ============================================================

def get_weather_events():

    print("Consultando eventos Weather activos...")

    events = []

    offset = 0

    for page in range(MAX_EVENT_PAGES):

        params = {
            "tag_id": TAG_ID,
            "limit": EVENT_PAGE_SIZE,
            "offset": offset,
            "active": "true",
            "closed": "false"
        }

        try:

            batch = request_json(
                f"{API_BASE}/events",
                params=params
            )

        except Exception as e:

            print(
                f"  Fin de paginación: {e}"
            )

            break

        if not batch:

            break

        events.extend(batch)

        print(
            f"  Eventos descargados: "
            f"{len(events)}"
        )

        if len(batch) < EVENT_PAGE_SIZE:

            break

        offset += EVENT_PAGE_SIZE

        time.sleep(0.15)

    return events


# ============================================================
# CITY
# ============================================================

def extract_city(question):

    if not question:
        return None

    patterns = [

        r"highest temperature in (.+?) on",

        r"lowest temperature in (.+?) on",

        r"temperature in (.+?) on",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            question,
            re.IGNORECASE
        )

        if match:

            city = match.group(1).strip()

            city = re.sub(
                r"\s+on\s+.*$",
                "",
                city,
                flags=re.IGNORECASE
            )

            return city.strip()

    return None


# ============================================================
# DATE
# ============================================================

def extract_date(market):

    value = market.get(
        "endDateIso"
    )

    if value:

        return str(value)[:10]

    value = market.get(
        "endDate"
    )

    if value:

        return str(value)[:10]

    return None


# ============================================================
# MARKET TYPE
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
# TEMPERATURE FILTER
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
# DATE VALIDATION
# ============================================================

def is_future_date(date_string):

    if not date_string:
        return False

    try:

        target = datetime.strptime(
            date_string,
            "%Y-%m-%d"
        ).date()

        return target >= utc_now().date()

    except Exception:

        return False


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_market(
    event,
    market
):

    question = market.get(
        "question",
        ""
    )

    outcomes = safe_json(
        market.get("outcomes")
    )

    prices = safe_json(
        market.get("outcomePrices")
    )

    tokens = safe_json(
        market.get("clobTokenIds")
    )

    yes_price = None
    no_price = None

    if (
        isinstance(prices, list)
        and len(prices) >= 2
    ):

        yes_price = safe_float(
            prices[0]
        )

        no_price = safe_float(
            prices[1]
        )

    yes_token = None
    no_token = None

    if (
        isinstance(tokens, list)
        and len(tokens) >= 2
    ):

        yes_token = tokens[0]
        no_token = tokens[1]

    best_bid = safe_float(
        market.get("bestBid")
    )

    best_ask = safe_float(
        market.get("bestAsk")
    )

    spread = None

    if (
        best_bid is not None
        and best_ask is not None
    ):

        spread = (
            best_ask
            - best_bid
        )

    market_date = extract_date(
        market
    )

    return {

        "collected_at": utc_iso(),

        "event_id": str(
            event.get("id")
        ),

        "event_title": event.get(
            "title"
        ),

        "event_slug": event.get(
            "slug"
        ),

        "market_id": str(
            market.get("id")
        ),

        "city": extract_city(
            question
        ),

        "market_date": market_date,

        "market_type":
            detect_market_type(
                question
            ),

        "question": question,

        "slug": market.get(
            "slug"
        ),

        "group_title":
            market.get(
                "groupItemTitle"
            ),

        "group_threshold":
            safe_float(
                market.get(
                    "groupItemThreshold"
                )
            ),

        "yes_price": yes_price,

        "no_price": no_price,

        "best_bid": best_bid,

        "best_ask": best_ask,

        "spread": spread,

        "yes_token": yes_token,

        "no_token": no_token,

        "volume":
            safe_float(
                market.get(
                    "volume"
                )
            ),

        "volume_24h":
            safe_float(
                market.get(
                    "volume24hr"
                )
            ),

        "liquidity":
            safe_float(
                market.get(
                    "liquidity"
                )
            ),

        "liquidity_clob":
            safe_float(
                market.get(
                    "liquidityClob"
                )
            ),

        "active":
            bool(
                market.get(
                    "active"
                )
            ),

        "closed":
            bool(
                market.get(
                    "closed"
                )
            ),

        "accepting_orders":
            bool(
                market.get(
                    "acceptingOrders"
                )
            ),

        "enable_order_book":
            bool(
                market.get(
                    "enableOrderBook"
                )
            ),

        "approved":
            bool(
                market.get(
                    "approved"
                )
            ),

        "archived":
            bool(
                market.get(
                    "archived"
                )
            ),

        "resolution_source":
            market.get(
                "resolutionSource"
            ),

        "start_date":
            market.get(
                "startDate"
            ),

        "end_date":
            market.get(
                "endDate"
            ),

        "condition_id":
            market.get(
                "conditionId"
            ),

        "order_min_size":
            safe_float(
                market.get(
                    "orderMinSize"
                )
            ),

        "tick_size":
            safe_float(
                market.get(
                    "orderPriceMinTickSize"
                )
            ),
    }


# ============================================================
# ACTIVE MARKET
# ============================================================

def is_active_candidate(
    market
):

    return (

        market.get(
            "active"
        ) is True

        and market.get(
            "closed"
        ) is False

        and market.get(
            "accepting_orders"
        ) is True

        and market.get(
            "enable_order_book"
        ) is True

        and market.get(
            "approved"
        ) is True

        and market.get(
            "city"
        ) is not None

        and is_future_date(
            market.get(
                "market_date"
            )
        )

        and market.get(
            "yes_price"
        ) is not None

        and market.get(
            "no_price"
        ) is not None

        and market.get(
            "yes_token"
        )

        and market.get(
            "no_token"
        )
    )


# ============================================================
# COLLECT
# ============================================================

def collect_markets(events):

    markets = []

    for event in events:

        event_markets = (
            event.get(
                "markets"
            )
            or []
        )

        for market in event_markets:

            question = market.get(
                "question",
                ""
            )

            if not is_temperature_market(
                question
            ):

                continue

            normalized = normalize_market(
                event,
                market
            )

            markets.append(
                normalized
            )

    return markets


# ============================================================
# STATS
# ============================================================

def calculate_stats(
    markets
):

    cities = sorted(
        {
            m.get("city")
            for m in markets
            if m.get("city")
        }
    )

    prices = [
        m for m in markets
        if (
            m.get("yes_price")
            is not None
            and
            m.get("no_price")
            is not None
        )
    ]

    tokens = [
        m for m in markets
        if (
            m.get("yes_token")
            and
            m.get("no_token")
        )
    ]

    bidask = [
        m for m in markets
        if (
            m.get("best_bid")
            is not None
            and
            m.get("best_ask")
            is not None
        )
    ]

    active = [
        m for m in markets
        if is_active_candidate(m)
    ]

    return {

        "temperature_markets":
            len(markets),

        "cities":
            len(cities),

        "markets_with_prices":
            len(prices),

        "markets_with_tokens":
            len(tokens),

        "markets_with_bid_ask":
            len(bidask),

        "active_candidates":
            len(active),

        "cities_list":
            cities,
    }


# ============================================================
# JSON
# ============================================================

def save_json(
    filepath,
    payload
):

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


# ============================================================
# LATEST
# ============================================================

def save_latest(
    markets,
    stats
):

    payload = {

        "collector_version":
            "8.0",

        "collected_at":
            utc_iso(),

        "source": {

            "platform":
                "Polymarket",

            "api":
                API_BASE,

            "tag_id":
                TAG_ID,

            "tag":
                TAG_SLUG,
        },

        "stats":
            stats,

        "markets":
            markets,
    }

    save_json(
        LATEST_FILE,
        payload
    )


# ============================================================
# ACTIVE FILE
# ============================================================

def save_active(
    markets,
    stats
):

    active = [
        m for m in markets
        if is_active_candidate(m)
    ]

    payload = {

        "collector_version":
            "8.0",

        "collected_at":
            utc_iso(),

        "count":
            len(active),

        "markets":
            active,
    }

    save_json(
        ACTIVE_FILE,
        payload
    )


# ============================================================
# SNAPSHOT
# ============================================================

def save_snapshot(
    markets,
    stats
):

    now = utc_now()

    day_dir = os.path.join(
        SNAPSHOT_DIR,
        now.strftime(
            "%Y-%m-%d"
        )
    )

    os.makedirs(
        day_dir,
        exist_ok=True
    )

    filename = (
        now.strftime(
            "%H%M%S"
        )
        + ".json"
    )

    filepath = os.path.join(
        day_dir,
        filename
    )

    active = [
        m for m in markets
        if is_active_candidate(m)
    ]

    payload = {

        "collector_version":
            "8.0",

        "collected_at":
            utc_iso(),

        "stats":
            stats,

        "active_markets":
            active,
    }

    save_json(
        filepath,
        payload
    )

    return filepath


# ============================================================
# HISTORY
# ============================================================

HISTORY_FIELDS = [

    "collected_at",

    "event_id",

    "market_id",

    "city",

    "market_date",

    "market_type",

    "question",

    "group_title",

    "group_threshold",

    "yes_price",

    "no_price",

    "best_bid",

    "best_ask",

    "spread",

    "volume",

    "volume_24h",

    "liquidity",

    "active",

    "closed",

    "accepting_orders",

    "enable_order_book",

    "condition_id",
]


def append_history(
    markets
):

    active = [
        m for m in markets
        if is_active_candidate(m)
    ]

    if not active:

        return

    exists = os.path.exists(
        HISTORY_FILE
    )

    with open(
        HISTORY_FILE,
        "a",
        newline="",
        encoding="utf-8"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=HISTORY_FIELDS
        )

        if not exists:

            writer.writeheader()

        for market in active:

            writer.writerow(
                {
                    field:
                    market.get(field)
                    for field
                    in HISTORY_FIELDS
                }
            )


# ============================================================
# VALIDATION
# ============================================================

def print_validation(
    markets
):

    active = [
        m for m in markets
        if is_active_candidate(m)
    ]

    active.sort(
        key=lambda m:
        (
            m.get(
                "volume_24h"
            )
            or 0
        ),
        reverse=True
    )

    print()

    print(
        "=" * 60
    )

    print(
        "VALIDATION"
    )

    print(
        "=" * 60
    )

    if not active:

        print(
            "No se encontraron mercados activos válidos."
        )

        return

    for market in active[:10]:

        print()

        print(
            f"{market['city']} | "
            f"{market['market_date']} | "
            f"{market['group_title']}"
        )

        print(
            f"  Market ID: "
            f"{market['market_id']}"
        )

        print(
            f"  YES: "
            f"{market['yes_price']} | "
            f"NO: "
            f"{market['no_price']}"
        )

        print(
            f"  Bid: "
            f"{market['best_bid']} | "
            f"Ask: "
            f"{market['best_ask']} | "
            f"Spread: "
            f"{market['spread']}"
        )

        print(
            f"  Volume 24h: "
            f"{market['volume_24h']}"
        )

        print(
            f"  Liquidity: "
            f"{market['liquidity']}"
        )

        print(
            f"  YES token: "
            f"{str(market['yes_token'])[:24]}..."
        )

        print(
            f"  NO token: "
            f"{str(market['no_token'])[:24]}..."
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        "Polymarket Weather Edge Lab"
    )

    print(
        "Temperature Collector V8.0"
    )

    print("=" * 60)

    print(
        f"UTC: {utc_iso()}"
    )

    print(
        f"Weather tag: "
        f"{TAG_ID} ({TAG_SLUG})"
    )

    ensure_directories()

    events = get_weather_events()

    print()

    print(
        f"Weather events: "
        f"{len(events)}"
    )

    print(
        "=" * 60
    )

    markets = collect_markets(
        events
    )

    stats = calculate_stats(
        markets
    )

    print()

    print(
        "=" * 60
    )

    print(
        "COLLECTION COMPLETE"
    )

    print(
        "=" * 60
    )

    print(
        f"Events: "
        f"{len(events)}"
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

    save_active(
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
        f"Latest: "
        f"{LATEST_FILE}"
    )

    print(
        f"Active: "
        f"{ACTIVE_FILE}"
    )

    print(
        f"Snapshot: "
        f"{snapshot}"
    )

    print(
        f"History: "
        f"{HISTORY_FILE}"
    )

    print_validation(
        markets
    )

    print()

    print(
        "=" * 60
    )

    print(
        "COLLECTOR V8 COMPLETE"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()

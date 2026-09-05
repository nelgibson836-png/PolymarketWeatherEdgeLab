import csv
import json
import os
import re
import time
from datetime import datetime, timezone, date

import requests


# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Temperature Collector V9.0
#
# Objetivo:
#   - Recolectar mercados meteorológicos de Polymarket
#   - Normalizar ciudad, estación, fecha, unidad y bucket
#   - Crear event_key para agrupar mercados relacionados
#   - Mantener snapshots históricos
#   - Mantener historial compacto por mes
#
# IMPORTANTE:
#   Este collector NO necesita API keys.
# ============================================================


API_BASE = "https://gamma-api.polymarket.com"

TAG_ID = 84
TAG_SLUG = "weather"

DATA_DIR = "data"

SNAPSHOT_DIR = os.path.join(
    DATA_DIR,
    "snapshots"
)

HISTORY_DIR = os.path.join(
    DATA_DIR,
    "history"
)

LATEST_FILE = os.path.join(
    DATA_DIR,
    "temperature_markets_latest.json"
)

ACTIVE_FILE = os.path.join(
    DATA_DIR,
    "active_temperature_markets.json"
)

LEGACY_HISTORY_FILE = os.path.join(
    DATA_DIR,
    "temperature_market_history.csv"
)


COLLECTOR_VERSION = "9.0"
SCHEMA_VERSION = "9.0"

REQUEST_TIMEOUT = 30

EVENT_PAGE_SIZE = 100

MAX_EVENT_PAGES = 20

HEADERS = {
    "User-Agent": (
        "PolymarketWeatherEdgeLab/"
        + COLLECTOR_VERSION
    )
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


def safe_int(value):

    try:

        if value is None or value == "":
            return None

        return int(float(value))

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

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    os.makedirs(
        SNAPSHOT_DIR,
        exist_ok=True
    )

    os.makedirs(
        HISTORY_DIR,
        exist_ok=True
    )


# ============================================================
# EVENTS
# ============================================================

def get_weather_events():

    print(
        "Consultando eventos Weather activos..."
    )

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
                "  Fin de paginación: "
                f"{e}"
            )

            break

        if not batch:
            break

        events.extend(batch)

        print(
            "  Eventos descargados: "
            f"{len(events)}"
        )

        if len(batch) < EVENT_PAGE_SIZE:
            break

        offset += EVENT_PAGE_SIZE

        time.sleep(0.15)

    return events


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(value):

    if value is None:
        return ""

    value = str(value)

    value = value.replace(
        "\u00a0",
        " "
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


# ============================================================
# CITY
# ============================================================

def clean_city(city):

    if not city:
        return None

    city = normalize_text(city)

    # Elimina residuos comunes que pueden quedar
    # después de una extracción imperfecta.

    city = re.sub(
        r"\s+be\s+.*$",
        "",
        city,
        flags=re.IGNORECASE
    )

    city = re.sub(
        r"\s+on\s+.*$",
        "",
        city,
        flags=re.IGNORECASE
    )

    city = re.sub(
        r"\s+(?:at|for)\s+.*$",
        "",
        city,
        flags=re.IGNORECASE
    )

    city = city.strip(
        " ,.-"
    )

    if not city:
        return None

    return city


def extract_city(question):

    if not question:
        return None

    question = normalize_text(
        question
    )

    patterns = [

        # Ejemplo:
        # Will the highest temperature in London be 13°C...
        r"temperature\s+in\s+(.+?)\s+be\s+",
        
        # Ejemplo:
        # Will the highest temperature in London on...
        r"temperature\s+in\s+(.+?)\s+on\s+",

        # Variante sin "be"
        r"temperature\s+in\s+(.+?)(?:\s+on\s+|$)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            question,
            flags=re.IGNORECASE
        )

        if match:

            city = clean_city(
                match.group(1)
            )

            if city:
                return city

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
# TEMPERATURE UNIT
# ============================================================

def detect_temperature_unit(
    question,
    group_title=None
):

    text = " ".join(
        [
            normalize_text(question),
            normalize_text(group_title)
        ]
    ).lower()

    if "°c" in text:
        return "C"

    if "°f" in text:
        return "F"

    if "degrees c" in text:
        return "C"

    if "degrees f" in text:
        return "F"

    return None


# ============================================================
# TEMPERATURE VALUES
# ============================================================

def extract_temperature_values(text):

    if not text:
        return []

    text = normalize_text(
        text
    )

    values = []

    pattern = re.compile(
        r"(-?\d+(?:\.\d+)?)\s*°?\s*([CF])",
        flags=re.IGNORECASE
    )

    for match in pattern.finditer(text):

        value = safe_float(
            match.group(1)
        )

        unit = match.group(2).upper()

        if value is not None:

            values.append(
                {
                    "value": value,
                    "unit": unit
                }
            )

    return values


# ============================================================
# BUCKET TYPE
# ============================================================

def detect_bucket_type(
    question,
    group_title
):

    text = " ".join(
        [
            normalize_text(question),
            normalize_text(group_title)
        ]
    )

    lower = text.lower()

    if (
        "or higher" in lower
        or "or above" in lower
        or "at least" in lower
        or "higher than or equal" in lower
        or "≥" in text
    ):

        return "or_higher"

    if (
        "or below" in lower
        or "or lower" in lower
        or "at most" in lower
        or "lower than or equal" in lower
        or "≤" in text
    ):

        return "or_lower"

    if (
        "between" in lower
        or re.search(
            r"\d+\s*(?:°[CF])?\s*(?:-|–|—|to)\s*\d+",
            text,
            flags=re.IGNORECASE
        )
    ):

        return "range"

    return "exact"


# ============================================================
# BUCKET VALUES
# ============================================================

def parse_bucket(
    question,
    group_title
):

    combined = " ".join(
        [
            normalize_text(group_title),
            normalize_text(question)
        ]
    )

    unit = detect_temperature_unit(
        question,
        group_title
    )

    bucket_type = detect_bucket_type(
        question,
        group_title
    )

    values = extract_temperature_values(
        combined
    )

    numeric_values = [
        item["value"]
        for item in values
        if (
            unit is None
            or item["unit"] == unit
        )
    ]

    # --------------------------------------------------------
    # Rango
    # --------------------------------------------------------

    if bucket_type == "range":

        if len(numeric_values) >= 2:

            low = numeric_values[0]
            high = numeric_values[1]

            if low > high:

                low, high = high, low

            return {
                "bucket_type": "range",
                "bucket_value": None,
                "bucket_low": low,
                "bucket_high": high
            }

    # --------------------------------------------------------
    # Higher
    # --------------------------------------------------------

    if bucket_type == "or_higher":

        if numeric_values:

            value = numeric_values[0]

            return {
                "bucket_type": "or_higher",
                "bucket_value": value,
                "bucket_low": value,
                "bucket_high": None
            }

    # --------------------------------------------------------
    # Lower
    # --------------------------------------------------------

    if bucket_type == "or_lower":

        if numeric_values:

            value = numeric_values[0]

            return {
                "bucket_type": "or_lower",
                "bucket_value": value,
                "bucket_low": None,
                "bucket_high": value
            }

    # --------------------------------------------------------
    # Exact
    # --------------------------------------------------------

    if numeric_values:

        value = numeric_values[0]

        return {
            "bucket_type": "exact",
            "bucket_value": value,
            "bucket_low": value,
            "bucket_high": value
        }

    # --------------------------------------------------------
    # Unknown
    # --------------------------------------------------------

    return {
        "bucket_type": "unknown",
        "bucket_value": None,
        "bucket_low": None,
        "bucket_high": None
    }


# ============================================================
# RESOLUTION SOURCE
# ============================================================

def extract_resolution_station(
    resolution_source
):

    if not resolution_source:
        return None

    source = str(
        resolution_source
    )

    # Principalmente:
    # https://www.weather.gov/wrh/timeseries?site=rpll

    patterns = [

        r"[?&]site=([A-Za-z0-9_-]+)",

        r"[?&]station=([A-Za-z0-9_-]+)",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            source,
            flags=re.IGNORECASE
        )

        if match:

            return match.group(1).upper()

    return None


def detect_resolution_provider(
    resolution_source
):

    if not resolution_source:
        return None

    source = str(
        resolution_source
    ).lower()

    if "weather.gov" in source:

        return "NOAA_NWS"

    if "wunderground.com" in source:

        return "WEATHER_UNDERGROUND"

    if "weather.com" in source:

        return "WEATHER_COM"

    return "OTHER"


# ============================================================
# DATE
# ============================================================

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def extract_iso_date_from_value(
    value
):

    if not value:
        return None

    text = str(
        value
    )

    match = re.search(
        r"(20\d{2}-\d{2}-\d{2})",
        text
    )

    if match:
        return match.group(1)

    return None


def extract_date_from_question(
    question,
    reference_date=None
):

    if not question:
        return None

    question = normalize_text(
        question
    )

    # Ejemplo:
    # on August 30
    # on September 5, 2026

    pattern = re.compile(
        r"\bon\s+"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+"
        r"(\d{1,2})"
        r"(?:,\s*(20\d{2}))?",
        flags=re.IGNORECASE
    )

    match = pattern.search(
        question
    )

    if not match:
        return None

    month_name = (
        match.group(1).lower()
    )

    month = MONTHS.get(
        month_name
    )

    day = safe_int(
        match.group(2)
    )

    year_text = match.group(3)

    if not month or not day:
        return None

    if year_text:

        year = safe_int(
            year_text
        )

    elif reference_date:

        try:

            ref = datetime.strptime(
                reference_date,
                "%Y-%m-%d"
            ).date()

            year = ref.year

        except Exception:

            year = utc_now().year

    else:

        year = utc_now().year

    try:

        candidate = date(
            year,
            month,
            day
        )

    except ValueError:

        return None

    # Si el año no está escrito y la fecha queda
    # demasiado lejos del reference_date, probamos
    # el año anterior/siguiente.

    if (
        not year_text
        and reference_date
    ):

        try:

            ref = datetime.strptime(
                reference_date,
                "%Y-%m-%d"
            ).date()

            candidates = [

                candidate,

                date(
                    year - 1,
                    month,
                    day
                ),

                date(
                    year + 1,
                    month,
                    day
                ),
            ]

            candidate = min(
                candidates,
                key=lambda d:
                abs(
                    (d - ref).days
                )
            )

        except Exception:

            pass

    return candidate.isoformat()


def extract_date(market):

    question = market.get(
        "question"
    )

    end_date = extract_iso_date_from_value(
        market.get("endDateIso")
    )

    if end_date is None:

        end_date = extract_iso_date_from_value(
            market.get("endDate")
        )

    question_date = extract_date_from_question(
        question,
        reference_date=end_date
    )

    if question_date:
        return question_date

    return end_date


# ============================================================
# DATE VALIDATION
# ============================================================

def is_future_date(
    date_string
):

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
# EVENT KEY
# ============================================================

def make_event_key(
    city,
    market_date,
    market_type,
    unit,
    station
):

    values = [

        city or "unknown",

        market_date or "unknown",

        market_type or "unknown",

        unit or "unknown",

        station or "unknown",

    ]

    normalized = []

    for value in values:

        value = normalize_text(
            value
        ).lower()

        value = re.sub(
            r"[^a-z0-9]+",
            "_",
            value
        )

        value = value.strip(
            "_"
        )

        normalized.append(
            value or "unknown"
        )

    return "|".join(
        normalized
    )


# ============================================================
# PRICE DATA
# ============================================================

def extract_prices(
    market
):

    prices = safe_json(
        market.get(
            "outcomePrices"
        )
    )

    yes_price = None
    no_price = None

    outcomes = safe_json(
        market.get(
            "outcomes"
        )
    )

    if (
        isinstance(prices, list)
        and len(prices) >= 2
    ):

        if (
            isinstance(outcomes, list)
            and len(outcomes) >= 2
        ):

            outcome_map = {}

            for index, outcome in enumerate(
                outcomes
            ):

                if index >= len(prices):
                    break

                outcome_name = str(
                    outcome
                ).lower()

                outcome_map[
                    outcome_name
                ] = safe_float(
                    prices[index]
                )

            yes_price = outcome_map.get(
                "yes"
            )

            no_price = outcome_map.get(
                "no"
            )

        else:

            yes_price = safe_float(
                prices[0]
            )

            no_price = safe_float(
                prices[1]
            )

    return (
        yes_price,
        no_price
    )


# ============================================================
# TOKENS
# ============================================================

def extract_tokens(
    market
):

    tokens = safe_json(
        market.get(
            "clobTokenIds"
        )
    )

    yes_token = None
    no_token = None

    if (
        isinstance(tokens, list)
        and len(tokens) >= 2
    ):

        yes_token = tokens[0]
        no_token = tokens[1]

    return (
        yes_token,
        no_token
    )


# ============================================================
# BID / ASK
# ============================================================

def extract_bid_ask(
    market
):

    best_bid = safe_float(
        market.get(
            "bestBid"
        )
    )

    best_ask = safe_float(
        market.get(
            "bestAsk"
        )
    )

    # Nunca inventamos 0/1.
    #
    # Si Polymarket no entrega bid/ask,
    # permanecen como None.

    spread = None

    if (
        best_bid is not None
        and best_ask is not None
        and best_ask >= best_bid
    ):

        spread = (
            best_ask
            - best_bid
        )

    return (
        best_bid,
        best_ask,
        spread
    )


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_market(
    event,
    market
):

    collected_at = utc_iso()

    question = normalize_text(
        market.get(
            "question",
            ""
        )
    )

    group_title = normalize_text(
        market.get(
            "groupItemTitle"
        )
    )

    market_date = extract_date(
        market
    )

    market_type = detect_market_type(
        question
    )

    temperature_unit = detect_temperature_unit(
        question,
        group_title
    )

    city = extract_city(
        question
    )

    resolution_source = market.get(
        "resolutionSource"
    )

    resolution_station = (
        extract_resolution_station(
            resolution_source
        )
    )

    resolution_provider = (
        detect_resolution_provider(
            resolution_source
        )
    )

    bucket = parse_bucket(
        question,
        group_title
    )

    event_key = make_event_key(
        city=city,
        market_date=market_date,
        market_type=market_type,
        unit=temperature_unit,
        station=resolution_station
    )

    yes_price, no_price = extract_prices(
        market
    )

    yes_token, no_token = extract_tokens(
        market
    )

    best_bid, best_ask, spread = extract_bid_ask(
        market
    )

    return {

        # ----------------------------------------------------
        # Metadata
        # ----------------------------------------------------

        "schema_version":
            SCHEMA_VERSION,

        "collector_version":
            COLLECTOR_VERSION,

        "collected_at":
            collected_at,

        # ----------------------------------------------------
        # Event
        # ----------------------------------------------------

        "event_id":
            str(
                event.get("id")
            )
            if event.get("id") is not None
            else None,

        "event_title":
            event.get("title"),

        "event_slug":
            event.get("slug"),

        "event_key":
            event_key,

        # ----------------------------------------------------
        # Market
        # ----------------------------------------------------

        "market_id":
            str(
                market.get("id")
            )
            if market.get("id") is not None
            else None,

        "city":
            city,

        "market_date":
            market_date,

        "market_type":
            market_type,

        "temperature_unit":
            temperature_unit,

        "question":
            question,

        "slug":
            market.get("slug"),

        # ----------------------------------------------------
        # Resolution
        # ----------------------------------------------------

        "resolution_source":
            resolution_source,

        "resolution_provider":
            resolution_provider,

        "resolution_station":
            resolution_station,

        # ----------------------------------------------------
        # Bucket
        # ----------------------------------------------------

        "group_title":
            group_title,

        "group_threshold":
            safe_float(
                market.get(
                    "groupItemThreshold"
                )
            ),

        "bucket_type":
            bucket.get(
                "bucket_type"
            ),

        "bucket_value":
            bucket.get(
                "bucket_value"
            ),

        "bucket_low":
            bucket.get(
                "bucket_low"
            ),

        "bucket_high":
            bucket.get(
                "bucket_high"
            ),

        # ----------------------------------------------------
        # Prices
        # ----------------------------------------------------

        "yes_price":
            yes_price,

        "no_price":
            no_price,

        "best_bid":
            best_bid,

        "best_ask":
            best_ask,

        "spread":
            spread,

        # ----------------------------------------------------
        # Tokens
        # ----------------------------------------------------

        "yes_token":
            yes_token,

        "no_token":
            no_token,

        # ----------------------------------------------------
        # Volume / liquidity
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Market state
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Dates
        # ----------------------------------------------------

        "start_date":
            market.get(
                "startDate"
            ),

        "end_date":
            market.get(
                "endDate"
            ),

        # ----------------------------------------------------
        # Condition / trading rules
        # ----------------------------------------------------

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

        and market.get(
            "market_date"
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

def collect_markets(
    events
):

    markets = []

    seen_market_ids = set()

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

            market_id = market.get(
                "id"
            )

            if market_id is not None:

                market_id_string = str(
                    market_id
                )

                if market_id_string in seen_market_ids:

                    continue

                seen_market_ids.add(
                    market_id_string
                )

            normalized = normalize_market(
                event,
                market
            )

            markets.append(
                normalized
            )

    return markets


# ============================================================
# TEMPERATURE FILTER
# ============================================================

def is_temperature_market(
    question
):

    if not question:
        return False

    q = str(
        question
    ).lower()

    if "temperature" not in q:
        return False

    return (

        "°c" in q

        or "°f" in q

        or "degrees c" in q

        or "degrees f" in q
    )


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

    stations = sorted(
        {
            m.get(
                "resolution_station"
            )
            for m in markets
            if m.get(
                "resolution_station"
            )
        }
    )

    event_keys = sorted(
        {
            m.get(
                "event_key"
            )
            for m in markets
            if m.get(
                "event_key"
            )
        }
    )

    units = sorted(
        {
            m.get(
                "temperature_unit"
            )
            for m in markets
            if m.get(
                "temperature_unit"
            )
        }
    )

    bucket_types = sorted(
        {
            m.get(
                "bucket_type"
            )
            for m in markets
            if m.get(
                "bucket_type"
            )
        }
    )

    prices = [

        m

        for m in markets

        if (
            m.get(
                "yes_price"
            ) is not None

            and m.get(
                "no_price"
            ) is not None
        )
    ]

    tokens = [

        m

        for m in markets

        if (
            m.get(
                "yes_token"
            )

            and m.get(
                "no_token"
            )
        )
    ]

    bidask = [

        m

        for m in markets

        if (
            m.get(
                "best_bid"
            ) is not None

            and m.get(
                "best_ask"
            ) is not None
        )
    ]

    active = [

        m

        for m in markets

        if is_active_candidate(
            m
        )
    ]

    markets_with_event_key = [

        m

        for m in markets

        if m.get(
            "event_key"
        )
    ]

    return {

        "temperature_markets":
            len(markets),

        "cities":
            len(cities),

        "stations":
            len(stations),

        "event_keys":
            len(event_keys),

        "markets_with_prices":
            len(prices),

        "markets_with_tokens":
            len(tokens),

        "markets_with_bid_ask":
            len(bidask),

        "markets_with_event_key":
            len(markets_with_event_key),

        "active_candidates":
            len(active),

        "cities_list":
            cities,

        "stations_list":
            stations,

        "units":
            units,

        "bucket_types":
            bucket_types,
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
            COLLECTOR_VERSION,

        "schema_version":
            SCHEMA_VERSION,

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

        m

        for m in markets

        if is_active_candidate(
            m
        )
    ]

    payload = {

        "collector_version":
            COLLECTOR_VERSION,

        "schema_version":
            SCHEMA_VERSION,

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

        m

        for m in markets

        if is_active_candidate(
            m
        )
    ]

    payload = {

        "collector_version":
            COLLECTOR_VERSION,

        "schema_version":
            SCHEMA_VERSION,

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
# COMPACT HISTORY
# ============================================================

HISTORY_FIELDS = [

    "collected_at",

    "event_key",

    "event_id",

    "market_id",

    "city",

    "resolution_station",

    "resolution_provider",

    "market_date",

    "market_type",

    "temperature_unit",

    "bucket_type",

    "bucket_value",

    "bucket_low",

    "bucket_high",

    "group_title",

    "yes_price",

    "no_price",

    "best_bid",

    "best_ask",

    "spread",

    "volume_24h",

    "liquidity",

    "active",

    "accepting_orders",

    "condition_id",
]


def get_month_history_file():

    return os.path.join(
        HISTORY_DIR,
        utc_now().strftime(
            "%Y-%m"
        )
        + ".csv"
    )


def append_history(
    markets
):

    active = [

        m

        for m in markets

        if is_active_candidate(
            m
        )
    ]

    if not active:
        return None

    history_file = get_month_history_file()

    exists = os.path.exists(
        history_file
    )

    with open(
        history_file,
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
                        market.get(
                            field
                        )
                    for field
                    in HISTORY_FIELDS
                }
            )

    return history_file


# ============================================================
# LEGACY HISTORY NOTICE
# ============================================================

def report_legacy_history():

    if os.path.exists(
        LEGACY_HISTORY_FILE
    ):

        try:

            size = os.path.getsize(
                LEGACY_HISTORY_FILE
            )

            size_mb = (
                size
                / (
                    1024
                    * 1024
                )
            )

            print()
            print(
                "HISTORIAL LEGACY DETECTADO"
            )

            print(
                f"  Archivo: "
                f"{LEGACY_HISTORY_FILE}"
            )

            print(
                f"  Tamaño: "
                f"{size_mb:.2f} MB"
            )

            print(
                "  V9 NO lo modifica."
            )

            print(
                "  El nuevo historial se "
                "guardará por mes en:"
            )

            print(
                f"  {HISTORY_DIR}/"
            )

        except Exception:
            pass


# ============================================================
# VALIDATION
# ============================================================

def print_validation(
    markets
):

    active = [

        m

        for m in markets

        if is_active_candidate(
            m
        )
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
        "=" * 70
    )
    print(
        "VALIDATION V9"
    )
    print(
        "=" * 70
    )

    if not active:

        print(
            "No se encontraron "
            "mercados activos válidos."
        )

        return

    for market in active[:10]:

        print()

        print(
            f"{market.get('city')} | "
            f"{market.get('resolution_station')} | "
            f"{market.get('market_date')}"
        )

        print(
            f"  Type: "
            f"{market.get('market_type')}"
        )

        print(
            f"  Unit: "
            f"{market.get('temperature_unit')}"
        )

        print(
            f"  Bucket: "
            f"{market.get('bucket_type')}"
        )

        print(
            f"  Value: "
            f"{market.get('bucket_value')}"
        )

        print(
            f"  Range: "
            f"{market.get('bucket_low')} "
            f"-> "
            f"{market.get('bucket_high')}"
        )

        print(
            f"  Group: "
            f"{market.get('group_title')}"
        )

        print(
            f"  Event key: "
            f"{market.get('event_key')}"
        )

        print(
            f"  Market ID: "
            f"{market.get('market_id')}"
        )

        print(
            f"  YES: "
            f"{market.get('yes_price')} | "
            f"NO: "
            f"{market.get('no_price')}"
        )

        print(
            f"  Bid: "
            f"{market.get('best_bid')} | "
            f"Ask: "
            f"{market.get('best_ask')} | "
            f"Spread: "
            f"{market.get('spread')}"
        )

        print(
            f"  Volume 24h: "
            f"{market.get('volume_24h')}"
        )

        print(
            f"  Liquidity: "
            f"{market.get('liquidity')}"
        )

        print(
            f"  Resolution: "
            f"{market.get('resolution_source')}"
        )

        print(
            f"  YES token: "
            f"{str(market.get('yes_token'))[:24]}..."
        )

        print(
            f"  NO token: "
            f"{str(market.get('no_token'))[:24]}..."
        )


# ============================================================
# DATA QUALITY CHECKS
# ============================================================

def calculate_quality_checks(
    markets
):

    checks = {

        "markets_total":
            len(markets),

        "missing_city":
            0,

        "missing_market_date":
            0,

        "missing_unit":
            0,

        "missing_station":
            0,

        "missing_event_key":
            0,

        "unknown_bucket":
            0,

        "missing_prices":
            0,

        "missing_tokens":
            0,

        "missing_bid_ask":
            0,
    }

    for market in markets:

        if not market.get(
            "city"
        ):

            checks[
                "missing_city"
            ] += 1

        if not market.get(
            "market_date"
        ):

            checks[
                "missing_market_date"
            ] += 1

        if not market.get(
            "temperature_unit"
        ):

            checks[
                "missing_unit"
            ] += 1

        if not market.get(
            "resolution_station"
        ):

            checks[
                "missing_station"
            ] += 1

        if not market.get(
            "event_key"
        ):

            checks[
                "missing_event_key"
            ] += 1

        if market.get(
            "bucket_type"
        ) == "unknown":

            checks[
                "unknown_bucket"
            ] += 1

        if (
            market.get(
                "yes_price"
            ) is None

            or market.get(
                "no_price"
            ) is None
        ):

            checks[
                "missing_prices"
            ] += 1

        if (
            not market.get(
                "yes_token"
            )

            or not market.get(
                "no_token"
            )
        ):

            checks[
                "missing_tokens"
            ] += 1

        if (
            market.get(
                "best_bid"
            ) is None

            or market.get(
                "best_ask"
            ) is None
        ):

            checks[
                "missing_bid_ask"
            ] += 1

    return checks


def print_quality_checks(
    markets
):

    checks = calculate_quality_checks(
        markets
    )

    print()
    print(
        "=" * 70
    )
    print(
        "DATA QUALITY"
    )
    print(
        "=" * 70
    )

    for key, value in checks.items():

        print(
            f"{key}: {value}"
        )


# ============================================================
# EVENT FAMILY STATISTICS
# ============================================================

def calculate_event_family_stats(
    markets
):

    families = {}

    for market in markets:

        key = market.get(
            "event_key"
        )

        if not key:
            continue

        if key not in families:

            families[key] = 0

        families[key] += 1

    counts = list(
        families.values()
    )

    if not counts:

        return {

            "event_families":
                0,

            "max_markets_per_family":
                0,

            "avg_markets_per_family":
                0,
        }

    return {

        "event_families":
            len(families),

        "max_markets_per_family":
            max(counts),

        "avg_markets_per_family":
            round(
                sum(counts)
                / len(counts),
                2
            ),
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 70
    )

    print(
        "Polymarket Weather Edge Lab"
    )

    print(
        f"Temperature Collector V{COLLECTOR_VERSION}"
    )

    print(
        "=" * 70
    )

    print(
        f"UTC: {utc_iso()}"
    )

    print(
        f"Weather tag: "
        f"{TAG_ID} ({TAG_SLUG})"
    )

    ensure_directories()

    report_legacy_history()

    # --------------------------------------------------------
    # EVENTS
    # --------------------------------------------------------

    events = get_weather_events()

    print()

    print(
        f"Weather events: "
        f"{len(events)}"
    )

    print(
        "=" * 70
    )

    # --------------------------------------------------------
    # MARKETS
    # --------------------------------------------------------

    markets = collect_markets(
        events
    )

    stats = calculate_stats(
        markets
    )

    family_stats = calculate_event_family_stats(
        markets
    )

    stats[
        "event_family_stats"
    ] = family_stats

    quality = calculate_quality_checks(
        markets
    )

    stats[
        "data_quality"
    ] = quality

    # --------------------------------------------------------
    # COLLECTION SUMMARY
    # --------------------------------------------------------

    print()

    print(
        "=" * 70
    )

    print(
        "COLLECTION COMPLETE"
    )

    print(
        "=" * 70
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
        f"Stations: "
        f"{stats['stations']}"
    )

    print(
        f"Event keys: "
        f"{stats['event_keys']}"
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

    print()

    print(
        "Units: "
        f"{', '.join(stats['units'])}"
    )

    print(
        "Bucket types: "
        f"{', '.join(stats['bucket_types'])}"
    )

    print()

    print(
        "Event families: "
        f"{family_stats['event_families']}"
    )

    print(
        "Max markets/family: "
        f"{family_stats['max_markets_per_family']}"
    )

    print(
        "Avg markets/family: "
        f"{family_stats['avg_markets_per_family']}"
    )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

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

    history_file = append_history(
        markets
    )

    # --------------------------------------------------------
    # OUTPUT PATHS
    # --------------------------------------------------------

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
        f"{history_file}"
    )

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    print_quality_checks(
        markets
    )

    print_validation(
        markets
    )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    print()

    print(
        "=" * 70
    )

    print(
        "COLLECTOR V9 COMPLETE"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()

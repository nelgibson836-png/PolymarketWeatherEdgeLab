import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import requests


# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Temperature + Weather Collector V10.2.1
# ============================================================

COLLECTOR_VERSION = "10.2.1"
SCHEMA_VERSION = "10.2.1"

POLYMARKET_API = "https://gamma-api.polymarket.com"
WEATHER_TAG_ID = 84
WEATHER_TAG_SLUG = "weather"

AWC_API = "https://aviationweather.gov/api/data"

OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ECMWF_API = "https://api.open-meteo.com/v1/ecmwf"
OPEN_METEO_GFS_API = "https://api.open-meteo.com/v1/gfs"

DATA_DIR = "data"

SNAPSHOT_DIR = os.path.join(
    DATA_DIR,
    "snapshots",
)

HISTORY_DIR = os.path.join(
    DATA_DIR,
    "history",
)

WEATHER_DIR = os.path.join(
    DATA_DIR,
    "weather",
)

WEATHER_HISTORY_DIR = os.path.join(
    WEATHER_DIR,
    "history",
)

STATION_REGISTRY_FILE = os.path.join(
    WEATHER_DIR,
    "station_registry.json",
)

WEATHER_LATEST_FILE = os.path.join(
    WEATHER_DIR,
    "latest.json",
)

WEATHER_OBS_FILE = os.path.join(
    WEATHER_HISTORY_DIR,
    "observations.csv",
)

WEATHER_FCST_FILE = os.path.join(
    WEATHER_HISTORY_DIR,
    "forecasts.csv",
)

LATEST_FILE = os.path.join(
    DATA_DIR,
    "temperature_markets_latest.json",
)

ACTIVE_FILE = os.path.join(
    DATA_DIR,
    "active_temperature_markets.json",
)

LEGACY_HISTORY_FILE = os.path.join(
    DATA_DIR,
    "temperature_market_history.csv",
)

REQUEST_TIMEOUT = 15

EVENT_PAGE_SIZE = 100
MAX_EVENT_PAGES = 20

OPEN_METEO_BATCH_SIZE = 40

HEADERS = {
    "User-Agent": (
        "PolymarketWeatherEdgeLab/10.2.1 "
        "(research project)"
    ),
    "Accept": "application/json",
}


# ============================================================
# CSV SCHEMAS
# ============================================================

MARKET_HISTORY_FIELDS = [
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

OBS_FIELDS = [
    "collected_at",
    "station",
    "observation_time",
    "temperature_c",
    "dewpoint_c",
    "wind_speed_kt",
    "raw_text",
    "source",
]

FCST_FIELDS = [
    "collected_at",
    "station",
    "market_date",
    "model",
    "timezone",
    "forecast_target",
    "temperature_max_c",
    "temperature_min_c",
    "forecast_url",
]


GLOBALS = {
    "events_count": 0,
}


# ============================================================
# BASIC HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def utc_date_string():
    return utc_now().strftime("%Y-%m-%d")


def safe_float(value):
    try:
        if value in (None, ""):
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


def clean_text(value):
    if value is None:
        return None

    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


def normalize_name(value):
    value = (
        value or ""
    ).lower().strip()

    value = value.replace(
        "&",
        "and",
    )

    value = re.sub(
        r"[^a-z0-9]+",
        "_",
        value,
    )

    return (
        value.strip("_")
        or "unknown"
    )


def ensure_directories():
    directories = [
        DATA_DIR,
        SNAPSHOT_DIR,
        HISTORY_DIR,
        WEATHER_DIR,
        WEATHER_HISTORY_DIR,
    ]

    for path in directories:
        os.makedirs(
            path,
            exist_ok=True,
        )


def write_json(path, payload):
    """
    Atomic JSON writer.
    V10.2.1 restores this function explicitly.
    """

    directory = os.path.dirname(path)

    if directory:
        os.makedirs(
            directory,
            exist_ok=True,
        )

    temporary_path = (
        f"{path}.tmp"
    )

    with open(
        temporary_path,
        "w",
        encoding="utf-8",
    ) as handle:

        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
        )

        handle.write("\n")

    os.replace(
        temporary_path,
        path,
    )


def append_csv(
    path,
    fields,
    rows,
):
    if not rows:
        return

    directory = os.path.dirname(path)

    if directory:
        os.makedirs(
            directory,
            exist_ok=True,
        )

    exists = os.path.exists(path)

    with open(
        path,
        "a",
        newline="",
        encoding="utf-8",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
        )

        if not exists:
            writer.writeheader()

        for row in rows:
            writer.writerow(row)


def request_json(
    url,
    params=None,
):
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# POLYMARKET
# ============================================================

def get_weather_events():
    print(
        "Consultando eventos Weather activos..."
    )

    events = []
    offset = 0

    for _ in range(
        MAX_EVENT_PAGES
    ):

        params = {
            "tag_id": WEATHER_TAG_ID,
            "limit": EVENT_PAGE_SIZE,
            "offset": offset,
            "active": "true",
            "closed": "false",
        }

        try:
            batch = request_json(
                f"{POLYMARKET_API}/events",
                params=params,
            )

        except Exception as exc:
            print(
                "  Error consultando "
                f"offset {offset}: {exc}"
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

        time.sleep(0.10)

    return events


# ============================================================
# QUESTION PARSING
# ============================================================

def extract_city(question):
    if not question:
        return None

    q = clean_text(question)

    match = re.search(
        r"temperature\s+in\s+(.+)",
        q,
        re.IGNORECASE,
    )

    if match:
        city = (
            match.group(1)
            .strip()
        )

        city_match = re.match(
            r"(.+?)\s+\bbe\b",
            city,
            re.IGNORECASE,
        )

        if city_match:
            city = (
                city_match.group(1)
                .strip()
            )

        city = re.split(
            r"\s+\bon\b\s+\d{4}-\d{2}-\d{2}",
            city,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]

        city = re.split(
            r"\s+-?\d+(?:\.\d+)?\s*°?\s*[CF]\b",
            city,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]

        city = city.strip(" ?!")

        if city:
            return clean_text(city)

    match = re.search(
        r"\bin\s+(.+?)(?:\s+\b(?:be|on|at|for)\b|\?|$)",
        q,
        re.IGNORECASE,
    )

    if match:
        city = clean_text(
            match.group(1)
        )

        if city:
            return city

    return None


def extract_market_date(market):
    for key in (
        "endDateIso",
        "endDate",
        "resolutionDate",
    ):

        value = market.get(
            key
        )

        if value:
            return str(value)[:10]

    question = (
        market.get(
            "question"
        )
        or ""
    )

    match = re.search(
        r"(\d{4}-\d{2}-\d{2})",
        question,
    )

    if match:
        return match.group(1)

    return None


def detect_market_type(question):
    q = (
        question or ""
    ).lower()

    if "highest temperature" in q:
        return "highest_temperature"

    if "lowest temperature" in q:
        return "lowest_temperature"

    if "temperature" in q:
        return "temperature"

    return "unknown"


def is_temperature_market(question):
    q = (
        question or ""
    ).lower()

    return (
        "temperature" in q
        and (
            "°c" in q
            or "°f" in q
            or "degrees" in q
        )
    )


def detect_temperature_unit(
    question,
    group_title=None,
):
    text = (
        f"{question or ''} "
        f"{group_title or ''}"
    ).lower()

    if (
        "°f" in text
        or "degrees f" in text
        or "fahrenheit" in text
    ):
        return "F"

    if (
        "°c" in text
        or "degrees c" in text
        or "celsius" in text
    ):
        return "C"

    return None


# ============================================================
# RESOLUTION SOURCE / STATION
# ============================================================

def extract_resolution_station(
    resolution_source,
):
    if not resolution_source:
        return None

    try:
        parsed = urlparse(
            str(
                resolution_source
            )
        )

        query = parse_qs(
            parsed.query
        )

        for key in (
            "site",
            "ids",
            "id",
            "station",
        ):

            values = query.get(
                key
            )

            if not values:
                continue

            raw = values[0]

            candidates = re.findall(
                r"[A-Za-z0-9]{4,6}",
                raw,
            )

            for candidate in candidates:
                station = (
                    candidate.upper()
                )

                if len(station) in (
                    4,
                    5,
                ):
                    return station

        path_match = re.search(
            r"(?:site|station)[=/\-]([A-Za-z0-9]{4,6})",
            str(
                resolution_source
            ),
            re.IGNORECASE,
        )

        if path_match:
            return (
                path_match
                .group(1)
                .upper()
            )

    except Exception:
        pass

    return None


def detect_resolution_provider(
    resolution_source,
):
    if not resolution_source:
        return None

    text = (
        str(
            resolution_source
        )
        .lower()
    )

    if "weather.gov" in text:
        return "NOAA_NWS"

    if "wunderground" in text:
        return "WEATHER_UNDERGROUND"

    if "weather.com" in text:
        return "WEATHER_COM"

    return "OTHER"


# ============================================================
# BUCKET PARSING
# ============================================================

def extract_bucket(
    question,
    group_title,
    group_threshold,
):
    title = (
        clean_text(
            group_title
        )
        or ""
    )

    text = clean_text(
        f"{question or ''} {title}"
    )

    # --------------------------------------------------------
    # OR BELOW
    # --------------------------------------------------------

    lower_match = re.search(
        r"(-?\d+(?:\.\d+)?)"
        r"\s*°?\s*[CF]?"
        r"\s*"
        r"(?:or\s+below|or\s+lower|or\s+less|or\s+under)",
        title,
        re.IGNORECASE,
    )

    if not lower_match:
        lower_match = re.search(
            r"(?:below|under|<=)"
            r"\s*"
            r"(-?\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )

    if lower_match:
        value = safe_float(
            lower_match.group(1)
        )

        return (
            "or_lower",
            value,
            None,
            value,
        )

    # --------------------------------------------------------
    # OR ABOVE
    # --------------------------------------------------------

    higher_match = re.search(
        r"(-?\d+(?:\.\d+)?)"
        r"\s*°?\s*[CF]?"
        r"\s*"
        r"(?:or\s+higher|or\s+above|or\s+more|or\s+over)",
        title,
        re.IGNORECASE,
    )

    if not higher_match:
        higher_match = re.search(
            r"(?:above|over|>=)"
            r"\s*"
            r"(-?\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )

    if higher_match:
        value = safe_float(
            higher_match.group(1)
        )

        return (
            "or_higher",
            value,
            value,
            None,
        )

    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

    range_match = re.search(
        r"(-?\d+(?:\.\d+)?)"
        r"\s*(?:to|[-–])\s*"
        r"(-?\d+(?:\.\d+)?)",
        title,
        re.IGNORECASE,
    )

    if range_match:
        low = safe_float(
            range_match.group(1)
        )

        high = safe_float(
            range_match.group(2)
        )

        return (
            "range",
            None,
            low,
            high,
        )

    # --------------------------------------------------------
    # EXACT
    # --------------------------------------------------------

    exact_match = re.search(
        r"(-?\d+(?:\.\d+)?)"
        r"\s*°?\s*[CF]\b",
        title,
        re.IGNORECASE,
    )

    if exact_match:
        value = safe_float(
            exact_match.group(1)
        )

        return (
            "exact",
            value,
            value,
            value,
        )

    # --------------------------------------------------------
    # THRESHOLD
    # --------------------------------------------------------

    threshold = safe_float(
        group_threshold
    )

    if threshold is not None:
        return (
            "exact",
            threshold,
            threshold,
            threshold,
        )

    return (
        "unknown",
        None,
        None,
        None,
    )


# ============================================================
# MARKET NORMALIZATION
# ============================================================

def normalize_market(
    event,
    market,
):
    question = (
        market.get(
            "question"
        )
        or ""
    )

    group_title = (
        market.get(
            "groupItemTitle"
        )
    )

    prices = safe_json(
        market.get(
            "outcomePrices"
        )
    )

    tokens = safe_json(
        market.get(
            "clobTokenIds"
        )
    )

    yes_price = None
    no_price = None

    yes_token = None
    no_token = None

    if (
        isinstance(
            prices,
            list,
        )
        and len(prices) >= 2
    ):
        yes_price = safe_float(
            prices[0]
        )

        no_price = safe_float(
            prices[1]
        )

    if (
        isinstance(
            tokens,
            list,
        )
        and len(tokens) >= 2
    ):
        yes_token = tokens[0]
        no_token = tokens[1]

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

    spread = None

    if (
        best_bid is not None
        and
        best_ask is not None
    ):
        spread = (
            best_ask
            - best_bid
        )

    resolution_source = (
        market.get(
            "resolutionSource"
        )
    )

    station = (
        extract_resolution_station(
            resolution_source
        )
    )

    provider = (
        detect_resolution_provider(
            resolution_source
        )
    )

    market_date = (
        extract_market_date(
            market
        )
    )

    market_type = (
        detect_market_type(
            question
        )
    )

    temperature_unit = (
        detect_temperature_unit(
            question,
            group_title,
        )
    )

    (
        bucket_type,
        bucket_value,
        bucket_low,
        bucket_high,
    ) = extract_bucket(
        question,
        group_title,
        market.get(
            "groupItemThreshold"
        ),
    )

    city = extract_city(
        question
    )

    event_key = "|".join(
        [
            normalize_name(city),

            str(
                market_date
                or "unknown"
            ).replace(
                "-",
                "_",
            ),

            market_type,

            (
                temperature_unit
                or "unknown"
            ).lower(),

            (
                station
                or "unknown"
            ).lower(),
        ]
    )

    return {
        "collected_at": utc_iso(),

        "collector_version": (
            COLLECTOR_VERSION
        ),

        "schema_version": (
            SCHEMA_VERSION
        ),

        "event_key": event_key,

        "event_id": str(
            event.get("id")
            or ""
        ),

        "event_title": event.get(
            "title"
        ),

        "event_slug": event.get(
            "slug"
        ),

        "market_id": str(
            market.get("id")
            or ""
        ),

        "city": city,

        "resolution_station": station,

        "resolution_provider": provider,

        "market_date": market_date,

        "market_type": market_type,

        "temperature_unit": (
            temperature_unit
        ),

        "bucket_type": bucket_type,

        "bucket_value": bucket_value,

        "bucket_low": bucket_low,

        "bucket_high": bucket_high,

        "question": question,

        "slug": market.get(
            "slug"
        ),

        "group_title": group_title,

        "group_threshold": safe_float(
            market.get(
                "groupItemThreshold"
            )
        ),

        "yes_price": yes_price,

        "no_price": no_price,

        "yes_token": yes_token,

        "no_token": no_token,

        "best_bid": best_bid,

        "best_ask": best_ask,

        "spread": spread,

        "volume": safe_float(
            market.get(
                "volume"
            )
        ),

        "volume_24h": safe_float(
            market.get(
                "volume24hr"
            )
        ),

        "liquidity": safe_float(
            market.get(
                "liquidity"
            )
        ),

        "liquidity_clob": safe_float(
            market.get(
                "liquidityClob"
            )
        ),

        "active": bool(
            market.get(
                "active"
            )
        ),

        "closed": bool(
            market.get(
                "closed"
            )
        ),

        "accepting_orders": bool(
            market.get(
                "acceptingOrders"
            )
        ),

        "enable_order_book": bool(
            market.get(
                "enableOrderBook"
            )
        ),

        "approved": bool(
            market.get(
                "approved"
            )
        ),

        "archived": bool(
            market.get(
                "archived"
            )
        ),

        "resolution_source": (
            resolution_source
        ),

        "start_date": market.get(
            "startDate"
        ),

        "end_date": market.get(
            "endDate"
        ),

        "condition_id": market.get(
            "conditionId"
        ),

        "order_min_size": safe_float(
            market.get(
                "orderMinSize"
            )
        ),

        "tick_size": safe_float(
            market.get(
                "orderPriceMinTickSize"
            )
        ),

        "fees_enabled": market.get(
            "feesEnabled"
        ),

        "fee_type": market.get(
            "feeType"
        ),

        "fee_schedule": market.get(
            "feeSchedule"
        ),
    }


def collect_markets(
    events,
):
    markets = []

    for event in events:

        event_markets = (
            event.get(
                "markets"
            )
            or []
        )

        for market in event_markets:

            question = (
                market.get(
                    "question"
                )
                or ""
            )

            if not is_temperature_market(
                question
            ):
                continue

            markets.append(
                normalize_market(
                    event,
                    market,
                )
            )

    return markets


# ============================================================
# CURRENT / TRADEABLE
# ============================================================

def is_current_market_date(
    market_date,
):
    if not market_date:
        return False

    return (
        market_date
        >= utc_date_string()
    )


def is_current_candidate(
    market,
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

        and is_current_market_date(
            market.get(
                "market_date"
            )
        )

        and market.get(
            "bucket_type"
        ) != "unknown"
    )


# ============================================================
# MARKET FILES
# ============================================================

def write_market_history(
    markets,
):
    month = utc_now().strftime(
        "%Y-%m"
    )

    path = os.path.join(
        HISTORY_DIR,
        f"{month}.csv",
    )

    rows = []

    for market in markets:

        rows.append(
            {
                field: market.get(
                    field
                )
                for field in
                MARKET_HISTORY_FIELDS
            }
        )

    append_csv(
        path,
        MARKET_HISTORY_FIELDS,
        rows,
    )


def write_snapshot(
    markets,
):
    date_path = os.path.join(
        SNAPSHOT_DIR,
        utc_now().strftime(
            "%Y-%m-%d"
        ),
    )

    os.makedirs(
        date_path,
        exist_ok=True,
    )

    path = os.path.join(
        date_path,
        utc_now().strftime(
            "%H%M%S"
        ) + ".json",
    )

    payload = {
        "collector_version": (
            COLLECTOR_VERSION
        ),

        "schema_version": (
            SCHEMA_VERSION
        ),

        "collected_at": (
            utc_iso()
        ),

        "markets": markets,
    }

    write_json(
        path,
        payload,
    )

    return path


# ============================================================
# STATION REGISTRY
# ============================================================

def load_station_registry():

    if not os.path.exists(
        STATION_REGISTRY_FILE
    ):
        return {}

    try:

        with open(
            STATION_REGISTRY_FILE,
            "r",
            encoding="utf-8",
        ) as handle:

            data = json.load(
                handle
            )

        if isinstance(
            data,
            dict,
        ):
            return data

    except Exception:
        pass

    return {}


def save_station_registry(
    registry,
):
    write_json(
        STATION_REGISTRY_FILE,
        registry,
    )


def station_value(
    record,
    *keys,
):
    for key in keys:

        value = record.get(
            key
        )

        if value not in (
            None,
            "",
        ):
            return value

    return None


def get_station_info(
    station,
):

    url = (
        f"{AWC_API}"
        "/stationinfo"
    )

    data = request_json(
        url,
        params={
            "ids": station,
            "format": "json",
        },
    )

    if isinstance(
        data,
        list,
    ):
        records = data

    elif isinstance(
        data,
        dict,
    ):
        records = data.get(
            "data",
            [],
        )

    else:
        records = []

    if not records:
        return None

    record = records[0]

    latitude = safe_float(
        station_value(
            record,
            "lat",
            "latitude",
        )
    )

    longitude = safe_float(
        station_value(
            record,
            "lon",
            "longitude",
        )
    )

    name = station_value(
        record,
        "name",
        "stationName",
        "site",
    )

    if (
        latitude is None
        or longitude is None
    ):
        return None

    return {
        "station": station.upper(),

        "latitude": latitude,

        "longitude": longitude,

        "name": name,

        "country": station_value(
            record,
            "country",
            "countryCode",
        ),

        "elevation_m": safe_float(
            station_value(
                record,
                "elev",
                "elevation",
                "elevationM",
            )
        ),

        "source": (
            "AviationWeather.gov "
            "stationinfo"
        ),

        "updated_at": utc_iso(),
    }


def refresh_station_registry(
    markets,
):

    registry = (
        load_station_registry()
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

    new_stations = [
        station
        for station in stations
        if station not in registry
    ]

    print(
        "Station registry: "
        f"{len(stations)} estaciones "
        f"totales / "
        f"{len(new_stations)} nuevas"
    )

    for station in new_stations:

        try:

            info = get_station_info(
                station
            )

            if info:

                registry[
                    station
                ] = info

                print(
                    f"  {station}: "
                    f"{info['latitude']:.4f}, "
                    f"{info['longitude']:.4f}"
                )

            else:

                registry[
                    station
                ] = {
                    "station": station,

                    "latitude": None,

                    "longitude": None,

                    "status": "not_found",

                    "updated_at": (
                        utc_iso()
                    ),
                }

        except Exception as exc:

            print(
                f"  {station}: "
                f"stationinfo error: "
                f"{exc}"
            )

    save_station_registry(
        registry
    )

    return registry


# ============================================================
# METAR
# ============================================================

def collect_metar_batch(
    stations,
):

    stations = sorted(
        {
            station
            for station in stations
            if station
        }
    )

    if not stations:
        return []

    ids = ",".join(
        stations
    )

    url = (
        f"{AWC_API}"
        "/metar"
    )

    print(
        "Consultando METAR en una sola "
        f"petición para "
        f"{len(stations)} estaciones..."
    )

    try:

        response = requests.get(
            url,
            params={
                "ids": ids,
                "format": "json",
            },
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 204:

            print(
                "  METAR batch: sin datos"
            )

            return []

        response.raise_for_status()

        data = response.json()

    except Exception as exc:

        print(
            "  METAR batch error: "
            f"{exc}"
        )

        return []

    if isinstance(
        data,
        list,
    ):
        records = data

    elif isinstance(
        data,
        dict,
    ):
        records = data.get(
            "data",
            [],
        )

    else:
        records = []

    rows = []

    for record in records:

        station = station_value(
            record,
            "icaoId",
            "station",
            "site",
            "id",
        )

        if not station:
            continue

        rows.append(
            {
                "collected_at": utc_iso(),

                "station": str(
                    station
                ).upper(),

                "observation_time": (
                    station_value(
                        record,
                        "reportTime",
                        "obsTime",
                        "observationTime",
                        "reportTimeIso",
                    )
                ),

                "temperature_c": (
                    safe_float(
                        station_value(
                            record,
                            "temp",
                            "temperature",
                            "tempC",
                        )
                    )
                ),

                "dewpoint_c": (
                    safe_float(
                        station_value(
                            record,
                            "dewp",
                            "dewpoint",
                            "dewpointC",
                        )
                    )
                ),

                "wind_speed_kt": (
                    safe_float(
                        station_value(
                            record,
                            "wspd",
                            "windSpeed",
                            "windSpeedKt",
                        )
                    )
                ),

                "raw_text": (
                    station_value(
                        record,
                        "rawOb",
                        "raw_text",
                        "raw",
                    )
                ),

                "source": (
                    "AviationWeather.gov METAR"
                ),
            }
        )

    return rows


# ============================================================
# OPEN-METEO
# ============================================================

def build_location_list(
    registry,
    stations,
):

    locations = []

    for station in stations:

        info = registry.get(
            station
        )

        if not info:
            continue

        latitude = safe_float(
            info.get(
                "latitude"
            )
        )

        longitude = safe_float(
            info.get(
                "longitude"
            )
        )

        if (
            latitude is None
            or longitude is None
        ):
            continue

        locations.append(
            {
                "station": station,

                "latitude": latitude,

                "longitude": longitude,
            }
        )

    return locations


def build_batches(
    locations,
):

    return [
        locations[
            index:
            index
            + OPEN_METEO_BATCH_SIZE
        ]
        for index in range(
            0,
            len(locations),
            OPEN_METEO_BATCH_SIZE,
        )
    ]


def extract_target_dates(
    markets,
):

    dates = sorted(
        {
            m.get(
                "market_date"
            )
            for m in markets
            if (
                m.get(
                    "market_date"
                )
                and is_current_market_date(
                    m.get(
                        "market_date"
                    )
                )
            )
        }
    )

    return dates[:3]


def parse_open_meteo_batch(
    payload,
    locations,
    model_name,
    target_dates,
    forecast_url,
):

    if isinstance(
        payload,
        list,
    ):
        responses = payload

    elif isinstance(
        payload,
        dict,
    ):
        responses = [
            payload
        ]

    else:
        responses = []

    rows = []

    for index, data in enumerate(
        responses
    ):

        if index >= len(
            locations
        ):
            break

        location = locations[
            index
        ]

        station = location[
            "station"
        ]

        daily = (
            data.get(
                "daily"
            )
            or {}
        )

        dates = (
            daily.get(
                "time"
            )
            or []
        )

        max_values = (
            daily.get(
                "temperature_2m_max"
            )
            or []
        )

        min_values = (
            daily.get(
                "temperature_2m_min"
            )
            or []
        )

        timezone_name = data.get(
            "timezone"
        )

        for row_index, date_value in enumerate(
            dates
        ):

            if date_value not in target_dates:
                continue

            max_value = None
            min_value = None

            if row_index < len(
                max_values
            ):

                max_value = safe_float(
                    max_values[
                        row_index
                    ]
                )

            if row_index < len(
                min_values
            ):

                min_value = safe_float(
                    min_values[
                        row_index
                    ]
                )

            rows.append(
                {
                    "collected_at": utc_iso(),

                    "station": station,

                    "market_date": (
                        date_value
                    ),

                    "model": model_name,

                    "timezone": timezone_name,

                    "forecast_target": (
                        date_value
                    ),

                    "temperature_max_c": (
                        max_value
                    ),

                    "temperature_min_c": (
                        min_value
                    ),

                    "forecast_url": (
                        forecast_url
                    ),
                }
            )

    return rows


def collect_forecast_model(
    model_name,
    endpoint,
    batches,
    target_dates,
):

    rows = []

    total_batches = len(
        batches
    )

    for batch_index, batch in enumerate(
        batches,
        start=1,
    ):

        latitudes = ",".join(
            f"{x['latitude']:.6f}"
            for x in batch
        )

        longitudes = ",".join(
            f"{x['longitude']:.6f}"
            for x in batch
        )

        params = {
            "latitude": latitudes,

            "longitude": longitudes,

            "daily": (
                "temperature_2m_max,"
                "temperature_2m_min"
            ),

            "timezone": "auto",

            "start_date": min(
                target_dates
            ),

            "end_date": max(
                target_dates
            ),
        }

        try:

            response = requests.get(
                endpoint,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            batch_rows = (
                parse_open_meteo_batch(
                    payload,
                    batch,
                    model_name,
                    target_dates,
                    response.url,
                )
            )

            rows.extend(
                batch_rows
            )

            print(
                f"  Forecast "
                f"{model_name}: "
                f"batch "
                f"{batch_index}/"
                f"{total_batches} "
                f"({len(batch)} estaciones) OK"
            )

        except Exception as exc:

            print(
                f"  Forecast "
                f"{model_name}: "
                f"batch "
                f"{batch_index}/"
                f"{total_batches} "
                f"ERROR: "
                f"{exc}"
            )

    return rows


def collect_forecasts(
    markets,
    registry,
):

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

    target_dates = (
        extract_target_dates(
            markets
        )
    )

    if not stations:
        return []

    if not target_dates:
        return []

    locations = build_location_list(
        registry,
        stations,
    )

    batches = build_batches(
        locations
    )

    if not batches:
        return []

    sources = [
        (
            "open_meteo_best_match",
            OPEN_METEO_API,
        ),

        (
            "ecmwf",
            OPEN_METEO_ECMWF_API,
        ),

        (
            "gfs",
            OPEN_METEO_GFS_API,
        ),
    ]

    all_rows = []

    for (
        model_name,
        endpoint,
    ) in sources:

        rows = (
            collect_forecast_model(
                model_name,
                endpoint,
                batches,
                target_dates,
            )
        )

        all_rows.extend(
            rows
        )

    return all_rows


# ============================================================
# SUMMARY
# ============================================================

def summarize(
    markets,
):

    current_candidates = [
        m
        for m in markets
        if is_current_candidate(
            m
        )
    ]

    historical_past = [
        m
        for m in markets
        if (
            m.get(
                "market_date"
            )
            and
            m.get(
                "market_date"
            )
            < utc_date_string()
        )
    ]

    with_prices = [
        m
        for m in markets
        if (
            m.get(
                "yes_price"
            ) is not None
            and
            m.get(
                "no_price"
            ) is not None
        )
    ]

    with_tokens = [
        m
        for m in markets
        if (
            m.get(
                "yes_token"
            )
            and
            m.get(
                "no_token"
            )
        )
    ]

    with_bid_ask = [
        m
        for m in markets
        if (
            m.get(
                "best_bid"
            ) is not None
            and
            m.get(
                "best_ask"
            ) is not None
        )
    ]

    cities = {
        m.get(
            "city"
        )
        for m in markets
        if m.get(
            "city"
        )
    }

    stations = {
        m.get(
            "resolution_station"
        )
        for m in markets
        if m.get(
            "resolution_station"
        )
    }

    event_keys = {
        m.get(
            "event_key"
        )
        for m in markets
        if m.get(
            "event_key"
        )
    }

    missing_city = sum(
        m.get(
            "city"
        ) is None
        for m in markets
    )

    missing_date = sum(
        m.get(
            "market_date"
        ) is None
        for m in markets
    )

    missing_unit = sum(
        m.get(
            "temperature_unit"
        ) is None
        for m in markets
    )

    missing_station = sum(
        m.get(
            "resolution_station"
        ) is None
        for m in markets
    )

    unknown_bucket = sum(
        m.get(
            "bucket_type"
        ) == "unknown"
        for m in markets
    )

    missing_prices = sum(
        (
            m.get(
                "yes_price"
            ) is None
            or
            m.get(
                "no_price"
            ) is None
        )
        for m in markets
    )

    families = {}

    for market in markets:

        key = market.get(
            "event_key"
        )

        families[key] = (
            families.get(
                key,
                0,
            )
            + 1
        )

    family_sizes = list(
        families.values()
    )

    print("")
    print("=" * 70)
    print("COLLECTION COMPLETE")
    print("=" * 70)

    print(
        f"Collector: "
        f"{COLLECTOR_VERSION}"
    )

    print(
        f"Events: "
        f"{GLOBALS.get('events_count', 0)}"
    )

    print(
        f"Temperature markets: "
        f"{len(markets)}"
    )

    print(
        f"Cities: "
        f"{len(cities)}"
    )

    print(
        f"Stations: "
        f"{len(stations)}"
    )

    print(
        f"Event keys: "
        f"{len(event_keys)}"
    )

    print(
        f"Current trade candidates: "
        f"{len(current_candidates)}"
    )

    print(
        f"Past-date markets retained: "
        f"{len(historical_past)}"
    )

    print(
        f"Markets with prices: "
        f"{len(with_prices)}"
    )

    print(
        "Markets with YES/NO tokens: "
        f"{len(with_tokens)}"
    )

    print(
        "Markets with bid/ask: "
        f"{len(with_bid_ask)}"
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

    print(
        "Bucket types: "
        + ", ".join(
            bucket_types
        )
    )

    if family_sizes:

        print(
            f"Max markets/family: "
            f"{max(family_sizes)}"
        )

        print(
            "Avg markets/family: "
            f"{sum(family_sizes) / len(family_sizes):.2f}"
        )

    print("")
    print("=" * 70)
    print("DATA QUALITY")
    print("=" * 70)

    print(
        f"markets_total: "
        f"{len(markets)}"
    )

    print(
        f"missing_city: "
        f"{missing_city}"
    )

    print(
        f"missing_market_date: "
        f"{missing_date}"
    )

    print(
        f"missing_unit: "
        f"{missing_unit}"
    )

    print(
        f"missing_station: "
        f"{missing_station}"
    )

    print(
        f"missing_prices: "
        f"{missing_prices}"
    )

    print(
        f"unknown_bucket: "
        f"{unknown_bucket}"
    )

    return {
        "markets_total": len(markets),
        "current_candidates": len(
            current_candidates
        ),
        "past_date_markets": len(
            historical_past
        ),
        "cities": len(cities),
        "stations": len(stations),
        "event_keys": len(event_keys),
        "missing_city": missing_city,
        "missing_market_date": missing_date,
        "missing_unit": missing_unit,
        "missing_station": missing_station,
        "missing_prices": missing_prices,
        "unknown_bucket": unknown_bucket,
    }


# ============================================================
# VALIDATION
# ============================================================

def print_validation(
    markets,
):

    print("")
    print("=" * 70)
    print("VALIDATION V10.2.1")
    print("=" * 70)

    validation_markets = [
        m
        for m in markets
        if is_current_candidate(
            m
        )
    ]

    grouped = {}

    for market in validation_markets:

        key = (
            market.get(
                "city"
            ),

            market.get(
                "resolution_station"
            ),

            market.get(
                "market_date"
            ),
        )

        grouped.setdefault(
            key,
            [],
        ).append(
            market
        )

    count = 0

    for key, rows in grouped.items():

        if not rows:
            continue

        first = rows[0]

        print("")

        print(
            f"{first.get('city')} | "
            f"{first.get('resolution_station')} | "
            f"{first.get('market_date')}"
        )

        print(
            f"Markets in group: "
            f"{len(rows)}"
        )

        print(
            f"Type: "
            f"{first.get('market_type')} | "
            f"Unit: "
            f"{first.get('temperature_unit')}"
        )

        for row in rows[:8]:

            print(
                "  "
                f"{row.get('group_title')} | "
                f"bucket="
                f"{row.get('bucket_type')} | "
                f"value="
                f"{row.get('bucket_value')} | "
                f"YES="
                f"{row.get('yes_price')} | "
                f"Ask="
                f"{row.get('best_ask')}"
            )

        print(
            "Resolution: "
            f"{first.get('resolution_source')}"
        )

        count += 1

        if count >= 8:
            break


# ============================================================
# MAIN
# ============================================================

def main():

    ensure_directories()

    print("")
    print("=" * 70)
    print("Polymarket Weather Edge Lab")
    print(
        "Temperature + Weather "
        "Collector V10.2.1"
    )
    print("=" * 70)

    print(
        f"UTC: {utc_iso()}"
    )

    print(
        f"Weather tag: "
        f"{WEATHER_TAG_ID} "
        f"({WEATHER_TAG_SLUG})"
    )

    print(
        f"Current UTC date: "
        f"{utc_date_string()}"
    )

    if os.path.exists(
        LEGACY_HISTORY_FILE
    ):

        size_mb = (
            os.path.getsize(
                LEGACY_HISTORY_FILE
            )
            / 1024
            / 1024
        )

        print("")
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
            "  V10.2.1 NO lo modifica."
        )

    # ========================================================
    # POLYMARKET
    # ========================================================

    events = get_weather_events()

    GLOBALS[
        "events_count"
    ] = len(
        events
    )

    print("")

    print(
        f"Weather events: "
        f"{len(events)}"
    )

    markets = collect_markets(
        events
    )

    # --------------------------------------------------------
    # ALL MARKETS
    # --------------------------------------------------------

    write_json(
        LATEST_FILE,
        {
            "collector_version": (
                COLLECTOR_VERSION
            ),

            "schema_version": (
                SCHEMA_VERSION
            ),

            "collected_at": (
                utc_iso()
            ),

            "events_count": (
                len(events)
            ),

            "markets": markets,
        },
    )

    # --------------------------------------------------------
    # CURRENT CANDIDATES
    # --------------------------------------------------------

    current_candidates = [
        m
        for m in markets
        if is_current_candidate(
            m
        )
    ]

    write_json(
        ACTIVE_FILE,
        {
            "collector_version": (
                COLLECTOR_VERSION
            ),

            "schema_version": (
                SCHEMA_VERSION
            ),

            "collected_at": (
                utc_iso()
            ),

            "markets": (
                current_candidates
            ),
        },
    )

    # --------------------------------------------------------
    # MARKET HISTORY
    # --------------------------------------------------------

    write_market_history(
        markets
    )

    snapshot_path = write_snapshot(
        markets
    )

    validation = summarize(
        markets
    )

    print("")
    print(
        "Archivos Polymarket:"
    )

    print(
        f"  Latest: "
        f"{LATEST_FILE}"
    )

    print(
        f"  Current: "
        f"{ACTIVE_FILE}"
    )

    print(
        f"  Snapshot: "
        f"{snapshot_path}"
    )

    print(
        "  History: "
        f"{os.path.join(HISTORY_DIR, utc_now().strftime('%Y-%m') + '.csv')}"
    )

    # ========================================================
    # WEATHER
    # ========================================================

    print("")
    print("=" * 70)
    print("WEATHER LAYER V10.2.1")
    print("=" * 70)

    registry = (
        refresh_station_registry(
            markets
        )
    )

    stations = sorted(
        {
            m.get(
                "resolution_station"
            )
            for m in current_candidates
            if m.get(
                "resolution_station"
            )
        }
    )

    print(
        f"Current stations with ID: "
        f"{len(stations)}"
    )

    # --------------------------------------------------------
    # METAR
    # --------------------------------------------------------

    observations = (
        collect_metar_batch(
            stations
        )
    )

    append_csv(
        WEATHER_OBS_FILE,
        OBS_FIELDS,
        observations,
    )

    print(
        "Observations collected: "
        f"{len(observations)}"
    )

    # --------------------------------------------------------
    # FORECASTS
    # --------------------------------------------------------

    forecasts = collect_forecasts(
        current_candidates,
        registry,
    )

    append_csv(
        WEATHER_FCST_FILE,
        FCST_FIELDS,
        forecasts,
    )

    print(
        "Forecast rows collected: "
        f"{len(forecasts)}"
    )

    # --------------------------------------------------------
    # WEATHER LATEST
    # --------------------------------------------------------

    latest_weather = {
        "collector_version": (
            COLLECTOR_VERSION
        ),

        "schema_version": (
            SCHEMA_VERSION
        ),

        "collected_at": (
            utc_iso()
        ),

        "current_candidates": (
            len(
                current_candidates
            )
        ),

        "stations_requested": (
            len(stations)
        ),

        "observations_count": (
            len(observations)
        ),

        "forecast_rows_count": (
            len(forecasts)
        ),

        "station_registry": (
            STATION_REGISTRY_FILE
        ),

        "observation_file": (
            WEATHER_OBS_FILE
        ),

        "forecast_file": (
            WEATHER_FCST_FILE
        ),
    }

    write_json(
        WEATHER_LATEST_FILE,
        latest_weather,
    )

    print("")
    print("=" * 70)
    print("WEATHER COMPLETE")
    print("=" * 70)

    print(
        f"Station registry: "
        f"{STATION_REGISTRY_FILE}"
    )

    print(
        f"Weather latest: "
        f"{WEATHER_LATEST_FILE}"
    )

    print(
        f"Observation history: "
        f"{WEATHER_OBS_FILE}"
    )

    print(
        f"Forecast history: "
        f"{WEATHER_FCST_FILE}"
    )

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    print_validation(
        markets
    )

    # --------------------------------------------------------
    # FINAL STATUS
    # --------------------------------------------------------

    print("")
    print("=" * 70)
    print("V10.2.1 STATUS")
    print("=" * 70)

    print(
        json.dumps(
            validation,
            indent=2,
        )
    )

    print("=" * 70)


if __name__ == "__main__":
    main()

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
# Temperature + Weather Collector V10.0
# ============================================================

COLLECTOR_VERSION = "10.0"
SCHEMA_VERSION = "10.0"

POLYMARKET_API = "https://gamma-api.polymarket.com"
WEATHER_TAG_ID = 84
WEATHER_TAG_SLUG = "weather"

AWC_API = "https://aviationweather.gov/api/data"

OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ECMWF_API = "https://api.open-meteo.com/v1/ecmwf"
OPEN_METEO_GFS_API = "https://api.open-meteo.com/v1/gfs"

DATA_DIR = "data"
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")
HISTORY_DIR = os.path.join(DATA_DIR, "history")

WEATHER_DIR = os.path.join(DATA_DIR, "weather")
WEATHER_HISTORY_DIR = os.path.join(WEATHER_DIR, "history")

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

REQUEST_TIMEOUT = 30

EVENT_PAGE_SIZE = 100
MAX_EVENT_PAGES = 20
MAX_WEATHER_STATIONS = 100

HEADERS = {
    "User-Agent": "PolymarketWeatherEdgeLab/10.0",
    "Accept": "application/json",
}

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


# ============================================================
# TIME / CONVERSIONS
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


def safe_int(value):
    try:
        if value is None or value == "":
            return None

        return int(value)

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


def request_json(
    url,
    params=None,
    timeout=REQUEST_TIMEOUT,
):

    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=timeout,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# POLYMARKET
# ============================================================

def get_weather_events():

    print("Consultando eventos Weather activos...")

    events = []

    offset = 0

    for _ in range(MAX_EVENT_PAGES):

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
                f"  Fin de paginación / error: {exc}"
            )

            break

        if not batch:
            break

        events.extend(batch)

        print(
            f"  Eventos descargados: {len(events)}"
        )

        if len(batch) < EVENT_PAGE_SIZE:
            break

        offset += EVENT_PAGE_SIZE

        time.sleep(0.15)

    return events


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
            re.IGNORECASE,
        )

        if match:

            return clean_text(
                match.group(1)
            )

    return None


def extract_market_date(market):

    for key in (
        "endDateIso",
        "endDate",
        "resolutionDate",
    ):

        value = market.get(key)

        if value:

            return str(value)[:10]

    question = market.get(
        "question"
    ) or ""

    patterns = [
        r"on (\d{4}-\d{2}-\d{2})",
        r"on ([A-Z][a-z]+ \d{1,2}, \d{4})",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            question,
            re.IGNORECASE,
        )

        if match:

            value = match.group(1)

            if re.match(
                r"\d{4}-\d{2}-\d{2}",
                value,
            ):

                return value

    return None


def detect_market_type(question):

    q = (question or "").lower()

    if "highest temperature" in q:
        return "highest_temperature"

    if "lowest temperature" in q:
        return "lowest_temperature"

    if "temperature" in q:
        return "temperature"

    return "unknown"


def is_temperature_market(question):

    q = (question or "").lower()

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


def extract_resolution_station(
    resolution_source,
):

    if not resolution_source:
        return None

    try:

        parsed = urlparse(
            str(resolution_source)
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

            values = query.get(key)

            if values:

                raw = values[0]

                match = re.search(
                    r"[A-Za-z0-9]{4,6}",
                    raw,
                )

                if match:

                    station = (
                        match.group(0)
                        .upper()
                    )

                    if len(station) in (
                        4,
                        5,
                    ):

                        return station

        path_match = re.search(
            r"(?:site|station)[=/\-]([A-Za-z0-9]{4,6})",
            str(resolution_source),
            re.IGNORECASE,
        )

        if path_match:

            return (
                path_match.group(1)
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
        str(resolution_source)
        .lower()
    )

    if "weather.gov" in text:
        return "NOAA_NWS"

    if "wunderground" in text:
        return "WEATHER_UNDERGROUND"

    if "weather.com" in text:
        return "WEATHER_COM"

    return "OTHER"


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


def extract_bucket(
    question,
    group_title,
    group_threshold,
):

    title = (
        clean_text(group_title)
        or ""
    )

    text = (
        f"{question or ''} "
        f"{title}"
    )

    exact = re.search(
        r"(-?\d+(?:\.\d+)?)\s*°?\s*[CF]?\b",
        title,
    )

    values = re.findall(
        r"-?\d+(?:\.\d+)?",
        title,
    )

    lower_match = re.search(
        r"(?:or below|or lower|or less|or under|below|<=)\s*(-?\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )

    higher_match = re.search(
        r"(?:or higher|or above|or more|or over|above|>=)\s*(-?\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )

    range_match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*(?:to|-)\s*(-?\d+(?:\.\d+)?)",
        title,
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

    if exact:

        value = safe_float(
            exact.group(1)
        )

        return (
            "exact",
            value,
            value,
            value,
        )

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

    if values:

        value = safe_float(
            values[0]
        )

        if value is not None:

            return (
                "exact",
                value,
                value,
                value,
            )

    return (
        "unknown",
        None,
        None,
        None,
    )


def normalize_market(
    event,
    market,
):

    question = (
        market.get("question")
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
        isinstance(prices, list)
        and len(prices) >= 2
    ):

        yes_price = safe_float(
            prices[0]
        )

        no_price = safe_float(
            prices[1]
        )

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

    resolution_source = market.get(
        "resolutionSource"
    )

    station = extract_resolution_station(
        resolution_source
    )

    provider = detect_resolution_provider(
        resolution_source
    )

    market_date = extract_market_date(
        market
    )

    market_type = detect_market_type(
        question
    )

    temperature_unit = detect_temperature_unit(
        question,
        group_title,
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
        "collector_version": COLLECTOR_VERSION,
        "schema_version": SCHEMA_VERSION,
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
        "temperature_unit": temperature_unit,
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
            market.get("volume")
        ),
        "volume_24h": safe_float(
            market.get("volume24hr")
        ),
        "liquidity": safe_float(
            market.get("liquidity")
        ),
        "liquidity_clob": safe_float(
            market.get("liquidityClob")
        ),
        "active": bool(
            market.get("active")
        ),
        "closed": bool(
            market.get("closed")
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
            market.get("approved")
        ),
        "archived": bool(
            market.get("archived")
        ),
        "resolution_source": resolution_source,
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


def is_active_candidate(
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

        and market.get(
            "bucket_type"
        ) != "unknown"
    )


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

            normalized = normalize_market(
                event,
                market,
            )

            markets.append(
                normalized
            )

    return markets


# ============================================================
# FILE WRITERS
# ============================================================

def write_json(
    path,
    payload,
):

    tmp = (
        f"{path}.tmp"
    )

    with open(
        tmp,
        "w",
        encoding="utf-8",
    ) as handle:

        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        tmp,
        path,
    )


def append_csv(
    path,
    fields,
    rows,
):

    exists = os.path.exists(
        path
    )

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

            writer.writerow(
                row
            )


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
                for field in MARKET_HISTORY_FIELDS
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

    filename = (
        utc_now().strftime(
            "%H%M%S"
        )
        + ".json"
    )

    path = os.path.join(
        date_path,
        filename,
    )

    payload = {
        "collector_version": COLLECTOR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "collected_at": utc_iso(),
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

    lat = safe_float(
        station_value(
            record,
            "lat",
            "latitude",
        )
    )

    lon = safe_float(
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
        lat is None
        or lon is None
    ):

        return None

    return {
        "station": station.upper(),
        "latitude": lat,
        "longitude": lon,
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

    registry = load_station_registry()

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

    missing = [
        station
        for station in stations
        if station not in registry
    ]

    if missing:

        print(
            "Station registry: "
            f"{len(missing)} "
            "nuevas estaciones"
        )

    for station in missing[
        :MAX_WEATHER_STATIONS
    ]:

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
                    "updated_at": utc_iso(),
                }

        except Exception as exc:

            print(
                f"  {station}: "
                f"stationinfo error: {exc}"
            )

        time.sleep(
            0.15
        )

    save_station_registry(
        registry
    )

    return registry


# ============================================================
# METAR OBSERVATIONS
# ============================================================

def collect_metar(
    station,
):

    url = (
        f"{AWC_API}"
        "/metar"
    )

    response = requests.get(
        url,
        params={
            "ids": station,
            "format": "json",
        },
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 204:
        return None

    response.raise_for_status()

    data = response.json()

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

    return {
        "collected_at": utc_iso(),

        "station": station.upper(),

        "observation_time": station_value(
            record,
            "reportTime",
            "obsTime",
            "observationTime",
            "reportTimeIso",
        ),

        "temperature_c": safe_float(
            station_value(
                record,
                "temp",
                "temperature",
                "tempC",
            )
        ),

        "dewpoint_c": safe_float(
            station_value(
                record,
                "dewp",
                "dewpoint",
                "dewpointC",
            )
        ),

        "wind_speed_kt": safe_float(
            station_value(
                record,
                "wspd",
                "windSpeed",
                "windSpeedKt",
            )
        ),

        "raw_text": station_value(
            record,
            "rawOb",
            "raw_text",
            "raw",
        ),

        "source": (
            "AviationWeather.gov "
            "METAR"
        ),
    }


def collect_observations(
    stations,
):

    rows = []

    for station in stations:

        try:

            row = collect_metar(
                station
            )

            if row:

                rows.append(
                    row
                )

                print(
                    f"  METAR {station}: "
                    f"{row.get('temperature_c')} C"
                )

            else:

                print(
                    f"  METAR {station}: "
                    "sin observación"
                )

        except Exception as exc:

            print(
                f"  METAR {station}: "
                f"error {exc}"
            )

        time.sleep(
            0.15
        )

    if rows:

        append_csv(
            WEATHER_OBS_FILE,
            OBS_FIELDS,
            rows,
        )

    return rows


# ============================================================
# OPEN-METEO FORECASTS
# ============================================================

def parse_daily_forecast(
    payload,
    target_date,
    model,
    station,
    url,
):

    daily = (
        payload.get(
            "daily"
        )
        if isinstance(
            payload,
            dict,
        )
        else None
    )

    if not daily:
        return None

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

    for index, date_value in enumerate(
        dates
    ):

        if date_value != target_date:
            continue

        return {
            "collected_at": utc_iso(),

            "station": station,

            "market_date": target_date,

            "model": model,

            "timezone": payload.get(
                "timezone"
            ),

            "forecast_target": target_date,

            "temperature_max_c": safe_float(
                (
                    max_values[index]
                    if index
                    < len(max_values)
                    else None
                )
            ),

            "temperature_min_c": safe_float(
                (
                    min_values[index]
                    if index
                    < len(min_values)
                    else None
                )
            ),

            "forecast_url": url,
        }

    return None


def collect_open_meteo_forecast(
    station,
    info,
    target_dates,
):

    latitude = info.get(
        "latitude"
    )

    longitude = info.get(
        "longitude"
    )

    if (
        latitude is None
        or longitude is None
    ):

        return []

    rows = []

    start_date = min(
        target_dates
    )

    end_date = max(
        target_dates
    )

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

    for (
        model_name,
        endpoint,
    ) in sources:

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "daily": (
                "temperature_2m_max,"
                "temperature_2m_min"
            ),
            "timezone": "auto",
            "start_date": start_date,
            "end_date": end_date,
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

            for target_date in target_dates:

                row = parse_daily_forecast(
                    payload,
                    target_date,
                    model_name,
                    station,
                    response.url,
                )

                if row:

                    rows.append(
                        row
                    )

            print(
                f"  Forecast "
                f"{station}/"
                f"{model_name}: "
                f"{len(target_dates)} "
                "días solicitados"
            )

        except Exception as exc:

            print(
                f"  Forecast "
                f"{station}/"
                f"{model_name}: "
                f"error {exc}"
            )

        time.sleep(
            0.20
        )

    return rows


def collect_forecasts(
    markets,
    registry,
):

    target_by_station = {}

    for market in markets:

        station = market.get(
            "resolution_station"
        )

        market_date = market.get(
            "market_date"
        )

        if (
            not station
            or not market_date
        ):
            continue

        target_by_station.setdefault(
            station,
            set(),
        ).add(
            market_date
        )

    rows = []

    for (
        station,
        date_set,
    ) in sorted(
        target_by_station.items()
    ):

        info = registry.get(
            station
        )

        if not info:
            continue

        if (
            info.get(
                "latitude"
            ) is None
            or info.get(
                "longitude"
            ) is None
        ):

            continue

        target_dates = sorted(
            date_set
        )

        try:

            rows.extend(
                collect_open_meteo_forecast(
                    station,
                    info,
                    target_dates,
                )
            )

        except Exception as exc:

            print(
                f"  Forecast {station}: "
                f"error general {exc}"
            )

    if rows:

        append_csv(
            WEATHER_FCST_FILE,
            FCST_FIELDS,
            rows,
        )

    return rows


# ============================================================
# VALIDATION / REPORTING
# ============================================================

def summarize(
    markets,
):

    active = [
        m
        for m in markets
        if is_active_candidate(m)
    ]

    with_prices = [
        m
        for m in markets
        if (
            m.get("yes_price")
            is not None
            and
            m.get("no_price")
            is not None
        )
    ]

    with_tokens = [
        m
        for m in markets
        if (
            m.get("yes_token")
            and
            m.get("no_token")
        )
    ]

    with_bid_ask = [
        m
        for m in markets
        if (
            m.get("best_bid")
            is not None
            and
            m.get("best_ask")
            is not None
        )
    ]

    cities = {
        m.get("city")
        for m in markets
        if m.get("city")
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
        m.get("event_key")
        for m in markets
        if m.get("event_key")
    }

    missing_city = sum(
        m.get("city") is None
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
            m.get("yes_price")
            is None
            or
            m.get("no_price")
            is None
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

    print(
        f"Active candidates: "
        f"{len(active)}"
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
        "active_candidates": len(active),
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


def print_validation(
    markets,
):

    print("")
    print("=" * 70)
    print("VALIDATION V10")
    print("=" * 70)

    examples = []

    seen = set()

    for market in markets:

        station = (
            market.get(
                "resolution_station"
            )
            or "unknown"
        )

        city = (
            market.get(
                "city"
            )
            or "unknown"
        )

        key = (
            f"{city}|{station}"
        )

        if key in seen:
            continue

        if (
            market.get(
                "yes_price"
            )
            is None
        ):

            continue

        examples.append(
            market
        )

        seen.add(
            key
        )

        if len(examples) >= 8:
            break

    for market in examples:

        print(
            f"{market.get('city')} | "
            f"{market.get('resolution_station')} | "
            f"{market.get('market_date')}"
        )

        print(
            f"Type: "
            f"{market.get('market_type')} | "
            f"Unit: "
            f"{market.get('temperature_unit')}"
        )

        print(
            f"Bucket: "
            f"{market.get('bucket_type')} | "
            f"Value: "
            f"{market.get('bucket_value')}"
        )

        print(
            f"YES: "
            f"{market.get('yes_price')} | "
            f"NO: "
            f"{market.get('no_price')} | "
            f"Bid: "
            f"{market.get('best_bid')} | "
            f"Ask: "
            f"{market.get('best_ask')}"
        )

        print(
            f"Resolution: "
            f"{market.get('resolution_source')}"
        )

        print(
            "-" * 70
        )


# ============================================================
# MAIN
# ============================================================

GLOBALS = {
    "events_count": 0,
}


def main():

    ensure_directories()

    print("")
    print("=" * 70)
    print("Polymarket Weather Edge Lab")
    print("Temperature + Weather Collector V10.0")
    print("=" * 70)

    print(
        f"UTC: {utc_iso()}"
    )

    print(
        f"Weather tag: "
        f"{WEATHER_TAG_ID} "
        f"({WEATHER_TAG_SLUG})"
    )

    if os.path.exists(
        LEGACY_HISTORY_FILE
    ):

        size_mb = (
            os.path.getsize(
                LEGACY_HISTORY_FILE
            )
            / (
                1024 * 1024
            )
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
            "  V10 NO lo modifica."
        )

    # --------------------------------------------------------
    # POLYMARKET
    # --------------------------------------------------------

    events = get_weather_events()

    GLOBALS[
        "events_count"
    ] = len(events)

    print("")
    print(
        f"Weather events: "
        f"{len(events)}"
    )

    markets = collect_markets(
        events
    )

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

    active = [
        m
        for m in markets
        if is_active_candidate(m)
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
            "markets": active,
        },
    )

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
        f"  Active: "
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

    # --------------------------------------------------------
    # WEATHER LAYER
    # --------------------------------------------------------

    print("")
    print("=" * 70)
    print("WEATHER LAYER V10")
    print("=" * 70)

    registry = refresh_station_registry(
        markets
    )

    weather_stations = sorted(
        {
            m.get(
                "resolution_station"
            )
            for m in active
            if m.get(
                "resolution_station"
            )
        }
    )

    print(
        f"Stations activas con ID: "
        f"{len(weather_stations)}"
    )

    observations = collect_observations(
        weather_stations
    )

    forecasts = collect_forecasts(
        active,
        registry,
    )

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
        "observations_count": (
            len(observations)
        ),
        "forecast_rows_count": (
            len(forecasts)
        ),
        "observation_file": (
            WEATHER_OBS_FILE
        ),
        "forecast_file": (
            WEATHER_FCST_FILE
        ),
        "station_registry": (
            STATION_REGISTRY_FILE
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
        f"Observations collected: "
        f"{len(observations)}"
    )

    print(
        f"Forecast rows collected: "
        f"{len(forecasts)}"
    )

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

    print_validation(
        markets
    )

    print("")
    print("=" * 70)
    print("V10 STATUS")
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

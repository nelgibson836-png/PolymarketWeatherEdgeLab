import csv
import json
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests


# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Station Observation Localizer V1.0
#
# PURPOSE:
# Convert the existing UTC IEM observations into local
# station dates so they can be matched correctly against
# Open-Meteo Previous Runs data collected with timezone=auto.
#
# DOES NOT DOWNLOAD IEM DATA AGAIN.
# DOES NOT TRADE.
# DOES NOT USE POLYMARKET CREDENTIALS.
# ============================================================

VERSION = "1.0"

DATA_DIR = "data"

WEATHER_DIR = os.path.join(
    DATA_DIR,
    "weather",
)

CALIBRATION_DIR = os.path.join(
    DATA_DIR,
    "edge",
    "calibration",
)

STATION_REGISTRY_FILE = os.path.join(
    WEATHER_DIR,
    "station_registry.json",
)

RAW_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_observations_raw.csv",
)

OUTPUT_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_observations_daily_local.csv",
)

TIMEZONE_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_timezones.json",
)

REPORT_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_observations_local_report.txt",
)

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
)

HTTP_TIMEOUT = 60

BATCH_SIZE = 50

REQUEST_PAUSE_SECONDS = 1.0

MIN_DAILY_SAMPLES = 18


# ============================================================
# CSV FIELDS
# ============================================================

DAILY_FIELDS = [
    "station",
    "date",
    "min_c",
    "max_c",
    "sample_count",
    "timezone",
    "latitude",
    "longitude",
    "source",
]


# ============================================================
# HELPERS
# ============================================================

def iso_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def safe_float(value):
    try:
        if value is None or value == "":
            return None

        return float(value)

    except (
        ValueError,
        TypeError,
    ):
        return None


def read_csv(path):
    with open(
        path,
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        return list(
            csv.DictReader(handle)
        )


def write_csv(
    path,
    fields,
    rows,
):
    directory = os.path.dirname(
        path
    )

    if directory:
        os.makedirs(
            directory,
            exist_ok=True,
        )

    temporary = (
        path
        + ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(
                row
            )

    os.replace(
        temporary,
        path,
    )


def write_json(
    path,
    payload,
):
    directory = os.path.dirname(
        path
    )

    if directory:
        os.makedirs(
            directory,
            exist_ok=True,
        )

    temporary = (
        path
        + ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8",
    ) as handle:

        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
        )

        handle.write(
            "\n"
        )

    os.replace(
        temporary,
        path,
    )


def write_text(
    path,
    text,
):
    directory = os.path.dirname(
        path
    )

    if directory:
        os.makedirs(
            directory,
            exist_ok=True,
        )

    temporary = (
        path
        + ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8",
    ) as handle:

        handle.write(
            text
        )

    os.replace(
        temporary,
        path,
    )


def load_registry():
    with open(
        STATION_REGISTRY_FILE,
        "r",
        encoding="utf-8",
    ) as handle:

        payload = json.load(
            handle
        )

    stations = []

    for station, item in payload.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        latitude = safe_float(
            item.get(
                "latitude"
            )
        )

        longitude = safe_float(
            item.get(
                "longitude"
            )
        )

        if (
            latitude is None
            or longitude is None
        ):
            continue

        stations.append(
            {
                "station": str(
                    station
                ).upper(),

                "latitude": latitude,

                "longitude": longitude,
            }
        )

    stations.sort(
        key=lambda item:
        item["station"]
    )

    return stations


# ============================================================
# TIMEZONE LOOKUP
# ============================================================

def lookup_timezones(
    session,
    stations,
):
    latitude = ",".join(
        str(
            station[
                "latitude"
            ]
        )
        for station in stations
    )

    longitude = ",".join(
        str(
            station[
                "longitude"
            ]
        )
        for station in stations
    )

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m",
        "timezone": "auto",
    }

    print(
        "Consultando zonas horarias "
        "de las estaciones..."
    )

    response = session.get(
        OPEN_METEO_URL,
        params=params,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    payload = response.json()

    if isinstance(
        payload,
        list,
    ):
        locations = payload

    else:
        locations = [
            payload
        ]

    if len(locations) != len(
        stations
    ):
        raise RuntimeError(
            "La respuesta de Open-Meteo "
            "no coincide con el número "
            "de estaciones."
        )

    timezones = {}

    for index, location in enumerate(
        locations
    ):

        station = stations[
            index
        ][
            "station"
        ]

        tz = location.get(
            "timezone"
        )

        if not tz:
            raise RuntimeError(
                f"No se obtuvo timezone "
                f"para {station}."
            )

        timezones[
            station
        ] = tz

    return timezones


# ============================================================
# UTC PARSING
# ============================================================

def parse_utc_timestamp(
    value
):
    if not value:
        return None

    text = str(
        value
    ).strip()

    if text.endswith("Z"):
        text = (
            text[:-1]
            + "+00:00"
        )

    try:
        parsed = datetime.fromisoformat(
            text
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            timezone.utc
        )

    except Exception:
        return None


# ============================================================
# LOCAL DAILY AGGREGATION
# ============================================================

def aggregate_local(
    rows,
    timezones,
    registry,
):
    grouped = {}

    for row in rows:

        station = str(
            row.get(
                "station"
            )
        ).upper()

        if station not in timezones:
            continue

        timestamp = parse_utc_timestamp(
            row.get(
                "valid"
            )
        )

        temperature = safe_float(
            row.get(
                "tmpc"
            )
        )

        if (
            timestamp is None
            or temperature is None
        ):
            continue

        tz_name = timezones[
            station
        ]

        try:
            local_dt = timestamp.astimezone(
                ZoneInfo(
                    tz_name
                )
            )

        except Exception:
            continue

        local_date = (
            local_dt.date().isoformat()
        )

        key = (
            station,
            local_date,
        )

        grouped.setdefault(
            key,
            [],
        ).append(
            {
                "temperature": temperature,
                "timestamp": timestamp,
            }
        )

    daily = []

    for (
        station,
        target_date,
    ), observations in grouped.items():

        if len(
            observations
        ) < MIN_DAILY_SAMPLES:
            continue

        values = [
            item[
                "temperature"
            ]
            for item in observations
        ]

        station_info = registry.get(
            station
        )

        if station_info is None:
            continue

        daily.append(
            {
                "station": station,

                "date": target_date,

                "min_c": min(
                    values
                ),

                "max_c": max(
                    values
                ),

                "sample_count": len(
                    values
                ),

                "timezone": timezones[
                    station
                ],

                "latitude": station_info[
                    "latitude"
                ],

                "longitude": station_info[
                    "longitude"
                ],

                "source": (
                    "iem_asos_metar_local_day"
                ),
            }
        )

    daily.sort(
        key=lambda row: (
            row["station"],
            row["date"],
        )
    )

    return daily


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "POLYMARKET WEATHER "
        "STATION LOCALIZER V"
        + VERSION
    )

    print(
        "NO TRADING"
    )

    print(
        "NO POLYMARKET CREDENTIALS"
    )

    print("=" * 70)

    print(
        f"UTC: {iso_now()}"
    )

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if not os.path.exists(
        RAW_FILE
    ):
        raise FileNotFoundError(
            RAW_FILE
        )

    if not os.path.exists(
        STATION_REGISTRY_FILE
    ):
        raise FileNotFoundError(
            STATION_REGISTRY_FILE
        )

    # --------------------------------------------------------
    # LOAD
    # --------------------------------------------------------

    stations = load_registry()

    print(
        f"Stations: "
        f"{len(stations)}"
    )

    registry = {
        station[
            "station"
        ]: station
        for station in stations
    }

    raw_rows = read_csv(
        RAW_FILE
    )

    print(
        f"Raw observations: "
        f"{len(raw_rows)}"
    )

    # --------------------------------------------------------
    # TIMEZONES
    # --------------------------------------------------------

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent":
            "PolymarketWeatherEdgeLab/"
            + VERSION
        }
    )

    timezones = lookup_timezones(
        session,
        stations,
    )

    write_json(
        TIMEZONE_FILE,
        {
            "generated_at": iso_now(),
            "source": "open_meteo_timezone_auto",
            "stations": timezones,
        },
    )

    # --------------------------------------------------------
    # LOCAL AGGREGATION
    # --------------------------------------------------------

    daily_rows = aggregate_local(
        raw_rows,
        timezones,
        registry,
    )

    write_csv(
        OUTPUT_FILE,
        DAILY_FIELDS,
        daily_rows,
    )

    # --------------------------------------------------------
    # COVERAGE
    # --------------------------------------------------------

    coverage = {}

    for row in daily_rows:

        station = row[
            "station"
        ]

        coverage.setdefault(
            station,
            0,
        )

        coverage[
            station
        ] += 1

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = [
        (
            "POLYMARKET WEATHER "
            "STATION LOCALIZER V"
            + VERSION
        ),
        "",
        f"UTC: {iso_now()}",
        "",
        (
            f"Stations: "
            f"{len(stations)}"
        ),
        (
            f"Raw observations: "
            f"{len(raw_rows)}"
        ),
        (
            f"Local daily records: "
            f"{len(daily_rows)}"
        ),
        (
            f"Stations with local daily data: "
            f"{len(coverage)}"
        ),
        "",
        (
            "Daily date is calculated using "
            "the station's local timezone."
        ),
        "",
        "STATION COVERAGE",
        "",
    ]

    for station in sorted(
        coverage
    ):
        report.append(
            (
                f"{station}: "
                f"{coverage[station]} days | "
                f"{timezones[station]}"
            )
        )

    write_text(
        REPORT_FILE,
        "\n".join(
            report
        ),
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print("")
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(
        f"Stations: "
        f"{len(stations)}"
    )

    print(
        f"Raw observations: "
        f"{len(raw_rows)}"
    )

    print(
        f"Local daily records: "
        f"{len(daily_rows)}"
    )

    print(
        f"Stations with daily data: "
        f"{len(coverage)}"
    )

    print("")
    print(
        f"Local daily file: "
        f"{OUTPUT_FILE}"
    )

    print(
        f"Timezone file: "
        f"{TIMEZONE_FILE}"
    )

    print(
        f"Report: "
        f"{REPORT_FILE}"
    )


if __name__ == "__main__":
    main()

import csv
import json
import math
import os
import time
from datetime import datetime, timezone, timedelta
from statistics import mean

import requests


# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Station Observation Bootstrap V1.0
#
# PURPOSE:
# Build historical daily min/max temperatures from
# airport ASOS/AWOS/METAR observations for the same
# stations used by Polymarket weather markets.
#
# SOURCE:
# Iowa Environmental Mesonet (IEM)
#
# NO POLYMARKET CREDENTIALS
# NO TRADING
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

OUTPUT_RAW_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_observations_raw.csv",
)

OUTPUT_DAILY_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_observations_daily.csv",
)

OUTPUT_REPORT_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_observations_report.txt",
)

# ============================================================
# CONFIGURATION
# ============================================================

IEM_URL = (
    "https://mesonet.agron.iastate.edu/"
    "cgi-bin/request/asos.py"
)

# Start with 90 days, matching our current calibration
# bootstrap window.
HISTORY_DAYS = 90

# We request each station independently.
# This is slower, but much safer because IEM has different
# network availability depending on station/country.
REQUEST_PAUSE_SECONDS = 0.50

HTTP_TIMEOUT = 60

# Require enough hourly observations to call the daily
# min/max reasonably complete.
MIN_DAILY_SAMPLES = 18

RAW_FIELDS = [
    "station",
    "valid",
    "tmpf",
    "tmpc",
    "latitude",
    "longitude",
    "source",
]

DAILY_FIELDS = [
    "station",
    "date",
    "min_c",
    "max_c",
    "sample_count",
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


def fahrenheit_to_celsius(
    value
):
    return (
        value - 32.0
    ) * 5.0 / 9.0


def load_station_registry():
    with open(
        STATION_REGISTRY_FILE,
        "r",
        encoding="utf-8",
    ) as handle:
        payload = json.load(
            handle
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            "station_registry.json "
            "debe contener un objeto."
        )

    stations = []

    for key, item in payload.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        station = (
            item.get(
                "station"
            )
            or key
        )

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
            not station
            or latitude is None
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

                "name": item.get(
                    "name"
                ),

                "country": item.get(
                    "country"
                ),
            }
        )

    stations.sort(
        key=lambda item:
        item["station"]
    )

    return stations


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


def build_date_range():
    end_date = (
        datetime.now(
            timezone.utc
        ).date()
        - timedelta(
            days=1
        )
    )

    start_date = (
        end_date
        - timedelta(
            days=HISTORY_DAYS - 1
        )
    )

    return (
        start_date,
        end_date,
    )


# ============================================================
# IEM REQUEST
# ============================================================

def fetch_station_data(
    session,
    station,
    start_date,
    end_date,
):
    params = {
        "station": station,

        "data": (
            "tmpf"
        ),

        "year1": start_date.year,
        "month1": start_date.month,
        "day1": start_date.day,

        "year2": end_date.year,
        "month2": end_date.month,
        "day2": end_date.day,

        "tz": "Etc/UTC",

        "format": "onlycomma",

        "latlon": "yes",

        "elev": "no",

        "missing": "M",

        "trace": "T",

        "direct": "no",

        "report_type": "3",

        "report_type": "4",
    }

    for attempt in range(4):

        try:

            response = session.get(
                IEM_URL,
                params=params,
                timeout=HTTP_TIMEOUT,
            )

            response.raise_for_status()

            return response.text

        except requests.RequestException as exc:

            print(
                f"    Error {station}: "
                f"{exc}"
            )

            if attempt >= 3:
                return None

            wait_seconds = (
                5
                * (
                    attempt
                    + 1
                )
            )

            print(
                f"    Reintentando "
                f"en {wait_seconds}s..."
            )

            time.sleep(
                wait_seconds
            )

    return None


# ============================================================
# CSV PARSER
# ============================================================

def parse_iem_csv(
    text,
    station,
    latitude,
    longitude,
):
    if not text:
        return []

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        return []

    # Find first actual CSV header.
    header_index = None

    for index, line in enumerate(
        lines
    ):

        lowered = line.lower()

        if (
            "station" in lowered
            and "valid" in lowered
            and "tmpf" in lowered
        ):
            header_index = index
            break

    if header_index is None:
        return []

    csv_text = "\n".join(
        lines[
            header_index:
        ]
    )

    reader = csv.DictReader(
        csv_text.splitlines()
    )

    rows = []

    for item in reader:

        valid = (
            item.get(
                "valid"
            )
            or item.get(
                "time"
            )
        )

        tmpf = safe_float(
            item.get(
                "tmpf"
            )
        )

        if not valid:
            continue

        if tmpf is None:
            continue

        rows.append(
            {
                "station": station,

                "valid": valid,

                "tmpf": tmpf,

                "tmpc": fahrenheit_to_celsius(
                    tmpf
                ),

                "latitude": latitude,

                "longitude": longitude,

                "source": (
                    "iem_asos_metar"
                ),
            }
        )

    return rows


# ============================================================
# DAILY AGGREGATION
# ============================================================

def aggregate_daily(
    raw_rows
):
    grouped = {}

    for row in raw_rows:

        valid = row.get(
            "valid"
        )

        tmpc = safe_float(
            row.get(
                "tmpc"
            )
        )

        if (
            not valid
            or tmpc is None
        ):
            continue

        date_text = str(
            valid
        )[:10]

        key = (
            row["station"],
            date_text,
        )

        grouped.setdefault(
            key,
            [],
        ).append(
            row
        )

    daily = []

    for (
        station,
        date_text,
    ), rows in grouped.items():

        values = [
            safe_float(
                row["tmpc"]
            )
            for row in rows
        ]

        values = [
            value
            for value in values
            if value is not None
        ]

        if len(
            values
        ) < MIN_DAILY_SAMPLES:
            continue

        first = rows[0]

        daily.append(
            {
                "station": station,

                "date": date_text,

                "min_c": min(
                    values
                ),

                "max_c": max(
                    values
                ),

                "sample_count": len(
                    values
                ),

                "latitude": first[
                    "latitude"
                ],

                "longitude": first[
                    "longitude"
                ],

                "source": (
                    "iem_asos_metar"
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
        "STATION OBSERVATION "
        "BOOTSTRAP V"
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

    os.makedirs(
        CALIBRATION_DIR,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # STATIONS
    # --------------------------------------------------------

    stations = (
        load_station_registry()
    )

    print(
        f"Stations: "
        f"{len(stations)}"
    )

    if not stations:
        raise RuntimeError(
            "No hay estaciones."
        )

    # --------------------------------------------------------
    # DATE RANGE
    # --------------------------------------------------------

    start_date, end_date = (
        build_date_range()
    )

    print(
        f"Historical window: "
        f"{start_date} -> "
        f"{end_date}"
    )

    # --------------------------------------------------------
    # SESSION
    # --------------------------------------------------------

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "PolymarketWeatherEdgeLab/"
                + VERSION
            )
        }
    )

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    raw_rows = []

    successful = 0
    empty = 0
    failed = 0

    for index, station in enumerate(
        stations,
        start=1,
    ):

        station_id = (
            station[
                "station"
            ]
        )

        print(
            f"[{index}/{len(stations)}] "
            f"{station_id} | "
            f"{station.get('name')}"
        )

        text = fetch_station_data(
            session,
            station_id,
            start_date,
            end_date,
        )

        if text is None:
            failed += 1

            print(
                "    FAILED"
            )

            continue

        rows = parse_iem_csv(
            text,
            station_id,
            station[
                "latitude"
            ],
            station[
                "longitude"
            ],
        )

        if not rows:
            empty += 1

            print(
                "    NO DATA"
            )

            continue

        successful += 1

        raw_rows.extend(
            rows
        )

        print(
            f"    rows: "
            f"{len(rows)}"
        )

        time.sleep(
            REQUEST_PAUSE_SECONDS
        )

    # --------------------------------------------------------
    # DEDUP
    # --------------------------------------------------------

    raw_index = {}

    for row in raw_rows:

        key = (
            row[
                "station"
            ],
            row[
                "valid"
            ],
        )

        raw_index[
            key
        ] = row

    raw_rows = list(
        raw_index.values()
    )

    raw_rows.sort(
        key=lambda row: (
            row[
                "station"
            ],
            row[
                "valid"
            ],
        )
    )

    write_csv(
        OUTPUT_RAW_FILE,
        RAW_FIELDS,
        raw_rows,
    )

    # --------------------------------------------------------
    # DAILY
    # --------------------------------------------------------

    daily_rows = aggregate_daily(
        raw_rows
    )

    write_csv(
        OUTPUT_DAILY_FILE,
        DAILY_FIELDS,
        daily_rows,
    )

    # --------------------------------------------------------
    # COVERAGE
    # --------------------------------------------------------

    stations_with_daily = sorted(
        set(
            row[
                "station"
            ]
            for row in daily_rows
        )
    )

    coverage_days = {}

    for row in daily_rows:

        station = row[
            "station"
        ]

        coverage_days.setdefault(
            station,
            0,
        )

        coverage_days[
            station
        ] += 1

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = [
        (
            "POLYMARKET WEATHER "
            "STATION OBSERVATION "
            "BOOTSTRAP V"
            + VERSION
        ),

        "NO TRADING",

        "",

        f"UTC: {iso_now()}",

        "",

        (
            f"Stations requested: "
            f"{len(stations)}"
        ),

        (
            f"Stations successful: "
            f"{successful}"
        ),

        (
            f"Stations empty: "
            f"{empty}"
        ),

        (
            f"Stations failed: "
            f"{failed}"
        ),

        (
            f"Raw observations: "
            f"{len(raw_rows)}"
        ),

        (
            f"Daily station records: "
            f"{len(daily_rows)}"
        ),

        (
            f"Stations with daily data: "
            f"{len(stations_with_daily)}"
        ),

        "",

        (
            "SOURCE NOTE: IEM maintains an archive of "
            "ASOS/AWOS/METAR observations from around "
            "the world."
        ),

        (
            "Daily min/max are calculated from the "
            "available observations."
        ),

        (
            "Days with fewer than "
            f"{MIN_DAILY_SAMPLES} observations are "
            "excluded."
        ),

        "",
        "STATION COVERAGE",
        "",
    ]

    for station_id in sorted(
        coverage_days
    ):

        report.append(
            (
                f"{station_id}: "
                f"{coverage_days[station_id]} days"
            )
        )

    write_text(
        OUTPUT_REPORT_FILE,
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
        f"Stations requested: "
        f"{len(stations)}"
    )

    print(
        f"Stations successful: "
        f"{successful}"
    )

    print(
        f"Stations empty: "
        f"{empty}"
    )

    print(
        f"Stations failed: "
        f"{failed}"
    )

    print(
        f"Raw observations: "
        f"{len(raw_rows)}"
    )

    print(
        f"Daily records: "
        f"{len(daily_rows)}"
    )

    print(
        f"Stations with daily data: "
        f"{len(stations_with_daily)}"
    )

    print("")
    print(
        f"Raw file: "
        f"{OUTPUT_RAW_FILE}"
    )

    print(
        f"Daily file: "
        f"{OUTPUT_DAILY_FILE}"
    )

    print(
        f"Report: "
        f"{OUTPUT_REPORT_FILE}"
    )


if __name__ == "__main__":
    main()

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
# Calibration Bootstrap V1.1
#
# RESEARCH ONLY
# NO TRADING
# NO POLYMARKET CREDENTIALS
#
# PURPOSE:
# Build historical forecast-vs-ground-truth data
# for Weather Edge calibration.
#
# V1.1 changes:
# - Smaller API batches
# - Smaller historical windows
# - Explicit 429 handling
# - Retry-After support
# - ECMWF first
# - Safer request pacing
# ============================================================

VERSION = "1.1"

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

FORECAST_FILE = os.path.join(
    CALIBRATION_DIR,
    "bootstrap_forecasts.csv",
)

GROUND_TRUTH_FILE = os.path.join(
    CALIBRATION_DIR,
    "bootstrap_ground_truth.csv",
)

MATCHED_FILE = os.path.join(
    CALIBRATION_DIR,
    "bootstrap_matched.csv",
)

REPORT_FILE = os.path.join(
    CALIBRATION_DIR,
    "bootstrap_report.txt",
)

# ============================================================
# API
# ============================================================

PREVIOUS_RUNS_URL = (
    "https://previous-runs-api.open-meteo.com/v1/forecast"
)

ARCHIVE_URL = (
    "https://archive-api.open-meteo.com/v1/archive"
)

# Start with ECMWF only.
# Once this works correctly, we will add GFS.
MODELS = [
    "ecmwf_ifs025",
]

LEAD_DAYS = [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
]

# Start with 90 days.
HISTORY_DAYS = 90

# Date block for each API call.
DATE_CHUNK_DAYS = 30

# Smaller location batches reduce server-side request weight.
STATION_BATCH_SIZE = 5

# Request pacing.
REQUEST_PAUSE_SECONDS = 3.0

# 429 retry delays.
RATE_LIMIT_DELAYS = [
    30,
    60,
    120,
    180,
]

HTTP_TIMEOUT = 90

MIN_DAILY_SAMPLES = 18


# ============================================================
# CSV FIELDS
# ============================================================

FORECAST_FIELDS = [
    "station",
    "latitude",
    "longitude",
    "model",
    "lead_days",
    "valid_date",
    "forecast_min_c",
    "forecast_max_c",
    "forecast_source",
    "collected_at",
]

GROUND_TRUTH_FIELDS = [
    "station",
    "latitude",
    "longitude",
    "date",
    "actual_min_c",
    "actual_max_c",
    "sample_count",
    "ground_truth_source",
    "collected_at",
]

MATCHED_FIELDS = [
    "station",
    "latitude",
    "longitude",
    "model",
    "lead_days",
    "target_date",
    "forecast_min_c",
    "forecast_max_c",
    "actual_min_c",
    "actual_max_c",
    "error_min_c",
    "error_max_c",
    "abs_error_min_c",
    "abs_error_max_c",
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

    temporary = path + ".tmp"

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

    temporary = path + ".tmp"

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
            "no contiene un objeto."
        )

    stations = []

    for key, item in payload.items():

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
                "station": (
                    item.get(
                        "station"
                    )
                    or key
                ).upper(),

                "latitude": latitude,

                "longitude": longitude,

                "name": item.get(
                    "name"
                ),
            }
        )

    stations.sort(
        key=lambda item:
        item["station"]
    )

    return stations


def chunks(
    values,
    size,
):
    for index in range(
        0,
        len(values),
        size,
    ):
        yield values[
            index:index + size
        ]


def date_chunks(
    start_date,
    end_date,
):
    current = start_date

    while current <= end_date:

        chunk_end = min(
            end_date,
            current
            + timedelta(
                days=DATE_CHUNK_DAYS
                - 1
            ),
        )

        yield (
            current,
            chunk_end,
        )

        current = (
            chunk_end
            + timedelta(
                days=1
            )
        )


def coordinate_lists(
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

    return (
        latitude,
        longitude,
    )


# ============================================================
# HTTP
# ============================================================

def request_json(
    session,
    url,
    params,
):
    last_error = None

    for attempt in range(
        len(
            RATE_LIMIT_DELAYS
        ) + 1
    ):

        try:

            response = session.get(
                url,
                params=params,
                timeout=HTTP_TIMEOUT,
            )

            if (
                response.status_code
                == 429
            ):

                retry_after = response.headers.get(
                    "Retry-After"
                )

                if retry_after:
                    try:
                        wait_seconds = max(
                            30,
                            int(
                                float(
                                    retry_after
                                )
                            ),
                        )
                    except Exception:
                        wait_seconds = (
                            RATE_LIMIT_DELAYS[
                                min(
                                    attempt,
                                    len(
                                        RATE_LIMIT_DELAYS
                                    )
                                    - 1,
                                )
                            ]
                        )
                else:
                    wait_seconds = (
                        RATE_LIMIT_DELAYS[
                            min(
                                attempt,
                                len(
                                    RATE_LIMIT_DELAYS
                                )
                                - 1,
                            )
                        ]
                    )

                print(
                    "    HTTP 429. "
                    f"Esperando {wait_seconds}s..."
                )

                time.sleep(
                    wait_seconds
                )

                continue

            response.raise_for_status()

            return response.json()

        except requests.RequestException as exc:

            last_error = exc

            print(
                f"    Request error: "
                f"{exc}"
            )

            if attempt < len(
                RATE_LIMIT_DELAYS
            ):
                wait_seconds = (
                    RATE_LIMIT_DELAYS[
                        attempt
                    ]
                )

                print(
                    f"    Reintentando "
                    f"en {wait_seconds}s..."
                )

                time.sleep(
                    wait_seconds
                )

        except Exception as exc:

            last_error = exc
            break

    raise RuntimeError(
        f"API request failed: "
        f"{last_error}"
    )


# ============================================================
# PREVIOUS RUNS
# ============================================================

def fetch_previous_runs(
    session,
    stations,
    model,
    start_date,
    end_date,
):
    latitude, longitude = (
        coordinate_lists(
            stations
        )
    )

    hourly_variables = ",".join(
        [
            (
                "temperature_2m_"
                f"previous_day{lead}"
            )
            for lead in LEAD_DAYS
        ]
    )

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": hourly_variables,
        "models": model,
        "start_date": (
            start_date.isoformat()
        ),
        "end_date": (
            end_date.isoformat()
        ),
        "timezone": "auto",
    }

    return request_json(
        session,
        PREVIOUS_RUNS_URL,
        params,
    )


def parse_previous_runs(
    payload,
    stations,
    model,
):
    if isinstance(
        payload,
        list,
    ):
        locations = payload

    else:
        locations = [
            payload
        ]

    rows = []

    for index, location in enumerate(
        locations
    ):

        if index >= len(
            stations
        ):
            break

        station = stations[
            index
        ]

        hourly = location.get(
            "hourly",
            {}
        )

        times = hourly.get(
            "time",
            []
        )

        if not times:
            continue

        for lead in LEAD_DAYS:

            variable = (
                "temperature_2m_"
                f"previous_day{lead}"
            )

            values = hourly.get(
                variable,
                [],
            )

            if not values:
                continue

            daily = {}

            for i, timestamp in enumerate(
                times
            ):

                if i >= len(
                    values
                ):
                    break

                value = safe_float(
                    values[i]
                )

                if value is None:
                    continue

                target_date = str(
                    timestamp
                )[:10]

                daily.setdefault(
                    target_date,
                    [],
                ).append(
                    value
                )

            for target_date, values_for_day in (
                daily.items()
            ):

                if len(
                    values_for_day
                ) < MIN_DAILY_SAMPLES:
                    continue

                rows.append(
                    {
                        "station": (
                            station[
                                "station"
                            ]
                        ),

                        "latitude": (
                            station[
                                "latitude"
                            ]
                        ),

                        "longitude": (
                            station[
                                "longitude"
                            ]
                        ),

                        "model": model,

                        "lead_days": lead,

                        "valid_date": (
                            target_date
                        ),

                        "forecast_min_c": min(
                            values_for_day
                        ),

                        "forecast_max_c": max(
                            values_for_day
                        ),

                        "forecast_source": (
                            "open_meteo_previous_runs"
                        ),

                        "collected_at": (
                            iso_now()
                        ),
                    }
                )

    return rows


# ============================================================
# GROUND TRUTH
# ============================================================

def fetch_ground_truth(
    session,
    stations,
    start_date,
    end_date,
):
    latitude, longitude = (
        coordinate_lists(
            stations
        )
    )

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": (
            start_date.isoformat()
        ),
        "end_date": (
            end_date.isoformat()
        ),
        "hourly": (
            "temperature_2m"
        ),
        "timezone": "auto",
    }

    return request_json(
        session,
        ARCHIVE_URL,
        params,
    )


def parse_ground_truth(
    payload,
    stations,
):
    if isinstance(
        payload,
        list,
    ):
        locations = payload

    else:
        locations = [
            payload
        ]

    rows = []

    for index, location in enumerate(
        locations
    ):

        if index >= len(
            stations
        ):
            break

        station = stations[
            index
        ]

        hourly = location.get(
            "hourly",
            {}
        )

        times = hourly.get(
            "time",
            []
        )

        values = hourly.get(
            "temperature_2m",
            []
        )

        daily = {}

        for i, timestamp in enumerate(
            times
        ):

            if i >= len(
                values
            ):
                break

            value = safe_float(
                values[i]
            )

            if value is None:
                continue

            target_date = str(
                timestamp
            )[:10]

            daily.setdefault(
                target_date,
                [],
            ).append(
                value
            )

        for target_date, values_for_day in (
            daily.items()
        ):

            if len(
                values_for_day
            ) < MIN_DAILY_SAMPLES:
                continue

            rows.append(
                {
                    "station": (
                        station[
                            "station"
                        ]
                    ),

                    "latitude": (
                        station[
                            "latitude"
                        ]
                    ),

                    "longitude": (
                        station[
                            "longitude"
                        ]
                    ),

                    "date": target_date,

                    "actual_min_c": min(
                        values_for_day
                    ),

                    "actual_max_c": max(
                        values_for_day
                    ),

                    "sample_count": len(
                        values_for_day
                    ),

                    "ground_truth_source": (
                        "open_meteo_era5_reanalysis"
                    ),

                    "collected_at": (
                        iso_now()
                    ),
                }
            )

    return rows


# ============================================================
# MATCH
# ============================================================

def match_data(
    forecast_rows,
    ground_truth_rows,
):
    truth_index = {}

    for row in ground_truth_rows:
        truth_index[
            (
                row["station"],
                row["date"],
            )
        ] = row

    matched = []

    for row in forecast_rows:

        actual = truth_index.get(
            (
                row["station"],
                row["valid_date"],
            )
        )

        if actual is None:
            continue

        forecast_min = safe_float(
            row[
                "forecast_min_c"
            ]
        )

        forecast_max = safe_float(
            row[
                "forecast_max_c"
            ]
        )

        actual_min = safe_float(
            actual[
                "actual_min_c"
            ]
        )

        actual_max = safe_float(
            actual[
                "actual_max_c"
            ]
        )

        if (
            forecast_min is None
            or forecast_max is None
            or actual_min is None
            or actual_max is None
        ):
            continue

        error_min = (
            actual_min
            - forecast_min
        )

        error_max = (
            actual_max
            - forecast_max
        )

        matched.append(
            {
                "station": row[
                    "station"
                ],

                "latitude": row[
                    "latitude"
                ],

                "longitude": row[
                    "longitude"
                ],

                "model": row[
                    "model"
                ],

                "lead_days": row[
                    "lead_days"
                ],

                "target_date": row[
                    "valid_date"
                ],

                "forecast_min_c": (
                    forecast_min
                ),

                "forecast_max_c": (
                    forecast_max
                ),

                "actual_min_c": (
                    actual_min
                ),

                "actual_max_c": (
                    actual_max
                ),

                "error_min_c": (
                    error_min
                ),

                "error_max_c": (
                    error_max
                ),

                "abs_error_min_c": abs(
                    error_min
                ),

                "abs_error_max_c": abs(
                    error_max
                ),
            }
        )

    return matched


# ============================================================
# SUMMARY
# ============================================================

def summarize(
    matched_rows
):
    groups = {}

    for row in matched_rows:

        key = (
            row["station"],
            row["model"],
            row["lead_days"],
        )

        groups.setdefault(
            key,
            [],
        ).append(
            row
        )

    summaries = []

    for (
        station,
        model,
        lead_days,
    ), rows in groups.items():

        min_errors = [
            safe_float(
                row[
                    "error_min_c"
                ]
            )
            for row in rows
        ]

        max_errors = [
            safe_float(
                row[
                    "error_max_c"
                ]
            )
            for row in rows
        ]

        min_errors = [
            value
            for value in min_errors
            if value is not None
        ]

        max_errors = [
            value
            for value in max_errors
            if value is not None
        ]

        if not min_errors or not max_errors:
            continue

        min_bias = mean(
            min_errors
        )

        max_bias = mean(
            max_errors
        )

        min_rmse = math.sqrt(
            mean(
                [
                    value * value
                    for value in min_errors
                ]
            )
        )

        max_rmse = math.sqrt(
            mean(
                [
                    value * value
                    for value in max_errors
                ]
            )
        )

        summaries.append(
            {
                "station": station,
                "model": model,
                "lead_days": lead_days,
                "samples": len(
                    rows
                ),
                "min_bias_c": min_bias,
                "max_bias_c": max_bias,
                "min_rmse_c": min_rmse,
                "max_rmse_c": max_rmse,
                "first_date": min(
                    row[
                        "target_date"
                    ]
                    for row in rows
                ),
                "last_date": max(
                    row[
                        "target_date"
                    ]
                    for row in rows
                ),
            }
        )

    return summaries


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print(
        "POLYMARKET WEATHER "
        "CALIBRATION BOOTSTRAP V"
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
        f"Stations: {len(stations)}"
    )

    if not stations:
        raise RuntimeError(
            "No hay estaciones."
        )

    # --------------------------------------------------------
    # DATES
    # --------------------------------------------------------

    end_date = (
        datetime.now(
            timezone.utc
        ).date()
        - timedelta(
            days=2
        )
    )

    start_date = (
        end_date
        - timedelta(
            days=HISTORY_DAYS - 1
        )
    )

    print(
        f"Historical window: "
        f"{start_date} -> "
        f"{end_date}"
    )

    print(
        f"Models: "
        f"{', '.join(MODELS)}"
    )

    print(
        f"Lead days: "
        f"{LEAD_DAYS}"
    )

    print(
        f"Station batch: "
        f"{STATION_BATCH_SIZE}"
    )

    print(
        f"Date chunk: "
        f"{DATE_CHUNK_DAYS} days"
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

    station_batches = list(
        chunks(
            stations,
            STATION_BATCH_SIZE,
        )
    )

    date_ranges = list(
        date_chunks(
            start_date,
            end_date,
        )
    )

    forecast_rows = []

    total_forecast_calls = (
        len(MODELS)
        * len(station_batches)
        * len(date_ranges)
    )

    completed = 0

    # --------------------------------------------------------
    # FORECASTS
    # --------------------------------------------------------

    print("")
    print(
        "Downloading previous model runs..."
    )

    for model in MODELS:

        for (
            chunk_start,
            chunk_end,
        ) in date_ranges:

            for batch in station_batches:

                completed += 1

                print(
                    f"[{completed}/"
                    f"{total_forecast_calls}] "
                    f"{model} | "
                    f"{chunk_start} -> "
                    f"{chunk_end} | "
                    f"{len(batch)} stations"
                )

                payload = (
                    fetch_previous_runs(
                        session,
                        batch,
                        model,
                        chunk_start,
                        chunk_end,
                    )
                )

                rows = parse_previous_runs(
                    payload,
                    batch,
                    model,
                )

                forecast_rows.extend(
                    rows
                )

                print(
                    f"    rows: {len(rows)}"
                )

                time.sleep(
                    REQUEST_PAUSE_SECONDS
                )

    # --------------------------------------------------------
    # DEDUP FORECAST
    # --------------------------------------------------------

    forecast_index = {}

    for row in forecast_rows:

        key = (
            row["station"],
            row["model"],
            row["lead_days"],
            row["valid_date"],
        )

        forecast_index[
            key
        ] = row

    forecast_rows = list(
        forecast_index.values()
    )

    forecast_rows.sort(
        key=lambda row: (
            row["station"],
            row["model"],
            row["lead_days"],
            row["valid_date"],
        )
    )

    write_csv(
        FORECAST_FILE,
        FORECAST_FIELDS,
        forecast_rows,
    )

    print("")
    print(
        f"Forecast rows saved: "
        f"{len(forecast_rows)}"
    )

    # --------------------------------------------------------
    # GROUND TRUTH
    # --------------------------------------------------------

    ground_truth_rows = []

    total_truth_calls = (
        len(
            station_batches
        )
        * len(
            date_ranges
        )
    )

    completed = 0

    print("")
    print(
        "Downloading historical ground truth..."
    )

    for (
        chunk_start,
        chunk_end,
    ) in date_ranges:

        for batch in station_batches:

            completed += 1

            print(
                f"[{completed}/"
                f"{total_truth_calls}] "
                f"{chunk_start} -> "
                f"{chunk_end} | "
                f"{len(batch)} stations"
            )

            payload = (
                fetch_ground_truth(
                    session,
                    batch,
                    chunk_start,
                    chunk_end,
                )
            )

            rows = parse_ground_truth(
                payload,
                batch,
            )

            ground_truth_rows.extend(
                rows
            )

            print(
                f"    rows: {len(rows)}"
            )

            time.sleep(
                REQUEST_PAUSE_SECONDS
            )

    # --------------------------------------------------------
    # DEDUP GROUND TRUTH
    # --------------------------------------------------------

    truth_index = {}

    for row in ground_truth_rows:

        key = (
            row["station"],
            row["date"],
        )

        truth_index[
            key
        ] = row

    ground_truth_rows = list(
        truth_index.values()
    )

    ground_truth_rows.sort(
        key=lambda row: (
            row["station"],
            row["date"],
        )
    )

    write_csv(
        GROUND_TRUTH_FILE,
        GROUND_TRUTH_FIELDS,
        ground_truth_rows,
    )

    print("")
    print(
        f"Ground truth rows saved: "
        f"{len(ground_truth_rows)}"
    )

    # --------------------------------------------------------
    # MATCH
    # --------------------------------------------------------

    matched_rows = match_data(
        forecast_rows,
        ground_truth_rows,
    )

    matched_rows.sort(
        key=lambda row: (
            row["station"],
            row["model"],
            row["lead_days"],
            row["target_date"],
        )
    )

    write_csv(
        MATCHED_FILE,
        MATCHED_FIELDS,
        matched_rows,
    )

    print(
        f"Matched rows: "
        f"{len(matched_rows)}"
    )

    # --------------------------------------------------------
    # CALIBRATION SUMMARY
    # --------------------------------------------------------

    summaries = summarize(
        matched_rows
    )

    summaries.sort(
        key=lambda row: (
            row["station"],
            row["model"],
            row["lead_days"],
        )
    )

    print(
        f"Calibration groups: "
        f"{len(summaries)}"
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = [
        (
            "POLYMARKET WEATHER "
            "CALIBRATION BOOTSTRAP V"
            + VERSION
        ),
        "NO TRADING",
        "",
        f"UTC: {iso_now()}",
        "",
        f"Stations: {len(stations)}",
        f"Models: {len(MODELS)}",
        f"History days: {HISTORY_DAYS}",
        f"Forecast rows: {len(forecast_rows)}",
        (
            "Ground truth rows: "
            f"{len(ground_truth_rows)}"
        ),
        f"Matched rows: {len(matched_rows)}",
        f"Calibration groups: {len(summaries)}",
        "",
        (
            "Ground truth source: "
            "Open-Meteo ERA5 reanalysis."
        ),
        (
            "This is historical verification data, "
            "not literal METAR observations."
        ),
        "",
        "CALIBRATION GROUPS",
        "",
    ]

    for summary in summaries:

        report.append(
            (
                f"{summary['station']} | "
                f"{summary['model']} | "
                f"lead={summary['lead_days']} | "
                f"n={summary['samples']} | "
                f"min_bias="
                f"{summary['min_bias_c']:.3f}C | "
                f"min_rmse="
                f"{summary['min_rmse_c']:.3f}C | "
                f"max_bias="
                f"{summary['max_bias_c']:.3f}C | "
                f"max_rmse="
                f"{summary['max_rmse_c']:.3f}C"
            )
        )

    write_text(
        REPORT_FILE,
        "\n".join(
            report
        ),
    )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    print("")
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(
        f"Forecast rows: "
        f"{len(forecast_rows)}"
    )

    print(
        f"Ground truth rows: "
        f"{len(ground_truth_rows)}"
    )

    print(
        f"Matched rows: "
        f"{len(matched_rows)}"
    )

    print(
        f"Calibration groups: "
        f"{len(summaries)}"
    )

    print("")
    print(
        f"Forecast: "
        f"{FORECAST_FILE}"
    )

    print(
        f"Ground truth: "
        f"{GROUND_TRUTH_FILE}"
    )

    print(
        f"Matched: "
        f"{MATCHED_FILE}"
    )

    print(
        f"Report: "
        f"{REPORT_FILE}"
    )


if __name__ == "__main__":
    main()

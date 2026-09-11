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
# Calibration Bootstrap V1.0
#
# PURPOSE:
# Build historical forecast-vs-ground-truth data for
# calibration of the Weather Edge Engine.
#
# SOURCES:
#   - Open-Meteo Previous Runs API
#   - Open-Meteo Historical Weather API
#
# NO POLYMARKET CREDENTIALS
# NO TRADING
# NO ORDERS
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

BOOTSTRAP_FORECAST_FILE = os.path.join(
    CALIBRATION_DIR,
    "bootstrap_forecasts.csv",
)

BOOTSTRAP_GROUND_TRUTH_FILE = os.path.join(
    CALIBRATION_DIR,
    "bootstrap_ground_truth.csv",
)

BOOTSTRAP_MATCHED_FILE = os.path.join(
    CALIBRATION_DIR,
    "bootstrap_matched.csv",
)

BOOTSTRAP_REPORT_FILE = os.path.join(
    CALIBRATION_DIR,
    "bootstrap_report.txt",
)

# ============================================================
# CONFIGURATION
# ============================================================

OPEN_METEO_PREVIOUS_URL = (
    "https://previous-runs-api.open-meteo.com/v1/forecast"
)

OPEN_METEO_ARCHIVE_URL = (
    "https://archive-api.open-meteo.com/v1/archive"
)

# Models corresponding reasonably well with the live
# collector's current ECMWF/GFS concept.
MODELS = [
    "ecmwf_ifs025",
    "gfs_seamless",
]

# 1..7 days ahead.
LEAD_DAYS = [1, 2, 3, 4, 5, 6, 7]

# Start date.
#
# Open-Meteo documents most Previous Runs models from
# January 2024. We intentionally use the recent 365 days
# first to keep the bootstrap manageable.
HISTORY_DAYS = 365

# Maximum number of locations per HTTP request.
BATCH_SIZE = 10

HTTP_TIMEOUT = 60

REQUEST_PAUSE_SECONDS = 0.25

# Historical archive model used as verification/ground truth.
# This is NOT a literal METAR station observation.
GROUND_TRUTH_MODEL = "era5"

# We need enough daily hourly points before treating a
# daily min/max as valid.
MIN_DAILY_SAMPLES = 18


# ============================================================
# OUTPUT FIELDS
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

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def write_json(path, payload):
    directory = os.path.dirname(path)

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
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")

    os.replace(
        temporary,
        path,
    )


def write_text(path, text):
    directory = os.path.dirname(path)

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
        handle.write(text)

    os.replace(
        temporary,
        path,
    )


def write_csv(
    path,
    fields,
    rows,
):
    directory = os.path.dirname(path)

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


def load_station_registry():
    if not os.path.exists(
        STATION_REGISTRY_FILE
    ):
        raise FileNotFoundError(
            STATION_REGISTRY_FILE
        )

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
        raise ValueError(
            "station_registry.json no es un objeto JSON."
        )

    stations = {}

    for station, item in payload.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        lat = safe_float(
            item.get(
                "latitude"
            )
        )

        lon = safe_float(
            item.get(
                "longitude"
            )
        )

        if lat is None or lon is None:
            continue

        stations[
            str(station).upper()
        ] = {
            "station": str(
                station
            ).upper(),

            "latitude": lat,

            "longitude": lon,

            "name": item.get(
                "name"
            ),

            "country": item.get(
                "country"
            ),

            "elevation_m": safe_float(
                item.get(
                    "elevation_m"
                )
            ),
        }

    return stations


def batch_items(
    items,
    size,
):
    for index in range(
        0,
        len(items),
        size,
    ):
        yield items[
            index:index + size
        ]


def make_coordinates(
    stations,
):
    return (
        ",".join(
            str(
                item["latitude"]
            )
            for item in stations
        ),
        ",".join(
            str(
                item["longitude"]
            )
            for item in stations
        ),
    )


# ============================================================
# HTTP
# ============================================================

def get_json(
    session,
    url,
    params,
):
    last_error = None

    for attempt in range(3):

        try:

            response = session.get(
                url,
                params=params,
                timeout=HTTP_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            if attempt < 2:
                time.sleep(
                    2.0
                    * (attempt + 1)
                )

    raise RuntimeError(
        f"Request failed: {url} "
        f"{last_error}"
    )


# ============================================================
# PREVIOUS MODEL RUNS
# ============================================================

def fetch_previous_runs(
    session,
    stations,
    start_date,
    end_date,
    model,
):
    latitude, longitude = (
        make_coordinates(
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
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "auto",
    }

    print(
        f"  Previous Runs: "
        f"{model} | "
        f"{len(stations)} stations"
    )

    payload = get_json(
        session,
        OPEN_METEO_PREVIOUS_URL,
        params,
    )

    return payload


# ============================================================
# PREVIOUS RUN PARSING
# ============================================================

def parse_previous_runs_payload(
    payload,
    stations,
    model,
):
    """
    Converts hourly previous-run temperatures into
    daily forecast min/max values.

    Important:
    previous_dayN means the forecast was made N days
    before the valid time.
    """

    results = []

    if isinstance(
        payload,
        list,
    ):
        location_payloads = payload

    else:
        location_payloads = [
            payload
        ]

    for location_index, location in enumerate(
        location_payloads
    ):

        if location_index >= len(
            stations
        ):
            break

        station = stations[
            location_index
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

            key = (
                "temperature_2m_"
                f"previous_day{lead}"
            )

            values = hourly.get(
                key,
                [],
            )

            if not values:
                continue

            by_date = {}

            for index, timestamp in enumerate(
                times
            ):

                if index >= len(
                    values
                ):
                    break

                value = safe_float(
                    values[index]
                )

                if value is None:
                    continue

                target_date = str(
                    timestamp
                )[:10]

                by_date.setdefault(
                    target_date,
                    [],
                ).append(
                    value
                )

            for valid_date, daily_values in (
                by_date.items()
            ):

                if len(
                    daily_values
                ) < MIN_DAILY_SAMPLES:
                    continue

                results.append(
                    {
                        "station": station[
                            "station"
                        ],

                        "latitude": station[
                            "latitude"
                        ],

                        "longitude": station[
                            "longitude"
                        ],

                        "model": model,

                        "lead_days": lead,

                        "valid_date": valid_date,

                        "forecast_min_c": min(
                            daily_values
                        ),

                        "forecast_max_c": max(
                            daily_values
                        ),

                        "forecast_source": (
                            "open_meteo_previous_runs"
                        ),

                        "collected_at": iso_now(),
                    }
                )

    return results


# ============================================================
# HISTORICAL GROUND TRUTH
# ============================================================

def fetch_ground_truth(
    session,
    stations,
    start_date,
    end_date,
):
    latitude, longitude = (
        make_coordinates(
            stations
        )
    )

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "temperature_2m",
        "timezone": "auto",
        "models": GROUND_TRUTH_MODEL,
    }

    print(
        f"  Ground truth: "
        f"{len(stations)} stations"
    )

    payload = get_json(
        session,
        OPEN_METEO_ARCHIVE_URL,
        params,
    )

    return payload


def parse_ground_truth_payload(
    payload,
    stations,
):
    results = []

    if isinstance(
        payload,
        list,
    ):
        location_payloads = payload

    else:
        location_payloads = [
            payload
        ]

    for location_index, location in enumerate(
        location_payloads
    ):

        if location_index >= len(
            stations
        ):
            break

        station = stations[
            location_index
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

        for index, timestamp in enumerate(
            times
        ):

            if index >= len(
                values
            ):
                break

            value = safe_float(
                values[index]
            )

            if value is None:
                continue

            local_date = str(
                timestamp
            )[:10]

            daily.setdefault(
                local_date,
                [],
            ).append(
                value
            )

        for target_date, daily_values in (
            daily.items()
        ):

            if len(
                daily_values
            ) < MIN_DAILY_SAMPLES:
                continue

            results.append(
                {
                    "station": station[
                        "station"
                    ],

                    "latitude": station[
                        "latitude"
                    ],

                    "longitude": station[
                        "longitude"
                    ],

                    "date": target_date,

                    "actual_min_c": min(
                        daily_values
                    ),

                    "actual_max_c": max(
                        daily_values
                    ),

                    "sample_count": len(
                        daily_values
                    ),

                    "ground_truth_source": (
                        "open_meteo_era5_reanalysis"
                    ),

                    "collected_at": iso_now(),
                }
            )

    return results


# ============================================================
# MATCHING
# ============================================================

def create_ground_truth_index(
    rows,
):
    index = {}

    for row in rows:

        key = (
            row["station"],
            row["date"],
        )

        index[key] = row

    return index


def match_forecasts(
    forecast_rows,
    ground_truth_rows,
):
    ground_truth = (
        create_ground_truth_index(
            ground_truth_rows
        )
    )

    matched = []

    for row in forecast_rows:

        key = (
            row["station"],
            row["valid_date"],
        )

        actual = ground_truth.get(
            key
        )

        if actual is None:
            continue

        forecast_min = safe_float(
            row.get(
                "forecast_min_c"
            )
        )

        forecast_max = safe_float(
            row.get(
                "forecast_max_c"
            )
        )

        actual_min = safe_float(
            actual.get(
                "actual_min_c"
            )
        )

        actual_max = safe_float(
            actual.get(
                "actual_max_c"
            )
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

                "error_min_c": error_min,

                "error_max_c": error_max,

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
# STATISTICS
# ============================================================

def rmse(values):
    if not values:
        return None

    return math.sqrt(
        mean(
            [
                value * value
                for value in values
            ]
        )
    )


def calibration_summary(
    matched_rows,
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

        min_rmse = rmse(
            min_errors
        )

        max_rmse = rmse(
            max_errors
        )

        min_mae = mean(
            [
                abs(
                    value
                )
                for value in min_errors
            ]
        )

        max_mae = mean(
            [
                abs(
                    value
                )
                for value in max_errors
            ]
        )

        summaries.append(
            {
                "station": station,

                "model": model,

                "lead_days": lead_days,

                "samples": len(
                    rows
                ),

                "min_bias_c": (
                    min_bias
                ),

                "max_bias_c": (
                    max_bias
                ),

                "min_rmse_c": (
                    min_rmse
                ),

                "max_rmse_c": (
                    max_rmse
                ),

                "min_mae_c": (
                    min_mae
                ),

                "max_mae_c": (
                    max_mae
                ),

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
        "POLYMARKET WEATHER CALIBRATION BOOTSTRAP V"
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

    registry = (
        load_station_registry()
    )

    stations = list(
        registry.values()
    )

    stations.sort(
        key=lambda item:
        item["station"]
    )

    print(
        f"Stations: "
        f"{len(stations)}"
    )

    if not stations:
        raise RuntimeError(
            "No hay estaciones válidas."
        )

    # --------------------------------------------------------
    # DATES
    # --------------------------------------------------------

    end_date = (
        now_utc().date()
        - timedelta(
            days=2
        )
    )

    start_date = (
        end_date
        - timedelta(
            days=HISTORY_DAYS
            - 1
        )
    )

    start_date_text = (
        start_date.isoformat()
    )

    end_date_text = (
        end_date.isoformat()
    )

    print(
        f"Historical window: "
        f"{start_date_text} -> "
        f"{end_date_text}"
    )

    print(
        f"Models: "
        f"{', '.join(MODELS)}"
    )

    print(
        f"Lead days: "
        f"{LEAD_DAYS}"
    )

    # --------------------------------------------------------
    # HTTP SESSION
    # --------------------------------------------------------

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent":
            "PolymarketWeatherEdgeLab/"
            + VERSION
        }
    )

    # --------------------------------------------------------
    # FORECASTS
    # --------------------------------------------------------

    all_forecasts = []

    station_batches = list(
        batch_items(
            stations,
            BATCH_SIZE,
        )
    )

    print("")
    print(
        "Downloading historical forecasts..."
    )

    total_batches = (
        len(
            station_batches
        )
        * len(
            MODELS
        )
    )

    completed_batches = 0

    for model in MODELS:

        for batch in station_batches:

            completed_batches += 1

            print(
                f"[{completed_batches}/"
                f"{total_batches}] "
                f"{model}"
            )

            payload = fetch_previous_runs(
                session,
                batch,
                start_date_text,
                end_date_text,
                model,
            )

            rows = (
                parse_previous_runs_payload(
                    payload,
                    batch,
                    model,
                )
            )

            all_forecasts.extend(
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
    # DEDUP FORECASTS
    # --------------------------------------------------------

    forecast_index = {}

    for row in all_forecasts:

        key = (
            row["station"],
            row["model"],
            row["lead_days"],
            row["valid_date"],
        )

        forecast_index[
            key
        ] = row

    all_forecasts = list(
        forecast_index.values()
    )

    all_forecasts.sort(
        key=lambda row: (
            row["station"],
            row["model"],
            row["lead_days"],
            row["valid_date"],
        )
    )

    write_csv(
        BOOTSTRAP_FORECAST_FILE,
        FORECAST_FIELDS,
        all_forecasts,
    )

    print("")
    print(
        f"Forecast rows saved: "
        f"{len(all_forecasts)}"
    )

    # --------------------------------------------------------
    # GROUND TRUTH
    # --------------------------------------------------------

    all_ground_truth = []

    print("")
    print(
        "Downloading historical ground truth..."
    )

    completed_batches = 0

    for batch in station_batches:

        completed_batches += 1

        print(
            f"[{completed_batches}/"
            f"{len(station_batches)}]"
        )

        payload = fetch_ground_truth(
            session,
            batch,
            start_date_text,
            end_date_text,
        )

        rows = (
            parse_ground_truth_payload(
                payload,
                batch,
            )
        )

        all_ground_truth.extend(
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
    # DEDUP GROUND TRUTH
    # --------------------------------------------------------

    truth_index = {}

    for row in all_ground_truth:

        key = (
            row["station"],
            row["date"],
        )

        truth_index[
            key
        ] = row

    all_ground_truth = list(
        truth_index.values()
    )

    all_ground_truth.sort(
        key=lambda row: (
            row["station"],
            row["date"],
        )
    )

    write_csv(
        BOOTSTRAP_GROUND_TRUTH_FILE,
        GROUND_TRUTH_FIELDS,
        all_ground_truth,
    )

    print("")
    print(
        f"Ground truth rows saved: "
        f"{len(all_ground_truth)}"
    )

    # --------------------------------------------------------
    # MATCH
    # --------------------------------------------------------

    matched = match_forecasts(
        all_forecasts,
        all_ground_truth,
    )

    matched.sort(
        key=lambda row: (
            row["station"],
            row["model"],
            row["lead_days"],
            row["target_date"],
        )
    )

    write_csv(
        BOOTSTRAP_MATCHED_FILE,
        MATCHED_FIELDS,
        matched,
    )

    print(
        f"Matched rows: "
        f"{len(matched)}"
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    summaries = calibration_summary(
        matched
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

        f"Run UTC: {iso_now()}",

        "",

        (
            f"Stations: "
            f"{len(stations)}"
        ),

        (
            f"Models: "
            f"{len(MODELS)}"
        ),

        (
            f"Lead days: "
            f"{len(LEAD_DAYS)}"
        ),

        (
            f"History days: "
            f"{HISTORY_DAYS}"
        ),

        (
            f"Forecast rows: "
            f"{len(all_forecasts)}"
        ),

        (
            f"Ground truth rows: "
            f"{len(all_ground_truth)}"
        ),

        (
            f"Matched rows: "
            f"{len(matched)}"
        ),

        (
            f"Calibration groups: "
            f"{len(summaries)}"
        ),

        "",

        (
            "GROUND TRUTH NOTE: "
            "Open-Meteo ERA5 reanalysis is used as "
            "historical verification data. It is not "
            "a literal METAR station observation."
        ),

        "",
        "TOP CALIBRATION GROUPS",
        "",
    ]

    for summary in summaries:

        report.append(
            (
                f"{summary['station']} | "
                f"{summary['model']} | "
                f"lead={summary['lead_days']} | "
                f"n={summary['samples']} | "
                f"min_bias={summary['min_bias_c']:.3f}C | "
                f"min_rmse={summary['min_rmse_c']:.3f}C | "
                f"max_bias={summary['max_bias_c']:.3f}C | "
                f"max_rmse={summary['max_rmse_c']:.3f}C"
            )
        )

    write_text(
        BOOTSTRAP_REPORT_FILE,
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
        f"{len(all_forecasts)}"
    )

    print(
        f"Ground truth rows: "
        f"{len(all_ground_truth)}"
    )

    print(
        f"Matched rows: "
        f"{len(matched)}"
    )

    print(
        f"Calibration groups: "
        f"{len(summaries)}"
    )

    print("")
    print(
        "Forecast file:"
    )
    print(
        BOOTSTRAP_FORECAST_FILE
    )

    print(
        "Ground truth file:"
    )
    print(
        BOOTSTRAP_GROUND_TRUTH_FILE
    )

    print(
        "Matched file:"
    )
    print(
        BOOTSTRAP_MATCHED_FILE
    )

    print(
        "Report:"
    )
    print(
        BOOTSTRAP_REPORT_FILE
    )


if __name__ == "__main__":
    main()

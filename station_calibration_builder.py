import csv
import json
import math
import os
from collections import defaultdict
from statistics import mean


# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Station Calibration Builder V1.0
#
# PURPOSE:
# Cross historical ECMWF forecasts against historical
# same-station ASOS/AWOS/METAR observations.
#
# INPUT:
#   data/edge/calibration/bootstrap_forecasts.csv
#   data/edge/calibration/station_observations_daily_local.csv
#
# OUTPUT:
#   data/edge/calibration/station_calibration_matches.csv
#   data/edge/calibration/station_calibration_summary.csv
#   data/edge/calibration/station_calibration_report.txt
#
# NO TRADING
# NO POLYMARKET CREDENTIALS
# ============================================================

VERSION = "1.0"

DATA_DIR = "data"

CALIBRATION_DIR = os.path.join(
    DATA_DIR,
    "edge",
    "calibration",
)

FORECAST_FILE = os.path.join(
    CALIBRATION_DIR,
    "bootstrap_forecasts.csv",
)

OBSERVATION_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_observations_daily_local.csv",
)

MATCHED_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_calibration_matches.csv",
)

SUMMARY_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_calibration_summary.csv",
)

REPORT_FILE = os.path.join(
    CALIBRATION_DIR,
    "station_calibration_report.txt",
)

# Minimum amount of matched observations required
# before a station/lead/model calibration is considered
# sufficiently useful for the next Edge Engine.
MIN_CALIBRATION_SAMPLES = 15

# We don't allow calibration values outside these
# sanity limits to directly determine the live sigma.
MIN_SIGMA_C = 0.75
MAX_SIGMA_C = 5.00


# ============================================================
# CSV FIELDS
# ============================================================

MATCHED_FIELDS = [
    "station",
    "model",
    "lead_days",
    "target_date",
    "forecast_min_c",
    "actual_min_c",
    "error_min_c",
    "abs_error_min_c",
    "forecast_max_c",
    "actual_max_c",
    "error_max_c",
    "abs_error_max_c",
    "forecast_source",
    "observation_source",
]

SUMMARY_FIELDS = [
    "station",
    "model",
    "lead_days",
    "samples",
    "usable",
    "first_date",
    "last_date",
    "min_bias_c",
    "min_mae_c",
    "min_rmse_c",
    "min_sigma_c",
    "max_bias_c",
    "max_mae_c",
    "max_rmse_c",
    "max_sigma_c",
]


# ============================================================
# HELPERS
# ============================================================

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


def safe_int(value):
    try:
        if value is None or value == "":
            return None

        return int(float(value))

    except (
        ValueError,
        TypeError,
    ):
        return None


def read_csv(path):
    if not os.path.exists(path):
        raise FileNotFoundError(
            path
        )

    with open(
        path,
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        return list(
            csv.DictReader(
                handle
            )
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


def rounded(
    value,
    digits=5,
):
    if value is None:
        return None

    return round(
        float(value),
        digits,
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_rmse(
    values
):
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


def calculate_mae(
    values
):
    if not values:
        return None

    return mean(
        [
            abs(
                value
            )
            for value in values
        ]
    )


def calculate_sample_sigma(
    values
):
    if len(values) <= 1:
        return None

    average = mean(
        values
    )

    variance = (
        sum(
            (
                value
                - average
            ) ** 2
            for value in values
        )
        / (
            len(values)
            - 1
        )
    )

    return math.sqrt(
        max(
            0.0,
            variance,
        )
    )


def bounded_sigma(
    value
):
    if value is None:
        return None

    return max(
        MIN_SIGMA_C,
        min(
            MAX_SIGMA_C,
            value,
        ),
    )


# ============================================================
# LOAD / INDEX
# ============================================================

def build_observation_index(
    rows
):
    index = {}

    for row in rows:

        station = (
            row.get(
                "station"
            )
            or ""
        ).upper()

        target_date = (
            row.get(
                "date"
            )
            or ""
        )

        if (
            not station
            or not target_date
        ):
            continue

        index[
            (
                station,
                target_date,
            )
        ] = row

    return index


# ============================================================
# MATCH FORECASTS TO STATION OBSERVATIONS
# ============================================================

def build_matches(
    forecast_rows,
    observation_index,
):
    matches = []

    skipped_no_observation = 0
    skipped_invalid = 0

    for forecast in forecast_rows:

        station = (
            forecast.get(
                "station"
            )
            or ""
        ).upper()

        model = (
            forecast.get(
                "model"
            )
            or ""
        )

        lead_days = safe_int(
            forecast.get(
                "lead_days"
            )
        )

        target_date = (
            forecast.get(
                "valid_date"
            )
            or ""
        )

        if (
            not station
            or not model
            or lead_days is None
            or not target_date
        ):
            skipped_invalid += 1
            continue

        observation = observation_index.get(
            (
                station,
                target_date,
            )
        )

        if observation is None:
            skipped_no_observation += 1
            continue

        forecast_min = safe_float(
            forecast.get(
                "forecast_min_c"
            )
        )

        forecast_max = safe_float(
            forecast.get(
                "forecast_max_c"
            )
        )

        actual_min = safe_float(
            observation.get(
                "min_c"
            )
        )

        actual_max = safe_float(
            observation.get(
                "max_c"
            )
        )

        if (
            forecast_min is None
            or forecast_max is None
            or actual_min is None
            or actual_max is None
        ):
            skipped_invalid += 1
            continue

        error_min = (
            actual_min
            - forecast_min
        )

        error_max = (
            actual_max
            - forecast_max
        )

        matches.append(
            {
                "station": station,

                "model": model,

                "lead_days": lead_days,

                "target_date": target_date,

                "forecast_min_c": rounded(
                    forecast_min,
                    5,
                ),

                "actual_min_c": rounded(
                    actual_min,
                    5,
                ),

                "error_min_c": rounded(
                    error_min,
                    5,
                ),

                "abs_error_min_c": rounded(
                    abs(
                        error_min
                    ),
                    5,
                ),

                "forecast_max_c": rounded(
                    forecast_max,
                    5,
                ),

                "actual_max_c": rounded(
                    actual_max,
                    5,
                ),

                "error_max_c": rounded(
                    error_max,
                    5,
                ),

                "abs_error_max_c": rounded(
                    abs(
                        error_max
                    ),
                    5,
                ),

                "forecast_source": (
                    forecast.get(
                        "forecast_source"
                    )
                    or "unknown"
                ),

                "observation_source": (
                    observation.get(
                        "source"
                    )
                    or "unknown"
                ),
            }
        )

    return (
        matches,
        skipped_no_observation,
        skipped_invalid,
    )


# ============================================================
# CALIBRATION SUMMARY
# ============================================================

def summarize_matches(
    matches
):
    grouped = defaultdict(
        list
    )

    for row in matches:

        key = (
            row[
                "station"
            ],

            row[
                "model"
            ],

            row[
                "lead_days"
            ],
        )

        grouped[
            key
        ].append(
            row
        )

    summaries = []

    for (
        station,
        model,
        lead_days,
    ), rows in grouped.items():

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

        if (
            not min_errors
            or not max_errors
        ):
            continue

        min_bias = mean(
            min_errors
        )

        max_bias = mean(
            max_errors
        )

        min_mae = calculate_mae(
            min_errors
        )

        max_mae = calculate_mae(
            max_errors
        )

        min_rmse = calculate_rmse(
            min_errors
        )

        max_rmse = calculate_rmse(
            max_errors
        )

        min_sigma = bounded_sigma(
            calculate_sample_sigma(
                min_errors
            )
        )

        max_sigma = bounded_sigma(
            calculate_sample_sigma(
                max_errors
            )
        )

        samples = len(
            rows
        )

        usable = (
            samples
            >= MIN_CALIBRATION_SAMPLES
        )

        summaries.append(
            {
                "station": station,

                "model": model,

                "lead_days": lead_days,

                "samples": samples,

                "usable": (
                    "YES"
                    if usable
                    else "NO"
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

                "min_bias_c": rounded(
                    min_bias
                ),

                "min_mae_c": rounded(
                    min_mae
                ),

                "min_rmse_c": rounded(
                    min_rmse
                ),

                "min_sigma_c": rounded(
                    min_sigma
                ),

                "max_bias_c": rounded(
                    max_bias
                ),

                "max_mae_c": rounded(
                    max_mae
                ),

                "max_rmse_c": rounded(
                    max_rmse
                ),

                "max_sigma_c": rounded(
                    max_sigma
                ),
            }
        )

    summaries.sort(
        key=lambda row: (
            row["station"],
            row["model"],
            row["lead_days"],
        )
    )

    return summaries


# ============================================================
# SANITY CHECKS
# ============================================================

def sanity_checks(
    summaries
):
    results = []

    for row in summaries:

        min_rmse = safe_float(
            row[
                "min_rmse_c"
            ]
        )

        max_rmse = safe_float(
            row[
                "max_rmse_c"
            ]
        )

        min_bias = safe_float(
            row[
                "min_bias_c"
            ]
        )

        max_bias = safe_float(
            row[
                "max_bias_c"
            ]
        )

        flags = []

        if (
            min_rmse is not None
            and min_rmse > 4.0
        ):
            flags.append(
                "HIGH_MIN_RMSE"
            )

        if (
            max_rmse is not None
            and max_rmse > 4.0
        ):
            flags.append(
                "HIGH_MAX_RMSE"
            )

        if (
            min_bias is not None
            and abs(
                min_bias
            ) > 3.0
        ):
            flags.append(
                "HIGH_MIN_BIAS"
            )

        if (
            max_bias is not None
            and abs(
                max_bias
            ) > 3.0
        ):
            flags.append(
                "HIGH_MAX_BIAS"
            )

        results.append(
            {
                "station": row[
                    "station"
                ],

                "model": row[
                    "model"
                ],

                "lead_days": row[
                    "lead_days"
                ],

                "flags": (
                    ",".join(
                        flags
                    )
                    if flags
                    else "OK"
                ),
            }
        )

    return results


# ============================================================
# REPORT
# ============================================================

def build_report(
    summaries,
    checks,
    matched_count,
    skipped_no_observation,
    skipped_invalid,
):
    usable = [
        row
        for row in summaries
        if row[
            "usable"
        ]
        == "YES"
    ]

    flagged = [
        row
        for row in checks
        if row[
            "flags"
        ]
        != "OK"
    ]

    report = [
        (
            "POLYMARKET WEATHER "
            "STATION CALIBRATION "
            "BUILDER V"
            + VERSION
        ),

        "NO TRADING",

        "",

        (
            f"Matched forecast/observation rows: "
            f"{matched_count}"
        ),

        (
            f"Calibration groups: "
            f"{len(summaries)}"
        ),

        (
            f"Usable groups (>={MIN_CALIBRATION_SAMPLES} samples): "
            f"{len(usable)}"
        ),

        (
            f"Groups with sanity flags: "
            f"{len(flagged)}"
        ),

        (
            f"Skipped without observation: "
            f"{skipped_no_observation}"
        ),

        (
            f"Skipped invalid: "
            f"{skipped_invalid}"
        ),

        "",
        "TOP USABLE CALIBRATION GROUPS",
        "",
    ]

    usable_sorted = sorted(
        usable,
        key=lambda row: (
            row[
                "max_rmse_c"
            ]
            if row[
                "max_rmse_c"
            ] is not None
            else 999.0
        ),
    )

    for row in usable_sorted[
        :100
    ]:

        report.append(
            (
                f"{row['station']} | "
                f"{row['model']} | "
                f"lead={row['lead_days']} | "
                f"n={row['samples']} | "
                f"min_bias={row['min_bias_c']}C | "
                f"min_rmse={row['min_rmse_c']}C | "
                f"min_sigma={row['min_sigma_c']}C | "
                f"max_bias={row['max_bias_c']}C | "
                f"max_rmse={row['max_rmse_c']}C | "
                f"max_sigma={row['max_sigma_c']}C"
            )
        )

    report.extend(
        [
            "",
            "SANITY FLAGS",
            "",
        ]
    )

    for row in flagged[
        :100
    ]:

        report.append(
            (
                f"{row['station']} | "
                f"{row['model']} | "
                f"lead={row['lead_days']} | "
                f"{row['flags']}"
            )
        )

    return "\n".join(
        report
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "POLYMARKET WEATHER "
        "STATION CALIBRATION "
        "BUILDER V"
        + VERSION
    )

    print(
        "NO TRADING"
    )

    print(
        "NO POLYMARKET CREDENTIALS"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # INPUTS
    # --------------------------------------------------------

    print(
        "Loading forecast history..."
    )

    forecast_rows = read_csv(
        FORECAST_FILE
    )

    print(
        f"Forecast rows: "
        f"{len(forecast_rows)}"
    )

    print(
        "Loading local station observations..."
    )

    observation_rows = read_csv(
        OBSERVATION_FILE
    )

    print(
        f"Observation rows: "
        f"{len(observation_rows)}"
    )

    # --------------------------------------------------------
    # INDEX
    # --------------------------------------------------------

    observation_index = (
        build_observation_index(
            observation_rows
        )
    )

    print(
        f"Observation index: "
        f"{len(observation_index)}"
    )

    # --------------------------------------------------------
    # MATCH
    # --------------------------------------------------------

    (
        matches,
        skipped_no_observation,
        skipped_invalid,
    ) = build_matches(
        forecast_rows,
        observation_index,
    )

    print(
        f"Matched rows: "
        f"{len(matches)}"
    )

    print(
        f"Skipped without observation: "
        f"{skipped_no_observation}"
    )

    print(
        f"Skipped invalid: "
        f"{skipped_invalid}"
    )

    write_csv(
        MATCHED_FILE,
        MATCHED_FIELDS,
        matches,
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    summaries = summarize_matches(
        matches
    )

    print(
        f"Calibration groups: "
        f"{len(summaries)}"
    )

    usable_count = sum(
        1
        for row in summaries
        if row[
            "usable"
        ]
        == "YES"
    )

    print(
        f"Usable groups: "
        f"{usable_count}"
    )

    write_csv(
        SUMMARY_FILE,
        SUMMARY_FIELDS,
        summaries,
    )

    # --------------------------------------------------------
    # SANITY CHECKS
    # --------------------------------------------------------

    checks = sanity_checks(
        summaries
    )

    flagged_count = sum(
        1
        for row in checks
        if row[
            "flags"
        ]
        != "OK"
    )

    print(
        f"Sanity flagged groups: "
        f"{flagged_count}"
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        summaries,
        checks,
        len(matches),
        skipped_no_observation,
        skipped_invalid,
    )

    write_text(
        REPORT_FILE,
        report,
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
        f"Station observation rows: "
        f"{len(observation_rows)}"
    )

    print(
        f"Matched rows: "
        f"{len(matches)}"
    )

    print(
        f"Calibration groups: "
        f"{len(summaries)}"
    )

    print(
        f"Usable groups: "
        f"{usable_count}"
    )

    print(
        f"Sanity flagged: "
        f"{flagged_count}"
    )

    print("")
    print(
        f"Matches: "
        f"{MATCHED_FILE}"
    )

    print(
        f"Summary: "
        f"{SUMMARY_FILE}"
    )

    print(
        f"Report: "
        f"{REPORT_FILE}"
    )


if __name__ == "__main__":
    main()

import csv
import json
import math
import os
from datetime import datetime, timezone, date, time
from statistics import mean
from zoneinfo import ZoneInfo

# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Edge Engine V1.3
#
# RESEARCH / PAPER TRADING ONLY
# NO ORDERS ARE SENT
#
# V1.3:
# - Historical forecast calibration
# - Bias correction
# - Historical residual sigma
# - Lead-time aware calibration
# - No-lookahead filter for calibration
# - Model-by-model corrected consensus
# - Family normalization
# ============================================================

ENGINE_VERSION = "1.3"

DATA_DIR = "data"

ACTIVE_FILE = os.path.join(
    DATA_DIR,
    "active_temperature_markets.json",
)

FORECAST_FILE = os.path.join(
    DATA_DIR,
    "weather",
    "history",
    "forecasts.csv",
)

OBSERVATION_FILE = os.path.join(
    DATA_DIR,
    "weather",
    "history",
    "observations.csv",
)

STATION_REGISTRY_FILE = os.path.join(
    DATA_DIR,
    "weather",
    "station_registry.json",
)

EDGE_DIR = os.path.join(
    DATA_DIR,
    "edge",
)

EDGE_HISTORY_DIR = os.path.join(
    EDGE_DIR,
    "history",
)

CALIBRATION_DIR = os.path.join(
    EDGE_DIR,
    "calibration",
)

LATEST_FILE = os.path.join(
    EDGE_DIR,
    "latest_signals.json",
)

REPORT_FILE = os.path.join(
    EDGE_DIR,
    "latest_report.txt",
)

SIGNAL_HISTORY_FILE = os.path.join(
    EDGE_HISTORY_DIR,
    "signals.csv",
)

PAPER_SIGNAL_FILE = os.path.join(
    EDGE_DIR,
    "paper_signals.csv",
)

CALIBRATION_LATEST_FILE = os.path.join(
    CALIBRATION_DIR,
    "latest_calibration.json",
)

CALIBRATION_REPORT_FILE = os.path.join(
    CALIBRATION_DIR,
    "latest_calibration_report.txt",
)

CALIBRATION_HISTORY_FILE = os.path.join(
    CALIBRATION_DIR,
    "history.csv",
)

# ============================================================
# STRATEGY PARAMETERS
# ============================================================

DEFAULT_WEATHER_FEE_RATE = 0.05

DEFAULT_SIGMA_C = 1.20
MIN_SIGMA_C = 0.90
MAX_SIGMA_C = 4.00

# Candidate thresholds.
MIN_EDGE = 0.03
MIN_EV = 0.015

# Strong signal.
STRONG_MIN_EDGE = 0.06
STRONG_MIN_EV = 0.02
STRONG_MIN_ASK = 0.005

# Very cheap asks.
THIN_ASK_MAX = 0.002

# Family consistency.
FAMILY_MIN_COVERAGE = 0.90
FAMILY_MAX_COVERAGE = 1.05

# Calibration.
CALIBRATION_MIN_SAMPLES = 15
CALIBRATION_GLOBAL_MIN_SAMPLES = 10
CALIBRATION_LOOKBACK_DAYS = 365

# Lead-time buckets:
# 0 = forecast issued before target day but within 24h
# 1 = approximately one day
# 2 = approximately two days
# 3 = 3+ days
MAX_LEAD_BUCKET = 3

# Shrink tiny samples toward the default.
SHRINK_MIN_SAMPLES = 15
SHRINK_TARGET_SIGMA_C = DEFAULT_SIGMA_C

REQUIRE_EXECUTABLE_ASK = True

MAX_SIGNALS_PER_RUN = 100
MAX_TOP_PAPER_SIGNALS = 50

SIGNAL_FIELDS = [
    "run_at",
    "city",
    "station",
    "market_date",
    "market_type",
    "unit",
    "market_id",
    "event_key",
    "bucket_type",
    "bucket_value",
    "bucket_low",
    "bucket_high",
    "group_title",
    "model_probability_raw",
    "model_probability",
    "family_exclusive_probability_sum",
    "family_probability_status",
    "market_probability",
    "entry_price",
    "entry_source",
    "fee_rate",
    "fee_source",
    "fee_per_share",
    "gross_edge",
    "net_ev_per_share",
    "net_return_if_win",
    "forecast_count",
    "forecast_models",
    "forecast_mean_c",
    "forecast_min_c",
    "forecast_max_c",
    "forecast_std_c",
    "forecast_sigma_c",
    "calibration_method",
    "calibration_samples",
    "calibration_bias_c",
    "calibration_sigma_c",
    "calibration_lead_bucket",
    "observation_temperature_c",
    "observation_time",
    "execution_quality",
    "signal",
    "reason",
]

CALIBRATION_FIELDS = [
    "run_at",
    "station",
    "model",
    "market_type",
    "lead_bucket",
    "samples",
    "bias_c",
    "rmse_c",
    "sigma_c",
    "mean_abs_error_c",
    "first_target_date",
    "last_target_date",
    "source_level",
]

# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


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


def read_json(path):
    with open(
        path,
        "r",
        encoding="utf-8",
    ) as handle:
        return json.load(handle)


def read_csv(path):
    if not os.path.exists(path):
        return []

    with open(
        path,
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        return list(
            csv.DictReader(handle)
        )


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
        encoding="utf-8",
        newline="",
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


def rounded(
    value,
    digits=6,
):
    if value is None:
        return None

    return round(
        float(value),
        digits,
    )


# ============================================================
# DATE / TIME HELPERS
# ============================================================

def parse_datetime(value):
    if not value:
        return None

    text = str(value).strip()

    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    candidates = [
        text,
        text.replace(
            " ",
            "T",
            1,
        ),
    ]

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(
                candidate
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(
                    tzinfo=timezone.utc
                )

            return parsed.astimezone(
                timezone.utc
            )

        except Exception:
            continue

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(
                text,
                fmt,
            )

            return parsed.replace(
                tzinfo=timezone.utc
            )

        except Exception:
            continue

    return None


def parse_date(value):
    if not value:
        return None

    text = str(value).strip()

    if not text:
        return None

    try:
        return datetime.strptime(
            text[:10],
            "%Y-%m-%d",
        ).date()

    except Exception:
        return None


def get_row_timestamp(row):
    for key in (
        "observation_time",
        "valid_time",
        "timestamp",
        "collected_at",
        "time",
        "datetime",
    ):
        value = row.get(key)

        parsed = parse_datetime(
            value
        )

        if parsed:
            return parsed

    return None


def extract_row_date(
    row,
    timezone_name=None,
):
    for key in (
        "observation_date",
        "local_date",
        "date",
        "market_date",
        "target_date",
    ):
        value = row.get(key)

        parsed = parse_date(
            value
        )

        if parsed:
            return parsed

    timestamp = get_row_timestamp(
        row
    )

    if timestamp is None:
        return None

    if timezone_name:
        try:
            local_dt = timestamp.astimezone(
                ZoneInfo(
                    timezone_name
                )
            )

            return local_dt.date()

        except Exception:
            pass

    return timestamp.date()


# ============================================================
# STATION REGISTRY
# ============================================================

def load_station_registry():
    if not os.path.exists(
        STATION_REGISTRY_FILE
    ):
        return {}

    try:
        payload = read_json(
            STATION_REGISTRY_FILE
        )
    except Exception:
        return {}

    if isinstance(
        payload,
        dict,
    ):
        if isinstance(
            payload.get("stations"),
            list,
        ):
            result = {}

            for item in payload[
                "stations"
            ]:
                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                station = (
                    item.get("station")
                    or item.get("icao")
                    or item.get("id")
                )

                if station:
                    result[
                        str(station).upper()
                    ] = item

            return result

        result = {}

        for key, value in payload.items():
            if isinstance(
                value,
                dict,
            ):
                result[
                    str(key).upper()
                ] = value

        return result

    if isinstance(
        payload,
        list,
    ):
        result = {}

        for item in payload:
            if not isinstance(
                item,
                dict,
            ):
                continue

            station = (
                item.get("station")
                or item.get("icao")
                or item.get("id")
            )

            if station:
                result[
                    str(station).upper()
                ] = item

        return result

    return {}


def get_station_timezone(
    station,
    registry,
):
    if not station:
        return None

    item = registry.get(
        str(station).upper()
    )

    if not isinstance(
        item,
        dict,
    ):
        return None

    for key in (
        "timezone",
        "tz",
        "iana_timezone",
        "time_zone",
    ):
        value = item.get(key)

        if value:
            return str(
                value
            )

    return None


# ============================================================
# TEMPERATURE UNITS
# ============================================================

def fahrenheit_to_celsius(value):
    return (
        value - 32.0
    ) * 5.0 / 9.0


def normalize_bucket_to_celsius(
    market
):
    normalized = dict(
        market
    )

    unit = str(
        market.get(
            "temperature_unit"
        )
        or market.get(
            "unit"
        )
        or "C"
    ).upper()

    if unit != "F":
        return normalized

    for key in (
        "bucket_value",
        "bucket_low",
        "bucket_high",
    ):
        value = safe_float(
            market.get(key)
        )

        normalized[key] = (
            None
            if value is None
            else fahrenheit_to_celsius(
                value
            )
        )

    return normalized


# ============================================================
# NORMAL DISTRIBUTION
# ============================================================

def normal_cdf(
    x,
    mean_value,
    sigma,
):
    if sigma <= 0:
        return None

    z = (
        x - mean_value
    ) / (
        sigma * math.sqrt(2.0)
    )

    return 0.5 * (
        1.0
        + math.erf(z)
    )


def clamp_probability(
    value
):
    if value is None:
        return None

    return max(
        0.0,
        min(
            1.0,
            float(value),
        ),
    )


def bucket_probability(
    bucket_type,
    bucket_value,
    bucket_low,
    bucket_high,
    mean_c,
    sigma_c,
):
    if (
        mean_c is None
        or sigma_c is None
        or sigma_c <= 0
    ):
        return None

    if bucket_type == "exact":
        value = safe_float(
            bucket_value
        )

        if value is None:
            return None

        return clamp_probability(
            normal_cdf(
                value + 0.5,
                mean_c,
                sigma_c,
            )
            - normal_cdf(
                value - 0.5,
                mean_c,
                sigma_c,
            )
        )

    if bucket_type == "or_lower":
        threshold = safe_float(
            bucket_value
        )

        if threshold is None:
            return None

        return clamp_probability(
            normal_cdf(
                threshold + 0.5,
                mean_c,
                sigma_c,
            )
        )

    if bucket_type == "or_higher":
        threshold = safe_float(
            bucket_value
        )

        if threshold is None:
            return None

        return clamp_probability(
            1.0
            - normal_cdf(
                threshold - 0.5,
                mean_c,
                sigma_c,
            )
        )

    if bucket_type == "range":
        low = safe_float(
            bucket_low
        )

        high = safe_float(
            bucket_high
        )

        if (
            low is None
            or high is None
            or high < low
        ):
            return None

        return clamp_probability(
            normal_cdf(
                high + 0.5,
                mean_c,
                sigma_c,
            )
            - normal_cdf(
                low - 0.5,
                mean_c,
                sigma_c,
            )
        )

    return None


# ============================================================
# FORECAST / OBSERVATION HELPERS
# ============================================================

def forecast_value_for_market(
    row,
    market_type,
):
    if market_type == "lowest_temperature":
        return safe_float(
            row.get(
                "temperature_min_c"
            )
        )

    return safe_float(
        row.get(
            "temperature_max_c"
        )
    )


def observation_value(row):
    for key in (
        "temperature_c",
        "temp_c",
        "temperature",
    ):
        value = safe_float(
            row.get(key)
        )

        if value is not None:
            return value

    return None


def target_date_from_forecast(
    row
):
    return parse_date(
        row.get(
            "market_date"
        )
    )


# ============================================================
# OBSERVATION GROUND TRUTH
# ============================================================

def build_daily_observations(
    rows,
    station_registry,
):
    """
    Creates:
        (station, date) -> {
            min_c,
            max_c,
            samples,
            first_time,
            last_time
        }

    Only raw observed temperature is used.
    """

    daily = {}

    for row in rows:
        station = row.get(
            "station"
        )

        if not station:
            continue

        station = str(
            station
        ).upper()

        value = observation_value(
            row
        )

        if value is None:
            continue

        timezone_name = (
            get_station_timezone(
                station,
                station_registry,
            )
        )

        obs_date = extract_row_date(
            row,
            timezone_name,
        )

        if obs_date is None:
            continue

        key = (
            station,
            obs_date,
        )

        item = daily.get(
            key
        )

        if item is None:
            item = {
                "station": station,
                "date": obs_date.isoformat(),
                "min_c": value,
                "max_c": value,
                "samples": 0,
                "first_time": None,
                "last_time": None,
            }

            daily[key] = item

        item["min_c"] = min(
            item["min_c"],
            value,
        )

        item["max_c"] = max(
            item["max_c"],
            value,
        )

        item["samples"] += 1

        timestamp = get_row_timestamp(
            row
        )

        if timestamp:
            timestamp_text = (
                timestamp.isoformat()
            )

            if (
                item["first_time"]
                is None
                or timestamp_text
                < item["first_time"]
            ):
                item["first_time"] = (
                    timestamp_text
                )

            if (
                item["last_time"]
                is None
                or timestamp_text
                > item["last_time"]
            ):
                item["last_time"] = (
                    timestamp_text
                )

    return daily


# ============================================================
# CALIBRATION
# ============================================================

def lead_bucket_from_dates(
    collected_at,
    target_date,
    timezone_name=None,
):
    if not collected_at or not target_date:
        return None

    try:
        if timezone_name:
            local_dt = collected_at.astimezone(
                ZoneInfo(
                    timezone_name
                )
            )
        else:
            local_dt = collected_at.astimezone(
                timezone.utc
            )

        collection_date = (
            local_dt.date()
        )

        delta = (
            target_date
            - collection_date
        ).days

        if delta < 0:
            return None

        if delta <= 0:
            return 0

        if delta == 1:
            return 1

        if delta == 2:
            return 2

        return MAX_LEAD_BUCKET

    except Exception:
        return None


def add_error_sample(
    storage,
    key,
    target_date,
    actual,
    forecast,
):
    if actual is None or forecast is None:
        return

    error = (
        actual - forecast
    )

    storage.setdefault(
        key,
        [],
    ).append(
        {
            "target_date": target_date.isoformat(),
            "error": error,
        }
    )


def build_calibration_samples(
    forecast_rows,
    daily_observations,
    station_registry,
):
    samples = {}

    now_date = now_utc().date()

    cutoff_date = (
        now_date
        if CALIBRATION_LOOKBACK_DAYS <= 0
        else now_date
    )

    for row in forecast_rows:
        station = row.get(
            "station"
        )

        model = row.get(
            "model"
        )

        target_date = target_date_from_forecast(
            row
        )

        collected_at = parse_datetime(
            row.get(
                "collected_at"
            )
        )

        if (
            not station
            or not model
            or not target_date
            or not collected_at
        ):
            continue

        station = str(
            station
        ).upper()

        model = str(
            model
        )

        # Only historical target dates.
        if target_date >= now_date:
            continue

        age_days = (
            now_date
            - target_date
        ).days

        if (
            age_days
            > CALIBRATION_LOOKBACK_DAYS
        ):
            continue

        timezone_name = (
            get_station_timezone(
                station,
                station_registry,
            )
        )

        # Critical no-lookahead rule:
        # forecast must have existed BEFORE the local target day started.
        if timezone_name:
            try:
                tz = ZoneInfo(
                    timezone_name
                )

                target_midnight = datetime.combine(
                    target_date,
                    time.min,
                ).replace(
                    tzinfo=tz
                ).astimezone(
                    timezone.utc
                )

            except Exception:
                target_midnight = datetime.combine(
                    target_date,
                    time.min,
                    tzinfo=timezone.utc,
                )
        else:
            target_midnight = datetime.combine(
                target_date,
                time.min,
                tzinfo=timezone.utc,
            )

        if collected_at >= target_midnight:
            continue

        lead_bucket = lead_bucket_from_dates(
            collected_at,
            target_date,
            timezone_name,
        )

        if lead_bucket is None:
            continue

        market_type = (
            "lowest_temperature"
            if safe_float(
                row.get(
                    "temperature_min_c"
                )
            ) is not None
            and safe_float(
                row.get(
                    "temperature_max_c"
                )
            ) is None
            else None
        )

        if market_type is None:
            # Infer from available values only when possible.
            # We create calibration for BOTH metrics when present.
            pass

        actual_daily = daily_observations.get(
            (
                station,
                target_date,
            )
        )

        if actual_daily is None:
            continue

        forecast_min = safe_float(
            row.get(
                "temperature_min_c"
            )
        )

        forecast_max = safe_float(
            row.get(
                "temperature_max_c"
            )
        )

        actual_min = safe_float(
            actual_daily.get(
                "min_c"
            )
        )

        actual_max = safe_float(
            actual_daily.get(
                "max_c"
            )
        )

        base_key = (
            station,
            model,
        )

        lead_key = (
            station,
            model,
            lead_bucket,
        )

        station_metric_key = (
            station,
            "ANY",
            lead_bucket,
        )

        global_metric_key = (
            "GLOBAL",
            model,
            lead_bucket,
        )

        global_all_key = (
            "GLOBAL",
            "ANY",
            lead_bucket,
        )

        if (
            forecast_min is not None
            and actual_min is not None
        ):
            add_error_sample(
                samples,
                (
                    "lowest_temperature",
                    lead_key,
                ),
                target_date,
                actual_min,
                forecast_min,
            )

            add_error_sample(
                samples,
                (
                    "lowest_temperature",
                    station_metric_key,
                ),
                target_date,
                actual_min,
                forecast_min,
            )

            add_error_sample(
                samples,
                (
                    "lowest_temperature",
                    global_metric_key,
                ),
                target_date,
                actual_min,
                forecast_min,
            )

            add_error_sample(
                samples,
                (
                    "lowest_temperature",
                    global_all_key,
                ),
                target_date,
                actual_min,
                forecast_min,
            )

        if (
            forecast_max is not None
            and actual_max is not None
        ):
            add_error_sample(
                samples,
                (
                    "highest_temperature",
                    lead_key,
                ),
                target_date,
                actual_max,
                forecast_max,
            )

            add_error_sample(
                samples,
                (
                    "highest_temperature",
                    station_metric_key,
                ),
                target_date,
                actual_max,
                forecast_max,
            )

            add_error_sample(
                samples,
                (
                    "highest_temperature",
                    global_metric_key,
                ),
                target_date,
                actual_max,
                forecast_max,
            )

            add_error_sample(
                samples,
                (
                    "highest_temperature",
                    global_all_key,
                ),
                target_date,
                actual_max,
                forecast_max,
            )

    return samples


def summarize_errors(
    error_rows,
):
    if not error_rows:
        return None

    errors = [
        safe_float(
            item.get(
                "error"
            )
        )
        for item in error_rows
    ]

    errors = [
        value
        for value in errors
        if value is not None
    ]

    if not errors:
        return None

    bias = mean(
        errors
    )

    rmse = math.sqrt(
        mean(
            [
                error * error
                for error in errors
            ]
        )
    )

    mae = mean(
        [
            abs(error)
            for error in errors
        ]
    )

    if len(errors) > 1:
        centered = [
            error - bias
            for error in errors
        ]

        sigma = math.sqrt(
            sum(
                value * value
                for value in centered
            )
            / (
                len(errors) - 1
            )
        )

    else:
        sigma = 0.0

    dates = [
        item.get(
            "target_date"
        )
        for item in error_rows
        if item.get(
            "target_date"
        )
    ]

    return {
        "samples": len(
            errors
        ),
        "bias_c": bias,
        "rmse_c": rmse,
        "sigma_c": sigma,
        "mean_abs_error_c": mae,
        "first_target_date": min(
            dates
        ) if dates else None,
        "last_target_date": max(
            dates
        ) if dates else None,
    }


def build_calibration_table(
    samples,
):
    table = {}

    for key, rows in samples.items():
        market_type, lookup_key = key

        summary = summarize_errors(
            rows
        )

        if summary is None:
            continue

        station = lookup_key[0]
        model = lookup_key[1]
        lead_bucket = lookup_key[2]

        if summary[
            "samples"
        ] < CALIBRATION_GLOBAL_MIN_SAMPLES:
            continue

        table[
            (
                market_type,
                station,
                model,
                lead_bucket,
            )
        ] = summary

    return table


def calibration_lookup(
    table,
    station,
    model,
    market_type,
    lead_bucket,
):
    candidates = [
        (
            market_type,
            station,
            model,
            lead_bucket,
            "station_model_lead",
        ),
        (
            market_type,
            station,
            model,
            None,
            "station_model_pool",
        ),
        (
            market_type,
            station,
            "ANY",
            lead_bucket,
            "station_pool_lead",
        ),
        (
            market_type,
            "GLOBAL",
            model,
            lead_bucket,
            "global_model_lead",
        ),
        (
            market_type,
            "GLOBAL",
            "ANY",
            lead_bucket,
            "global_pool_lead",
        ),
        (
            market_type,
            "GLOBAL",
            model,
            None,
            "global_model_pool",
        ),
        (
            market_type,
            "GLOBAL",
            "ANY",
            None,
            "global_pool",
        ),
    ]

    for (
        current_metric,
        current_station,
        current_model,
        current_lead,
        source_level,
    ) in candidates:

        if current_lead is None:
            possible = [
                item
                for item in table.items()
                if item[0][0]
                == current_metric
                and item[0][1]
                == current_station
                and item[0][2]
                == current_model
            ]

            if possible:
                # Pool multiple lead buckets.
                expanded = []

                for _, summary in possible:
                    if (
                        summary[
                            "samples"
                        ]
                        >= CALIBRATION_GLOBAL_MIN_SAMPLES
                    ):
                        expanded.append(
                            summary
                        )

                if expanded:
                    total_n = sum(
                        item[
                            "samples"
                        ]
                        for item in expanded
                    )

                    weighted_bias = sum(
                        item[
                            "bias_c"
                        ]
                        * item[
                            "samples"
                        ]
                        for item in expanded
                    ) / total_n

                    weighted_sigma_sq = sum(
                        (
                            (
                                item[
                                    "sigma_c"
                                ] ** 2
                            )
                            + (
                                item[
                                    "bias_c"
                                ]
                                - weighted_bias
                            ) ** 2
                        )
                        * item[
                            "samples"
                        ]
                        for item in expanded
                    ) / total_n

                    return {
                        "samples": total_n,
                        "bias_c": weighted_bias,
                        "sigma_c": math.sqrt(
                            max(
                                0.0,
                                weighted_sigma_sq,
                            )
                        ),
                        "source_level": source_level,
                        "lead_bucket": lead_bucket,
                    }

        else:
            key = (
                current_metric,
                current_station,
                current_model,
                current_lead,
            )

            summary = table.get(
                key
            )

            if (
                summary
                and summary[
                    "samples"
                ]
                >= CALIBRATION_GLOBAL_MIN_SAMPLES
            ):
                return {
                    "samples": summary[
                        "samples"
                    ],
                    "bias_c": summary[
                        "bias_c"
                    ],
                    "sigma_c": summary[
                        "sigma_c"
                    ],
                    "source_level": source_level,
                    "lead_bucket": current_lead,
                }

    return {
        "samples": 0,
        "bias_c": 0.0,
        "sigma_c": DEFAULT_SIGMA_C,
        "source_level": "default",
        "lead_bucket": lead_bucket,
    }


def apply_calibration_shrinkage(
    calibration,
):
    samples = calibration[
        "samples"
    ]

    if samples <= 0:
        return calibration

    sigma = safe_float(
        calibration.get(
            "sigma_c"
        )
    )

    if sigma is None:
        sigma = DEFAULT_SIGMA_C

    weight = min(
        1.0,
        samples
        / float(
            SHRINK_MIN_SAMPLES
        ),
    )

    shrunk_sigma = (
        sigma * weight
        + SHRINK_TARGET_SIGMA_C
        * (1.0 - weight)
    )

    calibration = dict(
        calibration
    )

    calibration[
        "sigma_c"
    ] = max(
        MIN_SIGMA_C,
        min(
            MAX_SIGMA_C,
            shrunk_sigma,
        ),
    )

    return calibration


def save_calibration_outputs(
    table,
    run_timestamp,
):
    rows = []

    for (
        market_type,
        station,
        model,
        lead_bucket,
    ), summary in sorted(
        table.items()
    ):
        rows.append(
            {
                "run_at": run_timestamp,
                "station": station,
                "model": model,
                "market_type": market_type,
                "lead_bucket": (
                    lead_bucket
                    if lead_bucket is not None
                    else ""
                ),
                "samples": summary[
                    "samples"
                ],
                "bias_c": rounded(
                    summary[
                        "bias_c"
                    ],
                    5,
                ),
                "rmse_c": rounded(
                    summary[
                        "rmse_c"
                    ],
                    5,
                ),
                "sigma_c": rounded(
                    summary[
                        "sigma_c"
                    ],
                    5,
                ),
                "mean_abs_error_c": rounded(
                    summary[
                        "mean_abs_error_c"
                    ],
                    5,
                ),
                "first_target_date": summary[
                    "first_target_date"
                ],
                "last_target_date": summary[
                    "last_target_date"
                ],
                "source_level": (
                    "historical"
                ),
            }
        )

    payload = {
        "engine_version": ENGINE_VERSION,
        "run_at": run_timestamp,
        "calibration_lookback_days": CALIBRATION_LOOKBACK_DAYS,
        "calibration_min_samples": CALIBRATION_MIN_SAMPLES,
        "records": rows,
    }

    write_json(
        CALIBRATION_LATEST_FILE,
        payload,
    )

    append_csv(
        CALIBRATION_HISTORY_FILE,
        CALIBRATION_FIELDS,
        rows,
    )

    return rows


# ============================================================
# CURRENT FORECAST CONSENSUS
# ============================================================

def select_latest_forecasts(
    rows
):
    latest = {}

    for row in rows:
        station = row.get(
            "station"
        )

        market_date = row.get(
            "market_date"
        )

        model = row.get(
            "model"
        )

        if (
            not station
            or not market_date
            or not model
        ):
            continue

        key = (
            str(station).upper(),
            market_date,
            str(model),
        )

        collected_at = row.get(
            "collected_at",
            "",
        )

        previous = latest.get(
            key
        )

        previous_at = (
            previous.get(
                "collected_at",
                "",
            )
            if previous
            else ""
        )

        if (
            previous is None
            or collected_at
            > previous_at
        ):
            latest[
                key
            ] = row

    return list(
        latest.values()
    )


def select_latest_observations(
    rows
):
    latest = {}

    for row in rows:
        station = row.get(
            "station"
        )

        if not station:
            continue

        station = str(
            station
        ).upper()

        timestamp = get_row_timestamp(
            row
        )

        previous = latest.get(
            station
        )

        previous_timestamp = (
            get_row_timestamp(
                previous
            )
            if previous
            else None
        )

        if (
            previous is None
            or (
                timestamp
                and (
                    previous_timestamp
                    is None
                    or timestamp
                    > previous_timestamp
                )
            )
        ):
            latest[
                station
            ] = row

    return latest


def build_current_forecast_stats(
    forecasts,
    station,
    market_date,
    market_type,
    calibration_table,
    station_registry,
):
    matching = []

    for row in forecasts:
        row_station = row.get(
            "station"
        )

        if not row_station:
            continue

        if str(
            row_station
        ).upper() != str(
            station
        ).upper():
            continue

        if row.get(
            "market_date"
        ) != market_date:
            continue

        model = (
            row.get("model")
            or "unknown"
        )

        value = forecast_value_for_market(
            row,
            market_type,
        )

        if value is None:
            continue

        collected_at = parse_datetime(
            row.get(
                "collected_at"
            )
        )

        timezone_name = (
            get_station_timezone(
                station,
                station_registry,
            )
        )

        target_date = parse_date(
            market_date
        )

        lead_bucket = None

        if collected_at and target_date:
            lead_bucket = lead_bucket_from_dates(
                collected_at,
                target_date,
                timezone_name,
            )

        if lead_bucket is None:
            lead_bucket = MAX_LEAD_BUCKET

        calibration = calibration_lookup(
            calibration_table,
            str(station).upper(),
            str(model),
            market_type,
            lead_bucket,
        )

        calibration = (
            apply_calibration_shrinkage(
                calibration
            )
        )

        corrected_value = (
            value
            + calibration[
                "bias_c"
            ]
        )

        matching.append(
            {
                "model": str(
                    model
                ),
                "raw_value_c": value,
                "corrected_value_c": corrected_value,
                "calibration": calibration,
            }
        )

    if not matching:
        return None

    corrected_values = [
        item[
            "corrected_value_c"
        ]
        for item in matching
    ]

    model_sigmas = [
        item[
            "calibration"
        ][
            "sigma_c"
        ]
        for item in matching
    ]

    mean_c = mean(
        corrected_values
    )

    if len(
        corrected_values
    ) > 1:
        between_model_variance = (
            sum(
                (
                    value
                    - mean_c
                ) ** 2
                for value in corrected_values
            )
            / (
                len(
                    corrected_values
                )
                - 1
            )
        )
    else:
        between_model_variance = 0.0

    average_model_variance = mean(
        [
            sigma * sigma
            for sigma in model_sigmas
        ]
    )

    combined_sigma = math.sqrt(
        max(
            0.0,
            average_model_variance
            + between_model_variance,
        )
    )

    combined_sigma = max(
        MIN_SIGMA_C,
        min(
            MAX_SIGMA_C,
            combined_sigma,
        ),
    )

    # Aggregate calibration diagnostics.
    total_samples = sum(
        item[
            "calibration"
        ][
            "samples"
        ]
        for item in matching
    )

    weighted_bias = (
        sum(
            item[
                "calibration"
            ][
                "bias_c"
            ]
            * max(
                1,
                item[
                    "calibration"
                ][
                    "samples"
                ],
            )
            for item in matching
        )
        / max(
            1,
            total_samples,
        )
    )

    source_levels = sorted(
        set(
            item[
                "calibration"
            ][
                "source_level"
            ]
            for item in matching
        )
    )

    lead_buckets = sorted(
        set(
            item[
                "calibration"
            ][
                "lead_bucket"
            ]
            for item in matching
        )
    )

    return {
        "values": corrected_values,

        "raw_values": [
            item[
                "raw_value_c"
            ]
            for item in matching
        ],

        "models": [
            item[
                "model"
            ]
            for item in matching
        ],

        "mean_c": mean_c,

        "min_c": min(
            corrected_values
        ),

        "max_c": max(
            corrected_values
        ),

        "std_c": math.sqrt(
            max(
                0.0,
                between_model_variance,
            )
        ),

        "sigma_c": combined_sigma,

        "calibration_samples": total_samples,

        "calibration_bias_c": weighted_bias,

        "calibration_method": ",".join(
            source_levels
        ),

        "calibration_lead_bucket": ",".join(
            str(
                item
            )
            for item in lead_buckets
        ),
    }


# ============================================================
# MARKET ENTRY / FEES
# ============================================================

def candidate_entry_price(
    market
):
    ask = safe_float(
        market.get(
            "best_ask"
        )
    )

    if (
        ask is not None
        and 0.0 < ask < 1.0
    ):
        return (
            ask,
            "ask",
        )

    yes_price = safe_float(
        market.get(
            "yes_price"
        )
    )

    if (
        not REQUIRE_EXECUTABLE_ASK
        and yes_price is not None
        and 0.0 < yes_price < 1.0
    ):
        return (
            yes_price,
            "yes_price_research",
        )

    return (
        None,
        "none",
    )


def market_probability(
    market
):
    return safe_float(
        market.get(
            "yes_price"
        )
    )


def get_fee_rate(
    market
):
    enabled = market.get(
        "fees_enabled"
    )

    if enabled is False:
        return (
            0.0,
            "disabled",
        )

    schedule = market.get(
        "fee_schedule"
    )

    if isinstance(
        schedule,
        str,
    ):
        try:
            schedule = json.loads(
                schedule
            )
        except Exception:
            schedule = None

    if isinstance(
        schedule,
        dict,
    ):
        for key in (
            "rate",
            "feeRate",
            "fee_rate",
            "r",
        ):
            rate = safe_float(
                schedule.get(
                    key
                )
            )

            if rate is not None:
                return (
                    rate,
                    "market",
                )

    return (
        DEFAULT_WEATHER_FEE_RATE,
        "default_weather",
    )


def fee_per_share(
    entry_price,
    fee_rate,
):
    if (
        entry_price is None
        or fee_rate <= 0
    ):
        return 0.0

    return (
        fee_rate
        * entry_price
        * (
            1.0
            - entry_price
        )
    )


# ============================================================
# FAMILY GROUPING / CONSISTENCY
# ============================================================

def group_market_families(
    markets
):
    groups = {}

    for market in markets:
        key = market.get(
            "event_key"
        )

        if not key:
            continue

        groups.setdefault(
            key,
            [],
        ).append(
            market
        )

    return groups


def family_probability_sum(
    family,
    stats,
):
    exclusive = []
    cumulative = []

    for market in family:
        normalized = (
            normalize_bucket_to_celsius(
                market
            )
        )

        bucket_type = normalized.get(
            "bucket_type"
        )

        probability = (
            bucket_probability(
                bucket_type,
                safe_float(
                    normalized.get(
                        "bucket_value"
                    )
                ),
                safe_float(
                    normalized.get(
                        "bucket_low"
                    )
                ),
                safe_float(
                    normalized.get(
                        "bucket_high"
                    )
                ),
                stats[
                    "mean_c"
                ],
                stats[
                    "sigma_c"
                ],
            )
        )

        if probability is None:
            continue

        if bucket_type in (
            "exact",
            "range",
        ):
            exclusive.append(
                probability
            )

        elif bucket_type in (
            "or_lower",
            "or_higher",
        ):
            cumulative.append(
                probability
            )

    if not exclusive:
        return {
            "sum": None,
            "status": "NO_EXCLUSIVE_BUCKETS",
            "exclusive_count": 0,
            "cumulative_count": len(
                cumulative
            ),
        }

    total = sum(
        exclusive
    )

    if total < FAMILY_MIN_COVERAGE:
        status = "INCOMPLETE"

    elif total > FAMILY_MAX_COVERAGE:
        status = (
            "OVERLAP_OR_PARSE_ERROR"
        )

    else:
        status = "PASS"

    return {
        "sum": total,
        "status": status,
        "exclusive_count": len(
            exclusive
        ),
        "cumulative_count": len(
            cumulative
        ),
    }


def normalize_exclusive_probability(
    raw_probability,
    family_sum,
):
    if raw_probability is None:
        return None

    if (
        family_sum is None
        or family_sum <= 0
    ):
        return raw_probability

    return clamp_probability(
        raw_probability
        / family_sum
    )


# ============================================================
# SIGNAL CREATION
# ============================================================

def create_signal(
    market,
    forecast_stats,
    observation,
    family_check,
):
    normalized = (
        normalize_bucket_to_celsius(
            market
        )
    )

    mean_c = forecast_stats[
        "mean_c"
    ]

    sigma_c = forecast_stats[
        "sigma_c"
    ]

    bucket_type = normalized.get(
        "bucket_type"
    )

    raw_probability = (
        bucket_probability(
            bucket_type,
            safe_float(
                normalized.get(
                    "bucket_value"
                )
            ),
            safe_float(
                normalized.get(
                    "bucket_low"
                )
            ),
            safe_float(
                normalized.get(
                    "bucket_high"
                )
            ),
            mean_c,
            sigma_c,
        )
    )

    if raw_probability is None:
        return None

    if (
        bucket_type in (
            "exact",
            "range",
        )
        and family_check[
            "sum"
        ] is not None
    ):
        model_probability = (
            normalize_exclusive_probability(
                raw_probability,
                family_check[
                    "sum"
                ],
            )
        )

    else:
        model_probability = (
            raw_probability
        )

    entry_price, entry_source = (
        candidate_entry_price(
            market
        )
    )

    current_probability = (
        market_probability(
            market
        )
    )

    fee_rate, fee_source = (
        get_fee_rate(
            market
        )
    )

    fee = (
        fee_per_share(
            entry_price,
            fee_rate,
        )
        if entry_price is not None
        else 0.0
    )

    gross_edge = None
    net_ev = None
    net_return_if_win = None

    if entry_price is not None:
        gross_edge = (
            model_probability
            - entry_price
        )

        net_ev = (
            model_probability
            - entry_price
            - fee
        )

        net_return_if_win = (
            1.0
            - entry_price
            - fee
        )

    # ========================================================
    # EXECUTION QUALITY
    # ========================================================

    execution_quality = (
        "NO_EXECUTABLE_ASK"
    )

    if entry_source == "ask":
        if entry_price <= THIN_ASK_MAX:
            execution_quality = (
                "THIN_ASK"
            )
        else:
            execution_quality = (
                "EXECUTABLE_ASK"
            )

    # ========================================================
    # SIGNAL
    # ========================================================

    signal = "NO_TRADE"

    reason_parts = []

    if entry_price is None:
        reason_parts.append(
            "no_executable_ask"
        )

    else:
        if (
            family_check[
                "status"
            ] != "PASS"
            and bucket_type in (
                "exact",
                "range",
            )
        ):
            reason_parts.append(
                "family="
                + family_check[
                    "status"
                ]
            )

        if entry_source != "ask":
            reason_parts.append(
                "not_executable"
            )

        if entry_price <= THIN_ASK_MAX:
            reason_parts.append(
                "thin_ask"
            )

        if gross_edge < MIN_EDGE:
            reason_parts.append(
                "edge_below_min"
            )

        if net_ev < MIN_EV:
            reason_parts.append(
                "ev_below_min"
            )

        strong_ok = (
            entry_source == "ask"
            and family_check[
                "status"
            ] == "PASS"
            and entry_price
            >= STRONG_MIN_ASK
            and gross_edge
            >= STRONG_MIN_EDGE
            and net_ev
            >= STRONG_MIN_EV
            and forecast_stats[
                "calibration_samples"
            ]
            >= CALIBRATION_MIN_SAMPLES
        )

        possible_ok = (
            gross_edge
            >= MIN_EDGE
            and net_ev
            >= MIN_EV
            and (
                bucket_type
                in (
                    "or_lower",
                    "or_higher",
                )
                or family_check[
                    "status"
                ]
                == "PASS"
            )
        )

        if strong_ok:
            signal = (
                "STRONG_EDGE"
            )

            reason_parts.append(
                "strong_thresholds_pass"
            )

            reason_parts.append(
                "calibrated_sample_gate_pass"
            )

        elif possible_ok:
            signal = (
                "POSSIBLE_EDGE"
            )

            reason_parts.append(
                "research_thresholds_pass"
            )

            if (
                forecast_stats[
                    "calibration_samples"
                ]
                < CALIBRATION_MIN_SAMPLES
            ):
                reason_parts.append(
                    "calibration_samples_below_strong_gate"
                )

    # ========================================================
    # PAPER BUY
    # ========================================================

    paper_buy = (
        signal
        == "STRONG_EDGE"
        and entry_source
        == "ask"
        and execution_quality
        == "EXECUTABLE_ASK"
        and family_check[
            "status"
        ]
        == "PASS"
    )

    if paper_buy:
        signal_output = (
            "PAPER_BUY"
        )

        reason_parts.append(
            "paper_buy_gate_pass"
        )

    else:
        signal_output = signal

    if not reason_parts:
        reason_parts.append(
            "no_trade_conditions"
        )

    # ========================================================
    # OBSERVATION
    # ========================================================

    observation_temperature = None
    observation_time = None

    if observation:
        observation_temperature = (
            observation_value(
                observation
            )
        )

        observation_time = (
            observation.get(
                "observation_time"
            )
            or observation.get(
                "valid_time"
            )
            or observation.get(
                "collected_at"
            )
        )

    return {
        "run_at": iso_now(),

        "city": market.get(
            "city"
        ),

        "station": market.get(
            "resolution_station"
        ),

        "market_date": market.get(
            "market_date"
        ),

        "market_type": market.get(
            "market_type"
        ),

        "unit": market.get(
            "temperature_unit"
        ),

        "market_id": market.get(
            "market_id"
        ),

        "event_key": market.get(
            "event_key"
        ),

        "bucket_type": bucket_type,

        "bucket_value": market.get(
            "bucket_value"
        ),

        "bucket_low": market.get(
            "bucket_low"
        ),

        "bucket_high": market.get(
            "bucket_high"
        ),

        "group_title": market.get(
            "group_title"
        ),

        "model_probability_raw": rounded(
            raw_probability,
            6,
        ),

        "model_probability": rounded(
            model_probability,
            6,
        ),

        "family_exclusive_probability_sum": rounded(
            family_check[
                "sum"
            ],
            6,
        ),

        "family_probability_status": (
            family_check[
                "status"
            ]
        ),

        "market_probability": rounded(
            current_probability,
            6,
        ),

        "entry_price": rounded(
            entry_price,
            6,
        ),

        "entry_source": entry_source,

        "fee_rate": rounded(
            fee_rate,
            6,
        ),

        "fee_source": fee_source,

        "fee_per_share": rounded(
            fee,
            8,
        ),

        "gross_edge": rounded(
            gross_edge,
            6,
        ),

        "net_ev_per_share": rounded(
            net_ev,
            6,
        ),

        "net_return_if_win": rounded(
            net_return_if_win,
            6,
        ),

        "forecast_count": forecast_stats[
            "forecast_count"
        ],

        "forecast_models": ",".join(
            forecast_stats[
                "models"
            ]
        ),

        "forecast_mean_c": rounded(
            mean_c,
            4,
        ),

        "forecast_min_c": rounded(
            forecast_stats[
                "min_c"
            ],
            4,
        ),

        "forecast_max_c": rounded(
            forecast_stats[
                "max_c"
            ],
            4,
        ),

        "forecast_std_c": rounded(
            forecast_stats[
                "std_c"
            ],
            4,
        ),

        "forecast_sigma_c": rounded(
            sigma_c,
            4,
        ),

        "calibration_method": (
            forecast_stats[
                "calibration_method"
            ]
        ),

        "calibration_samples": (
            forecast_stats[
                "calibration_samples"
            ]
        ),

        "calibration_bias_c": rounded(
            forecast_stats[
                "calibration_bias_c"
            ],
            4,
        ),

        "calibration_sigma_c": rounded(
            sigma_c,
            4,
        ),

        "calibration_lead_bucket": (
            forecast_stats[
                "calibration_lead_bucket"
            ]
        ),

        "observation_temperature_c": rounded(
            observation_temperature,
            4,
        ),

        "observation_time": observation_time,

        "execution_quality": (
            execution_quality
        ),

        "signal": signal_output,

        "reason": "; ".join(
            reason_parts
        ),
    }


# ============================================================
# DISPLAY
# ============================================================

def print_signal(
    signal
):
    print("-" * 70)

    print(
        f"{signal['city']} | "
        f"{signal['station']} | "
        f"{signal['market_date']} | "
        f"{signal['bucket_type']}"
    )

    print(
        f"  Bucket: "
        f"{signal['bucket_value'] or signal['bucket_low']}"
    )

    print(
        f"  Model: "
        f"{signal['model_probability']}"
    )

    print(
        f"  Market: "
        f"{signal['market_probability']}"
    )

    print(
        f"  Ask: "
        f"{signal['entry_price']} "
        f"({signal['entry_source']})"
    )

    print(
        f"  Family: "
        f"{signal['family_exclusive_probability_sum']} "
        f"({signal['family_probability_status']})"
    )

    print(
        f"  Gross edge: "
        f"{signal['gross_edge']}"
    )

    print(
        f"  Net EV/share: "
        f"{signal['net_ev_per_share']}"
    )

    print(
        f"  Forecast mean: "
        f"{signal['forecast_mean_c']}C"
    )

    print(
        f"  Sigma: "
        f"{signal['forecast_sigma_c']}C"
    )

    print(
        f"  Calibration: "
        f"{signal['calibration_method']} "
        f"n={signal['calibration_samples']} "
        f"bias={signal['calibration_bias_c']}C"
    )

    print(
        f"  Execution: "
        f"{signal['execution_quality']}"
    )

    print(
        f"  SIGNAL: "
        f"{signal['signal']}"
    )

    print(
        f"  Reason: "
        f"{signal['reason']}"
    )


# ============================================================
# CALIBRATION REPORT
# ============================================================

def create_calibration_report(
    calibration_rows,
    daily_observations,
    forecast_rows,
    run_timestamp,
):
    lines = [
        f"POLYMARKET WEATHER CALIBRATION V{ENGINE_VERSION}",
        "HISTORICAL FORECAST ERROR ANALYSIS",
        "",
        f"UTC: {run_timestamp}",
        "",
        (
            "Historical forecast rows: "
            f"{len(forecast_rows)}"
        ),
        (
            "Observed station-days: "
            f"{len(daily_observations)}"
        ),
        (
            "Calibration records: "
            f"{len(calibration_rows)}"
        ),
        "",
    ]

    if not calibration_rows:
        lines.extend(
            [
                "NO CALIBRATION DATA AVAILABLE.",
                "Engine will fall back to default sigma.",
                "",
            ]
        )

    else:
        valid_rows = [
            row
            for row in calibration_rows
            if safe_int(
                row.get(
                    "samples"
                )
            )
            and safe_int(
                row.get(
                    "samples"
                )
            ) >= CALIBRATION_MIN_SAMPLES
        ]

        lines.append(
            "Records meeting strong calibration sample threshold: "
            f"{len(valid_rows)}"
        )

        lines.append("")

        ranked = sorted(
            valid_rows,
            key=lambda row: (
                abs(
                    safe_float(
                        row.get(
                            "bias_c"
                        )
                    )
                    or 0.0
                ),
                -(
                    safe_int(
                        row.get(
                            "samples"
                        )
                    )
                    or 0
                ),
            ),
            reverse=True,
        )

        for row in ranked[:50]:
            lines.append(
                (
                    f"{row['station']} | "
                    f"{row['model']} | "
                    f"{row['market_type']} | "
                    f"lead={row['lead_bucket']} | "
                    f"n={row['samples']} | "
                    f"bias={row['bias_c']}C | "
                    f"RMSE={row['rmse_c']}C | "
                    f"sigma={row['sigma_c']}C"
                )
            )

    write_text(
        CALIBRATION_REPORT_FILE,
        "\n".join(
            lines
        ),
    )


# ============================================================
# MAIN
# ============================================================

def main():
    os.makedirs(
        EDGE_HISTORY_DIR,
        exist_ok=True,
    )

    os.makedirs(
        CALIBRATION_DIR,
        exist_ok=True,
    )

    print("=" * 70)

    print(
        f"POLYMARKET WEATHER EDGE ENGINE "
        f"V{ENGINE_VERSION}"
    )

    print(
        "RESEARCH / PAPER TRADING ONLY"
    )

    print(
        "NO ORDERS SENT"
    )

    print("=" * 70)

    run_timestamp = iso_now()

    print(
        f"UTC: {run_timestamp}"
    )

    # ========================================================
    # REQUIRED INPUTS
    # ========================================================

    required_files = [
        ACTIVE_FILE,
        FORECAST_FILE,
        OBSERVATION_FILE,
    ]

    for required in required_files:
        if not os.path.exists(
            required
        ):
            raise FileNotFoundError(
                required
            )

    # ========================================================
    # LOAD DATA
    # ========================================================

    active_payload = read_json(
        ACTIVE_FILE
    )

    markets = active_payload.get(
        "markets",
        []
    )

    all_forecast_rows = read_csv(
        FORECAST_FILE
    )

    all_observation_rows = read_csv(
        OBSERVATION_FILE
    )

    station_registry = (
        load_station_registry()
    )

    latest_forecasts = (
        select_latest_forecasts(
            all_forecast_rows
        )
    )

    latest_observations = (
        select_latest_observations(
            all_observation_rows
        )
    )

    print(
        f"Current markets: "
        f"{len(markets)}"
    )

    print(
        f"Historical forecast rows: "
        f"{len(all_forecast_rows)}"
    )

    print(
        f"Latest forecast records: "
        f"{len(latest_forecasts)}"
    )

    print(
        f"Historical observation rows: "
        f"{len(all_observation_rows)}"
    )

    print(
        f"Latest observations: "
        f"{len(latest_observations)}"
    )

    # ========================================================
    # BUILD GROUND TRUTH
    # ========================================================

    daily_observations = (
        build_daily_observations(
            all_observation_rows,
            station_registry,
        )
    )

    print(
        f"Observed station-days: "
        f"{len(daily_observations)}"
    )

    # ========================================================
    # CALIBRATION
    # ========================================================

    calibration_samples = (
        build_calibration_samples(
            all_forecast_rows,
            daily_observations,
            station_registry,
        )
    )

    calibration_table = (
        build_calibration_table(
            calibration_samples
        )
    )

    calibration_rows = (
        save_calibration_outputs(
            calibration_table,
            run_timestamp,
        )
    )

    create_calibration_report(
        calibration_rows,
        daily_observations,
        all_forecast_rows,
        run_timestamp,
    )

    usable_calibration_rows = [
        row
        for row in calibration_rows
        if (
            safe_int(
                row.get(
                    "samples"
                )
            )
            or 0
        )
        >= CALIBRATION_MIN_SAMPLES
    ]

    print(
        f"Calibration records: "
        f"{len(calibration_rows)}"
    )

    print(
        "Calibration records with "
        f">={CALIBRATION_MIN_SAMPLES} samples: "
        f"{len(usable_calibration_rows)}"
    )

    # ========================================================
    # FAMILIES
    # ========================================================

    families = group_market_families(
        markets
    )

    print(
        f"Families: "
        f"{len(families)}"
    )

    signals = []
    paper_candidates = []

    stats_cache = {}

    evaluated = 0
    no_station = 0
    no_forecast = 0

    # ========================================================
    # PROCESS FAMILIES
    # ========================================================

    for event_key, family in families.items():

        if not family:
            continue

        first = family[0]

        station = first.get(
            "resolution_station"
        )

        market_date = first.get(
            "market_date"
        )

        market_type = first.get(
            "market_type"
        )

        if (
            not station
            or not market_date
        ):
            no_station += len(
                family
            )
            continue

        cache_key = (
            str(
                station
            ).upper(),
            market_date,
            market_type,
        )

        if (
            cache_key
            not in stats_cache
        ):
            stats_cache[
                cache_key
            ] = (
                build_current_forecast_stats(
                    latest_forecasts,
                    station,
                    market_date,
                    market_type,
                    calibration_table,
                    station_registry,
                )
            )

        forecast_stats = (
            stats_cache[
                cache_key
            ]
        )

        if not forecast_stats:
            no_forecast += len(
                family
            )
            continue

        forecast_stats[
            "forecast_count"
        ] = len(
            forecast_stats[
                "models"
            ]
        )

        observation = (
            latest_observations.get(
                str(
                    station
                ).upper()
            )
        )

        family_check = (
            family_probability_sum(
                family,
                forecast_stats,
            )
        )

        family_signals = []

        for market in family:

            signal = create_signal(
                market,
                forecast_stats,
                observation,
                family_check,
            )

            if signal is not None:

                family_signals.append(
                    signal
                )

                evaluated += 1

        family_signals.sort(
            key=lambda item: (
                item[
                    "signal"
                ]
                == "PAPER_BUY",

                item[
                    "signal"
                ]
                == "STRONG_EDGE",

                item[
                    "signal"
                ]
                == "POSSIBLE_EDGE",

                item[
                    "net_ev_per_share"
                ]
                or -999.0,
            ),
            reverse=True,
        )

        # Keep best few per family.
        for signal in family_signals[:5]:

            signals.append(
                signal
            )

            if (
                signal[
                    "signal"
                ]
                == "PAPER_BUY"
            ):
                paper_candidates.append(
                    signal
                )

    # ========================================================
    # GLOBAL RANKING
    # ========================================================

    signal_rank = {
        "PAPER_BUY": 3,
        "STRONG_EDGE": 2,
        "POSSIBLE_EDGE": 1,
        "NO_TRADE": 0,
    }

    signals.sort(
        key=lambda item: (
            signal_rank.get(
                item[
                    "signal"
                ],
                0,
            ),

            item[
                "net_ev_per_share"
            ]
            or -999.0,

            item[
                "gross_edge"
            ]
            or -999.0,
        ),
        reverse=True,
    )

    signals = signals[
        :MAX_SIGNALS_PER_RUN
    ]

    paper_candidates.sort(
        key=lambda item: (
            item[
                "net_ev_per_share"
            ]
            or -999.0,

            item[
                "gross_edge"
            ]
            or -999.0,
        ),
        reverse=True,
    )

    # ========================================================
    # DISPLAY
    # ========================================================

    print("")

    print("=" * 70)
    print("TOP SIGNALS")
    print("=" * 70)

    for signal in signals[:20]:
        print_signal(
            signal
        )

    # ========================================================
    # OUTPUT JSON
    # ========================================================

    payload = {
        "engine_version": ENGINE_VERSION,

        "run_at": run_timestamp,

        "mode": "research_paper",

        "status": "NO_ORDERS_SENT",

        "parameters": {
            "min_edge": MIN_EDGE,
            "min_ev": MIN_EV,

            "strong_min_edge": (
                STRONG_MIN_EDGE
            ),

            "strong_min_ev": (
                STRONG_MIN_EV
            ),

            "strong_min_ask": (
                STRONG_MIN_ASK
            ),

            "thin_ask_max": (
                THIN_ASK_MAX
            ),

            "family_min_coverage": (
                FAMILY_MIN_COVERAGE
            ),

            "family_max_coverage": (
                FAMILY_MAX_COVERAGE
            ),

            "require_executable_ask": (
                REQUIRE_EXECUTABLE_ASK
            ),

            "default_weather_fee_rate": (
                DEFAULT_WEATHER_FEE_RATE
            ),

            "default_sigma_c": (
                DEFAULT_SIGMA_C
            ),

            "min_sigma_c": (
                MIN_SIGMA_C
            ),

            "max_sigma_c": (
                MAX_SIGMA_C
            ),

            "calibration_min_samples": (
                CALIBRATION_MIN_SAMPLES
            ),

            "calibration_lookback_days": (
                CALIBRATION_LOOKBACK_DAYS
            ),
        },

        "current_markets": len(
            markets
        ),

        "families": len(
            families
        ),

        "historical_forecast_rows": len(
            all_forecast_rows
        ),

        "latest_forecast_records": len(
            latest_forecasts
        ),

        "historical_observation_rows": len(
            all_observation_rows
        ),

        "observed_station_days": len(
            daily_observations
        ),

        "calibration_records": len(
            calibration_rows
        ),

        "calibration_records_usable": len(
            usable_calibration_rows
        ),

        "signals_evaluated": (
            evaluated
        ),

        "signals_retained": len(
            signals
        ),

        "paper_buy_candidates": len(
            paper_candidates
        ),

        "diagnostics": {
            "markets_skipped_no_station": (
                no_station
            ),

            "markets_skipped_no_forecast": (
                no_forecast
            ),

            "paper_buy_definition": (
                "STRONG_EDGE + executable best_ask + "
                "passing exclusive family coverage + "
                "minimum historical calibration sample"
            ),

            "calibration": (
                "Historical forecast errors are measured "
                "only when the forecast existed before the "
                "target local day started."
            ),

            "bias_correction": (
                "Forecast value is adjusted by historical "
                "forecast bias."
            ),

            "sigma_model": (
                "Combined model uncertainty plus "
                "between-model disagreement."
            ),

            "cumulative_bucket_handling": (
                "or_lower/or_higher are evaluated directly "
                "and excluded from exclusive family normalization."
            ),
        },

        "signals": signals,
    }

    write_json(
        LATEST_FILE,
        payload,
    )

    # ========================================================
    # TEXT REPORT
    # ========================================================

    report_lines = [
        (
            f"POLYMARKET WEATHER EDGE ENGINE "
            f"V{ENGINE_VERSION}"
        ),

        "RESEARCH / PAPER TRADING ONLY",

        "NO ORDERS SENT",

        f"UTC: {run_timestamp}",

        "",

        f"Current markets: {len(markets)}",

        f"Families: {len(families)}",

        (
            "Historical forecast rows: "
            f"{len(all_forecast_rows)}"
        ),

        (
            "Latest forecast records: "
            f"{len(latest_forecasts)}"
        ),

        (
            "Historical observation rows: "
            f"{len(all_observation_rows)}"
        ),

        (
            "Observed station-days: "
            f"{len(daily_observations)}"
        ),

        (
            "Calibration records: "
            f"{len(calibration_rows)}"
        ),

        (
            "Usable calibration records: "
            f"{len(usable_calibration_rows)}"
        ),

        (
            "Signals evaluated: "
            f"{evaluated}"
        ),

        (
            "Signals retained: "
            f"{len(signals)}"
        ),

        (
            "PAPER_BUY candidates: "
            f"{len(paper_candidates)}"
        ),

        (
            "Skipped without station: "
            f"{no_station}"
        ),

        (
            "Skipped without forecast: "
            f"{no_forecast}"
        ),

        "",
    ]

    for signal in paper_candidates[
        :MAX_TOP_PAPER_SIGNALS
    ]:

        report_lines.extend(
            [
                (
                    f"{signal['city']} | "
                    f"{signal['station']} | "
                    f"{signal['market_date']} | "
                    f"{signal['market_type']}"
                ),

                (
                    f"  bucket={signal['bucket_type']} "
                    f"value={signal['bucket_value']} "
                    f"low={signal['bucket_low']} "
                    f"high={signal['bucket_high']}"
                ),

                (
                    f"  model={signal['model_probability']} "
                    f"market={signal['market_probability']} "
                    f"ask={signal['entry_price']}"
                ),

                (
                    f"  family_sum="
                    f"{signal['family_exclusive_probability_sum']} "
                    f"family_status="
                    f"{signal['family_probability_status']}"
                ),

                (
                    f"  edge={signal['gross_edge']} "
                    f"net_ev={signal['net_ev_per_share']}"
                ),

                (
                    f"  forecast_mean="
                    f"{signal['forecast_mean_c']}C "
                    f"sigma="
                    f"{signal['forecast_sigma_c']}C"
                ),

                (
                    f"  calibration="
                    f"{signal['calibration_method']} "
                    f"n={signal['calibration_samples']} "
                    f"bias={signal['calibration_bias_c']}C"
                ),

                (
                    f"  signal="
                    f"{signal['signal']}"
                ),

                "",
            ]
        )

    write_text(
        REPORT_FILE,
        "\n".join(
            report_lines
        ),
    )

    # ========================================================
    # SIGNAL HISTORY
    # ========================================================

    history_rows = [
        dict(signal)
        for signal in signals
    ]

    append_csv(
        SIGNAL_HISTORY_FILE,
        SIGNAL_FIELDS,
        history_rows,
    )

    paper_rows = [
        signal
        for signal in paper_candidates
        if signal[
            "signal"
        ]
        == "PAPER_BUY"
    ]

    append_csv(
        PAPER_SIGNAL_FILE,
        SIGNAL_FIELDS,
        paper_rows,
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print("")

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(
        f"Signals evaluated: "
        f"{evaluated}"
    )

    print(
        f"Signals retained: "
        f"{len(signals)}"
    )

    print(
        f"PAPER_BUY candidates: "
        f"{len(paper_candidates)}"
    )

    print(
        f"Calibration records: "
        f"{len(calibration_rows)}"
    )

    print(
        f"Usable calibration records: "
        f"{len(usable_calibration_rows)}"
    )

    print(
        f"Latest JSON: "
        f"{LATEST_FILE}"
    )

    print(
        f"Latest report: "
        f"{REPORT_FILE}"
    )

    print(
        f"Calibration JSON: "
        f"{CALIBRATION_LATEST_FILE}"
    )

    print(
        f"Calibration report: "
        f"{CALIBRATION_REPORT_FILE}"
    )

    print(
        f"Signal history: "
        f"{SIGNAL_HISTORY_FILE}"
    )

    print(
        f"Paper history: "
        f"{PAPER_SIGNAL_FILE}"
    )


if __name__ == "__main__":
    main()

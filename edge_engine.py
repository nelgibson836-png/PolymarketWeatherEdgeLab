import csv
import json
import math
import os
from datetime import datetime, timezone

# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Edge Engine V1.4
#
# RESEARCH / PAPER TRADING ONLY
# NO ORDERS ARE SENT
#
# V1.4:
# - Station-specific historical calibration
# - Historical bias correction
# - Residual sigma from same station
# - Lead-time aware calibration
# - Minimum calibration sample gate
# - Sanity-flag protection
# - Family normalization
# - Executable ask requirement
# ============================================================

ENGINE_VERSION = "1.4"

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

CALIBRATION_FILE = os.path.join(
    DATA_DIR,
    "edge",
    "calibration",
    "station_calibration_summary.csv",
)

EDGE_DIR = os.path.join(
    DATA_DIR,
    "edge",
)

EDGE_HISTORY_DIR = os.path.join(
    EDGE_DIR,
    "history",
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

# ============================================================
# PARAMETERS
# ============================================================

DEFAULT_WEATHER_FEE_RATE = 0.05

DEFAULT_SIGMA_C = 2.00

MIN_SIGMA_C = 0.75
MAX_SIGMA_C = 5.00

MIN_EDGE = 0.03
MIN_EV = 0.015

STRONG_MIN_EDGE = 0.06
STRONG_MIN_EV = 0.02
STRONG_MIN_ASK = 0.005

THIN_ASK_MAX = 0.002

FAMILY_MIN_COVERAGE = 0.90
FAMILY_MAX_COVERAGE = 1.05

MIN_CALIBRATION_SAMPLES = 15

# Groups with these flags are not trusted for PAPER_BUY.
BLOCKED_CALIBRATION_FLAGS = {
    "HIGH_MIN_RMSE",
    "HIGH_MAX_RMSE",
    "HIGH_MIN_BIAS",
    "HIGH_MAX_BIAS",
}

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
    "calibration_source",
    "calibration_samples",
    "calibration_bias_c",
    "calibration_sigma_c",
    "calibration_flag",
    "observation_temperature_c",
    "observation_time",
    "execution_quality",
    "signal",
    "reason",
]

# ============================================================
# BASIC HELPERS
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


def write_json(
    path,
    payload,
):
    directory = os.path.dirname(path)

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
        handle.write("\n")

    os.replace(
        temporary,
        path,
    )


def write_text(
    path,
    text,
):
    directory = os.path.dirname(path)

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
            writer.writerow(
                row
            )


# ============================================================
# MATH
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
        sigma
        * math.sqrt(2.0)
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
# UNIT HANDLING
# ============================================================

def fahrenheit_to_celsius(
    value
):
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
# WEATHER RECORDS
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
            str(
                station
            ).upper(),
            market_date,
            str(
                model
            ),
        )

        previous = latest.get(
            key
        )

        current_time = row.get(
            "collected_at",
            "",
        )

        previous_time = (
            previous.get(
                "collected_at",
                "",
            )
            if previous
            else ""
        )

        if (
            previous is None
            or current_time
            > previous_time
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

        previous = latest.get(
            station
        )

        current_time = (
            row.get(
                "collected_at",
                "",
            )
        )

        previous_time = (
            previous.get(
                "collected_at",
                "",
            )
            if previous
            else ""
        )

        if (
            previous is None
            or current_time
            > previous_time
        ):
            latest[
                station
            ] = row

    return latest


# ============================================================
# CALIBRATION
# ============================================================

def load_calibration():
    rows = read_csv(
        CALIBRATION_FILE
    )

    calibration = {}

    for row in rows:

        station = (
            row.get(
                "station"
            )
            or ""
        ).upper()

        model = (
            row.get(
                "model"
            )
            or ""
        )

        lead_days = safe_int(
            row.get(
                "lead_days"
            )
        )

        if (
            not station
            or not model
            or lead_days is None
        ):
            continue

        samples = safe_int(
            row.get(
                "samples"
            )
        )

        bias = safe_float(
            row.get(
                "max_bias_c"
            )
        )

        sigma = safe_float(
            row.get(
                "max_sigma_c"
            )
        )

        # We are primarily interested in highest_temperature
        # markets, so max calibration is used here.
        #
        # If invalid, fall back to min calibration.
        if bias is None:
            bias = safe_float(
                row.get(
                    "min_bias_c"
                )
            )

        if sigma is None:
            sigma = safe_float(
                row.get(
                    "min_sigma_c"
                )
            )

        usable = (
            str(
                row.get(
                    "usable",
                    ""
                )
            ).upper()
            == "YES"
        )

        calibration[
            (
                station,
                model,
                lead_days,
            )
        ] = {
            "samples": (
                samples
                if samples is not None
                else 0
            ),

            "bias_c": (
                bias
                if bias is not None
                else 0.0
            ),

            "sigma_c": (
                sigma
                if sigma is not None
                else DEFAULT_SIGMA_C
            ),

            "usable": usable,

            "flag": (
                "OK"
                if usable
                else "UNUSABLE_SAMPLE"
            ),
        }

    return calibration


def lookup_calibration(
    calibration,
    station,
    model,
    lead_days,
):
    key = (
        str(
            station
        ).upper(),
        str(
            model
        ),
        lead_days,
    )

    item = calibration.get(
        key
    )

    if item:
        return {
            **item,
            "source": (
                "station_model_lead"
            ),
        }

    # Fallback to another model for same station/lead
    candidates = []

    for (
        current_station,
        current_model,
        current_lead,
    ), candidate in calibration.items():

        if (
            current_station
            == str(
                station
            ).upper()
            and current_lead
            == lead_days
        ):
            candidates.append(
                candidate
            )

    usable_candidates = [
        item
        for item in candidates
        if item[
            "usable"
        ]
    ]

    if usable_candidates:
        best = max(
            usable_candidates,
            key=lambda item:
            item["samples"],
        )

        return {
            **best,
            "source": (
                "station_lead_other_model"
            ),
        }

    return {
        "samples": 0,
        "bias_c": 0.0,
        "sigma_c": DEFAULT_SIGMA_C,
        "usable": False,
        "flag": "NO_CALIBRATION",
        "source": "default",
    }


# ============================================================
# LEAD TIME
# ============================================================

def infer_lead_days(
    row
):
    try:
        market_date = datetime.strptime(
            str(
                row.get(
                    "market_date"
                )
            )[:10],
            "%Y-%m-%d",
        ).date()

        collected = row.get(
            "collected_at"
        )

        if not collected:
            return 1

        text = str(
            collected
        )

        if text.endswith("Z"):
            text = (
                text[:-1]
                + "+00:00"
            )

        collected_dt = datetime.fromisoformat(
            text
        )

        if (
            collected_dt.tzinfo
            is None
        ):
            collected_dt = collected_dt.replace(
                tzinfo=timezone.utc
            )

        collection_date = (
            collected_dt.astimezone(
                timezone.utc
            ).date()
        )

        difference = (
            market_date
            - collection_date
        ).days

        if difference <= 0:
            return 0

        return difference

    except Exception:
        return 1


# ============================================================
# CURRENT FORECAST CONSENSUS
# ============================================================

def build_forecast_stats(
    forecasts,
    station,
    market_date,
    market_type,
    calibration,
):
    entries = []

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

        model = str(
            row.get(
                "model"
            )
            or "unknown"
        )

        if market_type == (
            "lowest_temperature"
        ):
            raw_value = safe_float(
                row.get(
                    "temperature_min_c"
                )
            )
        else:
            raw_value = safe_float(
                row.get(
                    "temperature_max_c"
                )
            )

        if raw_value is None:
            continue

        lead_days = infer_lead_days(
            row
        )

        calibrated = lookup_calibration(
            calibration,
            station,
            model,
            lead_days,
        )

        bias = calibrated[
            "bias_c"
        ]

        corrected = (
            raw_value
            + bias
        )

        sigma = safe_float(
            calibrated[
                "sigma_c"
            ]
        )

        if (
            sigma is None
            or sigma <= 0
        ):
            sigma = DEFAULT_SIGMA_C

        sigma = max(
            MIN_SIGMA_C,
            min(
                MAX_SIGMA_C,
                sigma,
            ),
        )

        entries.append(
            {
                "model": model,
                "raw_value_c": raw_value,
                "corrected_value_c": corrected,
                "sigma_c": sigma,
                "bias_c": bias,
                "samples": calibrated[
                    "samples"
                ],
                "calibration_source": calibrated[
                    "source"
                ],
                "calibration_flag": calibrated[
                    "flag"
                ],
                "lead_days": lead_days,
            }
        )

    if not entries:
        return None

    corrected_values = [
        item[
            "corrected_value_c"
        ]
        for item in entries
    ]

    raw_values = [
        item[
            "raw_value_c"
        ]
        for item in entries
    ]

    model_sigmas = [
        item[
            "sigma_c"
        ]
        for item in entries
    ]

    mean_corrected = sum(
        corrected_values
    ) / len(
        corrected_values
    )

    raw_mean = sum(
        raw_values
    ) / len(
        raw_values
    )

    if len(
        corrected_values
    ) > 1:

        between_model_variance = (
            sum(
                (
                    value
                    - mean_corrected
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

    average_model_variance = (
        sum(
            sigma * sigma
            for sigma in model_sigmas
        )
        / len(
            model_sigmas
        )
    )

    combined_sigma = math.sqrt(
        max(
            MIN_SIGMA_C,
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

    total_samples = sum(
        item[
            "samples"
        ]
        for item in entries
    )

    weighted_bias = (
        sum(
            item[
                "bias_c"
            ]
            * max(
                1,
                item[
                    "samples"
                ],
            )
            for item in entries
        )
        / max(
            1,
            total_samples,
        )
    )

    sources = sorted(
        set(
            item[
                "calibration_source"
            ]
            for item in entries
        )
    )

    flags = sorted(
        set(
            item[
                "calibration_flag"
            ]
            for item in entries
        )
    )

    all_calibrated = all(
        item[
            "samples"
        ]
        >= MIN_CALIBRATION_SAMPLES
        and item[
            "calibration_flag"
        ]
        == "OK"
        for item in entries
    )

    return {
        "values": corrected_values,
        "raw_values": raw_values,
        "models": [
            item[
                "model"
            ]
            for item in entries
        ],
        "mean_c": mean_corrected,
        "raw_mean_c": raw_mean,
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
        "calibration_source": ",".join(
            sources
        ),
        "calibration_flag": ",".join(
            flags
        ),
        "all_calibrated": all_calibrated,
        "forecast_count": len(
            entries
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
# FAMILY
# ============================================================

def group_market_families(
    markets
):
    groups = {}

    for market in markets:

        event_key = market.get(
            "event_key"
        )

        if not event_key:
            continue

        groups.setdefault(
            event_key,
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

        probability = bucket_probability(
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
# SIGNAL
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

    bucket_type = normalized.get(
        "bucket_type"
    )

    raw_probability = bucket_probability(
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
        forecast_stats[
            "mean_c"
        ],
        forecast_stats[
            "sigma_c"
        ],
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

    # --------------------------------------------------------
    # EXECUTION
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # CALIBRATION GATE
    # --------------------------------------------------------

    calibration_ok = (
        forecast_stats[
            "all_calibrated"
        ]
    )

    calibration_samples = (
        forecast_stats[
            "calibration_samples"
        ]
    )

    calibration_flag = (
        forecast_stats[
            "calibration_flag"
        ]
    )

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

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

        if not calibration_ok:
            reason_parts.append(
                "calibration_not_fully_valid"
            )

        strong_ok = (
            entry_source == "ask"

            and execution_quality
            == "EXECUTABLE_ASK"

            and family_check[
                "status"
            ] == "PASS"

            and gross_edge
            >= STRONG_MIN_EDGE

            and net_ev
            >= STRONG_MIN_EV

            and calibration_ok

            and calibration_samples
            >= MIN_CALIBRATION_SAMPLES
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

        elif possible_ok:

            signal = (
                "POSSIBLE_EDGE"
            )

            reason_parts.append(
                "research_thresholds_pass"
            )

    # --------------------------------------------------------
    # PAPER BUY
    # --------------------------------------------------------

    paper_buy = (
        signal == "STRONG_EDGE"

        and entry_source == "ask"

        and execution_quality
        == "EXECUTABLE_ASK"

        and family_check[
            "status"
        ] == "PASS"

        and calibration_ok

        and calibration_samples
        >= MIN_CALIBRATION_SAMPLES

        and not any(
            flag in BLOCKED_CALIBRATION_FLAGS
            for flag in calibration_flag.split(",")
        )
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

    # --------------------------------------------------------
    # OBSERVATION
    # --------------------------------------------------------

    observation_temperature = None

    observation_time = None

    if observation:

        observation_temperature = (
            safe_float(
                observation.get(
                    "temperature_c"
                )
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
            raw_probability
        ),

        "model_probability": rounded(
            model_probability
        ),

        "family_exclusive_probability_sum": rounded(
            family_check[
                "sum"
            ]
        ),

        "family_probability_status": (
            family_check[
                "status"
            ]
        ),

        "market_probability": rounded(
            current_probability
        ),

        "entry_price": rounded(
            entry_price
        ),

        "entry_source": entry_source,

        "fee_rate": rounded(
            fee_rate
        ),

        "fee_source": fee_source,

        "fee_per_share": rounded(
            fee,
            8,
        ),

        "gross_edge": rounded(
            gross_edge
        ),

        "net_ev_per_share": rounded(
            net_ev
        ),

        "net_return_if_win": rounded(
            net_return_if_win
        ),

        "forecast_count": (
            forecast_stats[
                "forecast_count"
            ]
        ),

        "forecast_models": ",".join(
            forecast_stats[
                "models"
            ]
        ),

        "forecast_mean_c": rounded(
            forecast_stats[
                "mean_c"
            ],
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
            forecast_stats[
                "sigma_c"
            ],
            4,
        ),

        "calibration_source": (
            forecast_stats[
                "calibration_source"
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
            forecast_stats[
                "sigma_c"
            ],
            4,
        ),

        "calibration_flag": (
            calibration_flag
        ),

        "observation_temperature_c": rounded(
            observation_temperature,
            4,
        ),

        "observation_time": (
            observation_time
        ),

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
        f"  Edge: "
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
        f"{signal['calibration_source']} "
        f"n={signal['calibration_samples']} "
        f"bias={signal['calibration_bias_c']}C "
        f"flag={signal['calibration_flag']}"
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
# MAIN
# ============================================================

def main():

    os.makedirs(
        EDGE_HISTORY_DIR,
        exist_ok=True,
    )

    print("=" * 70)

    print(
        f"POLYMARKET WEATHER "
        f"EDGE ENGINE V{ENGINE_VERSION}"
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

    # --------------------------------------------------------
    # INPUT VALIDATION
    # --------------------------------------------------------

    required_files = [
        ACTIVE_FILE,
        FORECAST_FILE,
        CALIBRATION_FILE,
    ]

    for path in required_files:

        if not os.path.exists(
            path
        ):
            raise FileNotFoundError(
                path
            )

    # --------------------------------------------------------
    # LOAD MARKETS
    # --------------------------------------------------------

    active_payload = read_json(
        ACTIVE_FILE
    )

    markets = active_payload.get(
        "markets",
        []
    )

    all_forecasts = read_csv(
        FORECAST_FILE
    )

    forecasts = (
        select_latest_forecasts(
            all_forecasts
        )
    )

    observations = (
        select_latest_observations(
            read_csv(
                OBSERVATION_FILE
            )
        )
    )

    calibration = (
        load_calibration()
    )

    print(
        f"Current markets: "
        f"{len(markets)}"
    )

    print(
        f"Latest forecast records: "
        f"{len(forecasts)}"
    )

    print(
        f"Latest observations: "
        f"{len(observations)}"
    )

    print(
        f"Calibration groups: "
        f"{len(calibration)}"
    )

    usable_calibration = sum(
        1
        for item in calibration.values()
        if item[
            "usable"
        ]
    )

    print(
        f"Usable calibration groups: "
        f"{usable_calibration}"
    )

    # --------------------------------------------------------
    # FAMILIES
    # --------------------------------------------------------

    families = (
        group_market_families(
            markets
        )
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

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

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
            ] = build_forecast_stats(
                forecasts,
                station,
                market_date,
                market_type,
                calibration,
            )

        stats = stats_cache[
            cache_key
        ]

        if not stats:

            no_forecast += len(
                family
            )

            continue

        observation = (
            observations.get(
                str(
                    station
                ).upper()
            )
        )

        family_check = (
            family_probability_sum(
                family,
                stats,
            )
        )

        family_signals = []

        for market in family:

            signal = create_signal(
                market,
                stats,
                observation,
                family_check,
            )

            if signal is None:
                continue

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

    # --------------------------------------------------------
    # GLOBAL RANKING
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # DISPLAY
    # --------------------------------------------------------

    print("")
    print("=" * 70)
    print("TOP SIGNALS")
    print("=" * 70)

    for signal in signals[:20]:
        print_signal(
            signal
        )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    payload = {
        "engine_version": (
            ENGINE_VERSION
        ),

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

            "min_calibration_samples": (
                MIN_CALIBRATION_SAMPLES
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
        },

        "current_markets": len(
            markets
        ),

        "families": len(
            families
        ),

        "forecast_records": len(
            forecasts
        ),

        "observation_records": len(
            observations
        ),

        "calibration_groups": len(
            calibration
        ),

        "usable_calibration_groups": (
            usable_calibration
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
            "calibration_source": (
                "station_calibration_summary.csv"
            ),

            "paper_buy_requires_calibration": True,

            "paper_buy_min_samples": (
                MIN_CALIBRATION_SAMPLES
            ),

            "blocked_calibration_flags": sorted(
                BLOCKED_CALIBRATION_FLAGS
            ),

            "paper_buy_requires_executable_ask": True,

            "cumulative_bucket_handling": (
                "or_lower/or_higher are evaluated "
                "directly and excluded from "
                "exclusive family normalization"
            ),

            "orders_sent": False,
        },

        "signals": signals,
    }

    write_json(
        LATEST_FILE,
        payload,
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report_lines = [
        (
            f"POLYMARKET WEATHER "
            f"EDGE ENGINE V{ENGINE_VERSION}"
        ),

        "RESEARCH / PAPER TRADING ONLY",

        "NO ORDERS SENT",

        f"UTC: {run_timestamp}",

        "",

        (
            f"Current markets: "
            f"{len(markets)}"
        ),

        (
            f"Families: "
            f"{len(families)}"
        ),

        (
            f"Forecast records: "
            f"{len(forecasts)}"
        ),

        (
            f"Observation records: "
            f"{len(observations)}"
        ),

        (
            f"Calibration groups: "
            f"{len(calibration)}"
        ),

        (
            f"Usable calibration groups: "
            f"{usable_calibration}"
        ),

        (
            f"Signals evaluated: "
            f"{evaluated}"
        ),

        (
            f"Signals retained: "
            f"{len(signals)}"
        ),

        (
            f"PAPER_BUY candidates: "
            f"{len(paper_candidates)}"
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
                    f"  bucket="
                    f"{signal['bucket_type']} "
                    f"value="
                    f"{signal['bucket_value']} "
                    f"low="
                    f"{signal['bucket_low']} "
                    f"high="
                    f"{signal['bucket_high']}"
                ),

                (
                    f"  model="
                    f"{signal['model_probability']} "
                    f"market="
                    f"{signal['market_probability']} "
                    f"ask="
                    f"{signal['entry_price']}"
                ),

                (
                    f"  family_sum="
                    f"{signal['family_exclusive_probability_sum']} "
                    f"family_status="
                    f"{signal['family_probability_status']}"
                ),

                (
                    f"  edge="
                    f"{signal['gross_edge']} "
                    f"net_ev="
                    f"{signal['net_ev_per_share']}"
                ),

                (
                    f"  forecast_mean="
                    f"{signal['forecast_mean_c']}C "
                    f"sigma="
                    f"{signal['forecast_sigma_c']}C"
                ),

                (
                    f"  calibration="
                    f"{signal['calibration_source']} "
                    f"n="
                    f"{signal['calibration_samples']} "
                    f"bias="
                    f"{signal['calibration_bias_c']}C "
                    f"flag="
                    f"{signal['calibration_flag']}"
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

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    append_csv(
        SIGNAL_HISTORY_FILE,
        SIGNAL_FIELDS,
        signals,
    )

    append_csv(
        PAPER_SIGNAL_FILE,
        SIGNAL_FIELDS,
        paper_candidates,
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

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
        f"Calibration groups: "
        f"{len(calibration)}"
    )

    print(
        f"Usable calibration groups: "
        f"{usable_calibration}"
    )

    print(
        f"Latest JSON: "
        f"{LATEST_FILE}"
    )

    print(
        f"Latest report: "
        f"{REPORT_FILE}"
    )


if __name__ == "__main__":
    main()

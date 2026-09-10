import csv
import json
import math
import os
from datetime import datetime, timezone

# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Edge Engine V1.2
#
# RESEARCH / PAPER TRADING ONLY
# NO ORDERS ARE SENT
# ============================================================

ENGINE_VERSION = "1.2"

DATA_DIR = "data"
ACTIVE_FILE = os.path.join(DATA_DIR, "active_temperature_markets.json")
FORECAST_FILE = os.path.join(DATA_DIR, "weather", "history", "forecasts.csv")
OBSERVATION_FILE = os.path.join(DATA_DIR, "weather", "history", "observations.csv")

EDGE_DIR = os.path.join(DATA_DIR, "edge")
EDGE_HISTORY_DIR = os.path.join(EDGE_DIR, "history")
LATEST_FILE = os.path.join(EDGE_DIR, "latest_signals.json")
REPORT_FILE = os.path.join(EDGE_DIR, "latest_report.txt")
SIGNAL_HISTORY_FILE = os.path.join(EDGE_HISTORY_DIR, "signals.csv")
PAPER_SIGNAL_FILE = os.path.join(EDGE_DIR, "paper_signals.csv")

# ============================================================
# STRATEGY PARAMETERS
# ============================================================

DEFAULT_WEATHER_FEE_RATE = 0.05

DEFAULT_SIGMA_C = 1.20
MIN_SIGMA_C = 0.90
MAX_SIGMA_C = 3.00

# Candidate thresholds.
MIN_EDGE = 0.03
MIN_EV = 0.015

# Strong signal requires materially more evidence.
STRONG_MIN_EDGE = 0.06
STRONG_MIN_EV = 0.02
STRONG_MIN_ASK = 0.005

# Extremely cheap asks are treated as thin/stale-price research candidates,
# not as strong paper trades.
THIN_ASK_MAX = 0.002

# A family made of exclusive exact/range buckets should cover most of the
# modeled distribution. Cumulative buckets are excluded from this sum.
FAMILY_MIN_COVERAGE = 0.90
FAMILY_MAX_COVERAGE = 1.05

# We need a real executable ask for a PAPER_BUY candidate.
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
    "observation_temperature_c",
    "observation_time",
    "execution_quality",
    "signal",
    "reason",
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
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_csv(path):
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path, payload):
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    temporary = path + ".tmp"

    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    os.replace(temporary, path)


def write_text(path, text):
    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    temporary = path + ".tmp"

    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)

    os.replace(temporary, path)


def append_csv(path, fields, rows):
    if not rows:
        return

    directory = os.path.dirname(path)

    if directory:
        os.makedirs(directory, exist_ok=True)

    exists = os.path.exists(path)

    with open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
        )

        if not exists:
            writer.writeheader()

        for row in rows:
            writer.writerow(row)


def rounded(value, digits=6):
    if value is None:
        return None

    return round(float(value), digits)


# ============================================================
# MATHEMATICS
# ============================================================

def normal_cdf(x, mean, sigma):
    if sigma <= 0:
        return None

    z = (x - mean) / (sigma * math.sqrt(2.0))

    return 0.5 * (1.0 + math.erf(z))


def clamp_probability(value):
    if value is None:
        return None

    return max(0.0, min(1.0, float(value)))


def bucket_probability(
    bucket_type,
    bucket_value,
    bucket_low,
    bucket_high,
    mean_c,
    sigma_c,
):
    if mean_c is None or sigma_c is None or sigma_c <= 0:
        return None

    if bucket_type == "exact":
        value = safe_float(bucket_value)

        if value is None:
            return None

        return clamp_probability(
            normal_cdf(value + 0.5, mean_c, sigma_c)
            - normal_cdf(value - 0.5, mean_c, sigma_c)
        )

    if bucket_type == "or_lower":
        threshold = safe_float(bucket_value)

        if threshold is None:
            return None

        return clamp_probability(
            normal_cdf(threshold + 0.5, mean_c, sigma_c)
        )

    if bucket_type == "or_higher":
        threshold = safe_float(bucket_value)

        if threshold is None:
            return None

        return clamp_probability(
            1.0 - normal_cdf(threshold - 0.5, mean_c, sigma_c)
        )

    if bucket_type == "range":
        low = safe_float(bucket_low)
        high = safe_float(bucket_high)

        if low is None or high is None or high < low:
            return None

        return clamp_probability(
            normal_cdf(high + 0.5, mean_c, sigma_c)
            - normal_cdf(low - 0.5, mean_c, sigma_c)
        )

    return None


# ============================================================
# TEMPERATURE UNIT
# ============================================================

def fahrenheit_to_celsius(value):
    return (value - 32.0) * 5.0 / 9.0


def normalize_bucket_to_celsius(market):
    normalized = dict(market)

    unit = str(
        market.get("temperature_unit")
        or market.get("unit")
        or "C"
    ).upper()

    if unit != "F":
        return normalized

    for key in (
        "bucket_value",
        "bucket_low",
        "bucket_high",
    ):
        value = safe_float(market.get(key))

        normalized[key] = (
            None
            if value is None
            else fahrenheit_to_celsius(value)
        )

    return normalized


# ============================================================
# LATEST WEATHER RECORDS
# ============================================================

def select_latest_forecasts(rows):
    latest = {}

    for row in rows:
        station = row.get("station")
        market_date = row.get("market_date")
        model = row.get("model")

        if not station or not market_date or not model:
            continue

        key = (
            station,
            market_date,
            model,
        )

        previous = latest.get(key)

        collected_at = row.get("collected_at", "")
        previous_at = previous.get("collected_at", "") if previous else ""

        if previous is None or collected_at > previous_at:
            latest[key] = row

    return list(latest.values())


def select_latest_observations(rows):
    latest = {}

    for row in rows:
        station = row.get("station")

        if not station:
            continue

        previous = latest.get(station)

        collected_at = row.get("collected_at", "")
        previous_at = previous.get("collected_at", "") if previous else ""

        if previous is None or collected_at > previous_at:
            latest[station] = row

    return latest


# ============================================================
# FORECAST CONSENSUS
# ============================================================

def build_forecast_stats(
    forecasts,
    station,
    market_date,
    market_type,
):
    matching = []

    for row in forecasts:
        if row.get("station") != station:
            continue

        if row.get("market_date") != market_date:
            continue

        if market_type == "lowest_temperature":
            value = safe_float(row.get("temperature_min_c"))
        else:
            value = safe_float(row.get("temperature_max_c"))

        if value is not None:
            matching.append(
                (
                    row.get("model") or "unknown",
                    value,
                )
            )

    if not matching:
        return None

    values = [
        value
        for _, value in matching
    ]

    mean_c = sum(values) / len(values)

    if len(values) > 1:
        std_c = math.sqrt(
            sum(
                (value - mean_c) ** 2
                for value in values
            )
            / (len(values) - 1)
        )
    else:
        std_c = 0.0

    sigma_c = max(
        MIN_SIGMA_C,
        min(
            MAX_SIGMA_C,
            max(
                DEFAULT_SIGMA_C,
                std_c * 1.15,
            ),
        ),
    )

    return {
        "values": values,
        "models": [
            model
            for model, _ in matching
        ],
        "mean_c": mean_c,
        "min_c": min(values),
        "max_c": max(values),
        "std_c": std_c,
        "sigma_c": sigma_c,
    }


# ============================================================
# MARKET ENTRY / FEES
# ============================================================

def candidate_entry_price(market):
    ask = safe_float(
        market.get("best_ask")
    )

    if ask is not None and 0.0 < ask < 1.0:
        return ask, "ask"

    yes_price = safe_float(
        market.get("yes_price")
    )

    if (
        not REQUIRE_EXECUTABLE_ASK
        and yes_price is not None
        and 0.0 < yes_price < 1.0
    ):
        return yes_price, "yes_price_research"

    return None, "none"


def market_probability(market):
    return safe_float(
        market.get("yes_price")
    )


def get_fee_rate(market):
    enabled = market.get("fees_enabled")

    if enabled is False:
        return 0.0, "disabled"

    schedule = market.get(
        "fee_schedule"
    )

    if isinstance(schedule, str):
        try:
            schedule = json.loads(schedule)
        except Exception:
            schedule = None

    if isinstance(schedule, dict):
        for key in (
            "rate",
            "feeRate",
            "fee_rate",
            "r",
        ):
            rate = safe_float(
                schedule.get(key)
            )

            if rate is not None:
                return rate, "market"

    return (
        DEFAULT_WEATHER_FEE_RATE,
        "default_weather",
    )


def fee_per_share(
    entry_price,
    fee_rate,
):
    if entry_price is None or fee_rate <= 0:
        return 0.0

    return (
        fee_rate
        * entry_price
        * (1.0 - entry_price)
    )


# ============================================================
# FAMILY GROUPING / CONSISTENCY
# ============================================================

def group_market_families(markets):
    groups = {}

    for market in markets:
        key = market.get(
            "event_key"
        )

        if not key:
            continue

        groups.setdefault(
            key,
            []
        ).append(market)

    return groups


def family_probability_sum(
    family,
    stats,
):
    """
    Sum only exclusive exact/range buckets.

    or_lower / or_higher are cumulative and overlap with
    exact/range buckets, so they are NOT included in the
    exclusive family sum.
    """

    exclusive = []
    cumulative = []

    for market in family:
        normalized = normalize_bucket_to_celsius(
            market
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
            stats["mean_c"],
            stats["sigma_c"],
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

    total = sum(exclusive)

    if total < FAMILY_MIN_COVERAGE:
        status = "INCOMPLETE"

    elif total > FAMILY_MAX_COVERAGE:
        status = "OVERLAP_OR_PARSE_ERROR"

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

    if family_sum is None or family_sum <= 0:
        return raw_probability

    return clamp_probability(
        raw_probability / family_sum
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
    normalized = normalize_bucket_to_celsius(
        market
    )

    mean_c = forecast_stats["mean_c"]
    sigma_c = forecast_stats["sigma_c"]

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
        mean_c,
        sigma_c,
    )

    if raw_probability is None:
        return None

    # Normalize only exclusive buckets.
    if (
        bucket_type in (
            "exact",
            "range",
        )
        and family_check["sum"] is not None
    ):
        model_probability = (
            normalize_exclusive_probability(
                raw_probability,
                family_check["sum"],
            )
        )
    else:
        model_probability = raw_probability

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
    # EXECUTION QUALITY
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
    # SIGNAL CLASSIFICATION
    # --------------------------------------------------------

    signal = "NO_TRADE"
    reason_parts = []

    if entry_price is None:
        reason_parts.append(
            "no_executable_ask"
        )

    else:
        if (
            family_check["status"] != "PASS"
            and bucket_type in (
                "exact",
                "range",
            )
        ):
            reason_parts.append(
                f"family={family_check['status']}"
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
            and family_check["status"]
            == "PASS"
            and entry_price
            >= STRONG_MIN_ASK
            and gross_edge
            >= STRONG_MIN_EDGE
            and net_ev
            >= STRONG_MIN_EV
        )

        possible_ok = (
            gross_edge >= MIN_EDGE
            and net_ev >= MIN_EV
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
            signal = "STRONG_EDGE"

            reason_parts.append(
                "strong_thresholds_pass"
            )

        elif possible_ok:
            signal = "POSSIBLE_EDGE"

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
        and family_check["status"]
        == "PASS"
    )

    if paper_buy:
        signal_output = "PAPER_BUY"

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

        observation_time = observation.get(
            "observation_time"
        )

    # --------------------------------------------------------
    # FINAL SIGNAL
    # --------------------------------------------------------

    return {
        "run_at": iso_now(),

        "city": market.get("city"),

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
            family_check["sum"],
            6,
        ),

        "family_probability_status": family_check[
            "status"
        ],

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

        "forecast_count": len(
            forecast_stats[
                "values"
            ]
        ),

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

        "observation_temperature_c": rounded(
            observation_temperature,
            4,
        ),

        "observation_time": observation_time,

        "execution_quality": execution_quality,

        "signal": signal_output,

        "reason": "; ".join(
            reason_parts
        ),
    }


# ============================================================
# DISPLAY
# ============================================================

def print_signal(signal):
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

    # --------------------------------------------------------
    # INPUT VALIDATION
    # --------------------------------------------------------

    for required in (
        ACTIVE_FILE,
        FORECAST_FILE,
    ):
        if not os.path.exists(required):
            raise FileNotFoundError(
                required
            )

    active_payload = read_json(
        ACTIVE_FILE
    )

    markets = active_payload.get(
        "markets",
        []
    )

    forecast_rows = (
        select_latest_forecasts(
            read_csv(
                FORECAST_FILE
            )
        )
    )

    observation_rows = (
        select_latest_observations(
            read_csv(
                OBSERVATION_FILE
            )
        )
    )

    print(
        f"Current markets: "
        f"{len(markets)}"
    )

    print(
        f"Latest forecast records: "
        f"{len(forecast_rows)}"
    )

    print(
        f"Latest observations: "
        f"{len(observation_rows)}"
    )

    # --------------------------------------------------------
    # FAMILY GROUPING
    # --------------------------------------------------------

    families = group_market_families(
        markets
    )

    print(
        f"Families: {len(families)}"
    )

    signals = []
    paper_candidates = []

    stats_cache = {}

    evaluated = 0
    no_station = 0
    no_forecast = 0

    # --------------------------------------------------------
    # PROCESS FAMILIES
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

        if not station or not market_date:
            no_station += len(
                family
            )
            continue

        cache_key = (
            station,
            market_date,
            market_type,
        )

        if cache_key not in stats_cache:
            stats_cache[
                cache_key
            ] = build_forecast_stats(
                forecast_rows,
                station,
                market_date,
                market_type,
            )

        stats = stats_cache[
            cache_key
        ]

        if not stats:
            no_forecast += len(
                family
            )
            continue

        observation = observation_rows.get(
            station
        )

        family_check = (
            family_probability_sum(
                family,
                stats,
            )
        )

        family_signals = []

        # ----------------------------------------------------
        # EVALUATE MARKETS
        # ----------------------------------------------------

        for market in family:
            signal = create_signal(
                market,
                stats,
                observation,
                family_check,
            )

            if signal is not None:
                family_signals.append(
                    signal
                )

                evaluated += 1

        # Keep best few per family.
        family_signals.sort(
            key=lambda item: (
                item["signal"]
                == "PAPER_BUY",

                item["signal"]
                == "STRONG_EDGE",

                item["signal"]
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
                signal["signal"]
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
                item["signal"],
                0,
            ),

            item[
                "net_ev_per_share"
            ]
            or -999.0,

            item["gross_edge"]
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

            item["gross_edge"]
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
    # OUTPUT JSON
    # --------------------------------------------------------

    payload = {
        "engine_version": ENGINE_VERSION,

        "run_at": run_timestamp,

        "mode": "research_paper",

        "status": "NO_ORDERS_SENT",

        "parameters": {
            "min_edge": MIN_EDGE,

            "min_ev": MIN_EV,

            "strong_min_edge": STRONG_MIN_EDGE,

            "strong_min_ev": STRONG_MIN_EV,

            "strong_min_ask": STRONG_MIN_ASK,

            "thin_ask_max": THIN_ASK_MAX,

            "family_min_coverage": FAMILY_MIN_COVERAGE,

            "family_max_coverage": FAMILY_MAX_COVERAGE,

            "require_executable_ask": REQUIRE_EXECUTABLE_ASK,

            "default_weather_fee_rate": DEFAULT_WEATHER_FEE_RATE,

            "default_sigma_c": DEFAULT_SIGMA_C,

            "min_sigma_c": MIN_SIGMA_C,

            "max_sigma_c": MAX_SIGMA_C,
        },

        "current_markets": len(
            markets
        ),

        "families": len(
            families
        ),

        "forecast_records": len(
            forecast_rows
        ),

        "observation_records": len(
            observation_rows
        ),

        "signals_evaluated": evaluated,

        "signals_retained": len(
            signals
        ),

        "paper_buy_candidates": len(
            paper_candidates
        ),

        "diagnostics": {
            "markets_skipped_no_station": no_station,

            "markets_skipped_no_forecast": no_forecast,

            "paper_buy_definition": (
                "STRONG_EDGE + executable best_ask + "
                "passing exclusive family coverage"
            ),

            "cumulative_bucket_handling": (
                "or_lower/or_higher are evaluated directly "
                "and excluded from exclusive family normalization"
            ),
        },

        "signals": signals,
    }

    write_json(
        LATEST_FILE,
        payload,
    )

    # --------------------------------------------------------
    # TEXT REPORT
    # --------------------------------------------------------

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

        f"Signals evaluated: {evaluated}",

        f"Signals retained: {len(signals)}",

        (
            f"PAPER_BUY candidates: "
            f"{len(paper_candidates)}"
        ),

        (
            f"Skipped without station: "
            f"{no_station}"
        ),

        (
            f"Skipped without forecast: "
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

    history_rows = []

    for signal in signals:
        history_rows.append(
            dict(signal)
        )

    append_csv(
        SIGNAL_HISTORY_FILE,
        SIGNAL_FIELDS,
        history_rows,
    )

    paper_rows = [
        signal
        for signal in paper_candidates
        if signal["signal"]
        == "PAPER_BUY"
    ]

    append_csv(
        PAPER_SIGNAL_FILE,
        SIGNAL_FIELDS,
        paper_rows,
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
        f"Latest JSON: "
        f"{LATEST_FILE}"
    )

    print(
        f"Latest report: "
        f"{REPORT_FILE}"
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

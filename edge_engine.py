import csv
import json
import math
import os
from datetime import datetime, timezone


# ============================================================
# POLYMARKET WEATHER EDGE LAB
# Edge Engine V1.0
#
# RESEARCH / PAPER TRADING ONLY
# NO ORDERS ARE SENT
# ============================================================

ENGINE_VERSION = "1.0"

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
# STRATEGY PARAMETERS
# ============================================================

# Current Polymarket Weather taker fee rate.
DEFAULT_WEATHER_FEE_RATE = 0.05

# Initial heuristic uncertainty.
#
# IMPORTANT:
# This is NOT a calibrated weather probability model.
# It is a first research model that converts the three
# forecast point estimates into a temperature distribution.
DEFAULT_SIGMA_C = 1.20

MIN_SIGMA_C = 0.90
MAX_SIGMA_C = 3.00

# Minimum gross model edge required.
MIN_EDGE = 0.03

# Minimum expected value per share after estimated fee.
MIN_EV = 0.015

# Maximum signals retained in output.
MAX_SIGNALS_PER_RUN = 100


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
    "model_probability",
    "market_probability",
    "entry_price",
    "fee_rate",
    "fee_per_share",
    "gross_edge",
    "net_ev_per_share",
    "net_return_if_win",
    "forecast_count",
    "forecast_mean_c",
    "forecast_min_c",
    "forecast_max_c",
    "forecast_std_c",
    "observation_temperature_c",
    "observation_time",
    "signal",
    "reason",
]


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(
        timezone.utc
    )


def iso_now():
    return now_utc().isoformat()


def safe_float(value):
    try:
        if value in (
            None,
            "",
        ):
            return None

        return float(
            value
        )

    except (
        ValueError,
        TypeError,
    ):
        return None


def read_json(path):

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as handle:

        return json.load(
            handle
        )


def read_csv(path):

    if not os.path.exists(
        path
    ):
        return []

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


def append_csv(
    path,
    fields,
    rows,
):

    if not rows:
        return

    directory = os.path.dirname(
        path
    )

    if directory:

        os.makedirs(
            directory,
            exist_ok=True,
        )

    exists = os.path.exists(
        path
    )

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
# MATHEMATICS
# ============================================================

def normal_cdf(
    x,
    mean,
    sigma,
):

    z = (
        x - mean
    ) / (
        sigma
        * math.sqrt(2.0)
    )

    return 0.5 * (
        1.0
        + math.erf(z)
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
        or
        sigma_c <= 0
    ):

        return None

    # --------------------------------------------------------
    # EXACT INTEGER TEMPERATURE
    #
    # Example:
    # 10°C = [9.5, 10.5)
    # --------------------------------------------------------

    if bucket_type == "exact":

        value = safe_float(
            bucket_value
        )

        if value is None:
            return None

        low = (
            value - 0.5
        )

        high = (
            value + 0.5
        )

        probability = (
            normal_cdf(
                high,
                mean_c,
                sigma_c,
            )
            -
            normal_cdf(
                low,
                mean_c,
                sigma_c,
            )
        )

        return max(
            0.0,
            min(
                1.0,
                probability,
            ),
        )

    # --------------------------------------------------------
    # OR LOWER
    #
    # Example:
    # <= 5°C
    # --------------------------------------------------------

    if bucket_type == "or_lower":

        threshold = safe_float(
            bucket_value
        )

        if threshold is None:
            return None

        probability = normal_cdf(
            threshold + 0.5,
            mean_c,
            sigma_c,
        )

        return max(
            0.0,
            min(
                1.0,
                probability,
            ),
        )

    # --------------------------------------------------------
    # OR HIGHER
    #
    # Example:
    # >= 30°C
    # --------------------------------------------------------

    if bucket_type == "or_higher":

        threshold = safe_float(
            bucket_value
        )

        if threshold is None:
            return None

        probability = (
            1.0
            -
            normal_cdf(
                threshold - 0.5,
                mean_c,
                sigma_c,
            )
        )

        return max(
            0.0,
            min(
                1.0,
                probability,
            ),
        )

    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

    if bucket_type == "range":

        low = safe_float(
            bucket_low
        )

        high = safe_float(
            bucket_high
        )

        if (
            low is None
            or
            high is None
        ):

            return None

        probability = (
            normal_cdf(
                high + 0.5,
                mean_c,
                sigma_c,
            )
            -
            normal_cdf(
                low - 0.5,
                mean_c,
                sigma_c,
            )
        )

        return max(
            0.0,
            min(
                1.0,
                probability,
            ),
        )

    return None


# ============================================================
# TEMPERATURE UNIT
# ============================================================

def fahrenheit_to_celsius(
    value,
):

    return (
        value - 32.0
    ) * 5.0 / 9.0


def normalize_bucket_to_celsius(
    market,
):

    normalized = dict(
        market
    )

    unit = (
        market.get(
            "temperature_unit"
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
            market.get(
                key
            )
        )

        if value is None:

            normalized[
                key
            ] = None

        else:

            normalized[
                key
            ] = (
                fahrenheit_to_celsius(
                    value
                )
            )

    return normalized


# ============================================================
# LATEST WEATHER RECORDS
# ============================================================

def select_latest_forecasts(
    rows,
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
            or
            not market_date
            or
            not model
        ):

            continue

        key = (
            station,
            market_date,
            model,
        )

        previous = latest.get(
            key
        )

        if (
            previous is None
            or
            row.get(
                "collected_at",
                "",
            )
            >
            previous.get(
                "collected_at",
                "",
            )
        ):

            latest[
                key
            ] = row

    return list(
        latest.values()
    )


def select_latest_observations(
    rows,
):

    latest = {}

    for row in rows:

        station = row.get(
            "station"
        )

        if not station:
            continue

        previous = latest.get(
            station
        )

        if (
            previous is None
            or
            row.get(
                "collected_at",
                "",
            )
            >
            previous.get(
                "collected_at",
                "",
            )
        ):

            latest[
                station
            ] = row

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

        if (
            row.get(
                "station"
            )
            != station
        ):

            continue

        if (
            row.get(
                "market_date"
            )
            != market_date
        ):

            continue

        if market_type == (
            "highest_temperature"
        ):

            value = safe_float(
                row.get(
                    "temperature_max_c"
                )
            )

        elif market_type == (
            "lowest_temperature"
        ):

            value = safe_float(
                row.get(
                    "temperature_min_c"
                )
            )

        else:

            value = safe_float(
                row.get(
                    "temperature_max_c"
                )
            )

        if value is not None:

            matching.append(
                (
                    row.get(
                        "model"
                    )
                    or "unknown",
                    value,
                )
            )

    if not matching:

        return None

    values = [
        item[1]
        for item in matching
    ]

    mean_c = (
        sum(values)
        / len(values)
    )

    if len(values) > 1:

        std_c = math.sqrt(
            sum(
                (
                    value
                    - mean_c
                ) ** 2
                for value in values
            )
            /
            (
                len(values)
                - 1
            )
        )

    else:

        std_c = 0.0

    #
    # We do not pretend the raw 3-model dispersion
    # is a calibrated forecast distribution.
    #
    # Instead we use a conservative floor and cap.
    #

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
            item[0]
            for item in matching
        ],

        "mean_c": mean_c,

        "min_c": min(
            values
        ),

        "max_c": max(
            values
        ),

        "std_c": std_c,

        "sigma_c": sigma_c,
    }


# ============================================================
# MARKET ENTRY / FEES
# ============================================================

def candidate_entry_price(
    market,
):

    ask = safe_float(
        market.get(
            "best_ask"
        )
    )

    yes_price = safe_float(
        market.get(
            "yes_price"
        )
    )

    # Prefer executable ask.
    if (
        ask is not None
        and
        0.0 < ask < 1.0
    ):

        return (
            ask,
            "ask",
        )

    # Fallback only for research.
    if (
        yes_price is not None
        and
        0.0 < yes_price < 1.0
    ):

        return (
            yes_price,
            "yes_price",
        )

    return (
        None,
        "none",
    )


def market_probability(
    market,
):

    return safe_float(
        market.get(
            "yes_price"
        )
    )


def get_fee_rate(
    market,
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
        or
        fee_rate <= 0
    ):

        return 0.0

    return (
        fee_rate
        *
        entry_price
        *
        (
            1.0
            -
            entry_price
        )
    )


# ============================================================
# FAMILY GROUPING
# ============================================================

def group_market_families(
    markets,
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
            []
        ).append(
            market
        )

    return groups


# ============================================================
# SIGNAL CREATION
# ============================================================

def create_signal(
    market,
    forecast_stats,
    observation,
):

    normalized = (
        normalize_bucket_to_celsius(
            market
        )
    )

    mean_c = (
        forecast_stats[
            "mean_c"
        ]
    )

    sigma_c = (
        forecast_stats[
            "sigma_c"
        ]
    )

    model_probability = (
        bucket_probability(
            normalized.get(
                "bucket_type"
            ),
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

    if (
        model_probability is None
        or
        entry_price is None
    ):

        return None

    fee_rate, fee_source = (
        get_fee_rate(
            market
        )
    )

    fee = fee_per_share(
        entry_price,
        fee_rate,
    )

    gross_edge = (
        model_probability
        -
        entry_price
    )

    net_ev = (
        gross_edge
        -
        fee
    )

    if entry_price > 0:

        net_return_if_win = (
            1.0
            -
            entry_price
            -
            fee
        ) / entry_price

    else:

        net_return_if_win = None

    if (
        gross_edge >= MIN_EDGE
        and
        net_ev >= MIN_EV
        and
        model_probability > entry_price
    ):

        signal = "PAPER_BUY"

        reason = (
            "model_probability="
            f"{model_probability:.4f}; "
            "entry="
            f"{entry_price:.4f}; "
            "gross_edge="
            f"{gross_edge:.4f}; "
            "fee="
            f"{fee:.5f}; "
            "net_ev="
            f"{net_ev:.4f}; "
            "entry_source="
            f"{entry_source}; "
            "fee_source="
            f"{fee_source}"
        )

    else:

        signal = "NO_TRADE"

        reason = (
            "gross_edge="
            f"{gross_edge:.4f}; "
            "net_ev="
            f"{net_ev:.4f}; "
            "entry_source="
            f"{entry_source}; "
            "fee_source="
            f"{fee_source}"
        )

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

        "bucket_type": market.get(
            "bucket_type"
        ),

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

        "model_probability": round(
            model_probability,
            6,
        ),

        "market_probability": (
            current_probability
        ),

        "entry_price": (
            entry_price
        ),

        "fee_rate": (
            fee_rate
        ),

        "fee_per_share": round(
            fee,
            8,
        ),

        "gross_edge": round(
            gross_edge,
            6,
        ),

        "net_ev_per_share": round(
            net_ev,
            6,
        ),

        "net_return_if_win": (
            round(
                net_return_if_win,
                6,
            )
            if
            net_return_if_win is not None
            else None
        ),

        "forecast_count": len(
            forecast_stats[
                "values"
            ]
        ),

        "forecast_mean_c": round(
            mean_c,
            4,
        ),

        "forecast_min_c": round(
            forecast_stats[
                "min_c"
            ],
            4,
        ),

        "forecast_max_c": round(
            forecast_stats[
                "max_c"
            ],
            4,
        ),

        "forecast_std_c": round(
            forecast_stats[
                "std_c"
            ],
            4,
        ),

        "observation_temperature_c": (
            observation_temperature
        ),

        "observation_time": (
            observation_time
        ),

        "signal": signal,

        "reason": reason,
    }


# ============================================================
# CONSOLE OUTPUT
# ============================================================

def print_signal(
    signal,
):

    print("")

    print(
        f"{signal['city']} | "
        f"{signal['station'] or 'NO-STATION'} | "
        f"{signal['market_date']}"
    )

    print(
        f"  {signal['group_title']}"
    )

    print(
        "  Model probability: "
        f"{signal['model_probability']:.2%}"
    )

    print(
        "  Market probability: "
        f"{signal['market_probability']:.2%}"
        if
        signal["market_probability"]
        is not None
        else
        "  Market probability: None"
    )

    print(
        "  Entry price: "
        f"{signal['entry_price']:.4f}"
    )

    print(
        "  Gross edge: "
        f"{signal['gross_edge']:.2%}"
    )

    print(
        "  Fee/share: "
        f"{signal['fee_per_share']:.5f}"
    )

    print(
        "  Net EV/share: "
        f"{signal['net_ev_per_share']:.4f}"
    )

    print(
        "  Forecast: "
        f"{signal['forecast_mean_c']:.2f} C"
        " | range "
        f"{signal['forecast_min_c']:.2f}–"
        f"{signal['forecast_max_c']:.2f} C"
    )

    print(
        "  Current observation: "
        f"{signal['observation_temperature_c']}"
        " C"
    )

    print(
        f"  SIGNAL: {signal['signal']}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    os.makedirs(
        EDGE_HISTORY_DIR,
        exist_ok=True,
    )

    print(
        "=" * 70
    )

    print(
        "POLYMARKET WEATHER EDGE ENGINE V1.0"
    )

    print(
        "RESEARCH / PAPER TRADING ONLY"
    )

    print(
        "=" * 70
    )

    print(
        f"UTC: {iso_now()}"
    )

    if not os.path.exists(
        ACTIVE_FILE
    ):

        raise FileNotFoundError(
            ACTIVE_FILE
        )

    if not os.path.exists(
        FORECAST_FILE
    ):

        raise FileNotFoundError(
            FORECAST_FILE
        )

    active_payload = read_json(
        ACTIVE_FILE
    )

    markets = (
        active_payload.get(
            "markets",
            []
        )
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

    # --------------------------------------------------------
    # EVALUATE FAMILY BY FAMILY
    # --------------------------------------------------------

    for (
        event_key,
        family,
    ) in families.items():

        if not family:
            continue

        first = family[0]

        station = (
            first.get(
                "resolution_station"
            )
        )

        market_date = (
            first.get(
                "market_date"
            )
        )

        market_type = (
            first.get(
                "market_type"
            )
        )

        #
        # Without an exact resolution station we do not
        # generate a weather signal.
        #
        if (
            not station
            or
            not market_date
        ):

            continue

        stats = (
            build_forecast_stats(
                forecast_rows,
                station,
                market_date,
                market_type,
            )
        )

        if not stats:
            continue

        observation = (
            observation_rows.get(
                station
            )
        )

        family_signals = []

        for market in family:

            signal = create_signal(
                market,
                stats,
                observation,
            )

            if signal:

                family_signals.append(
                    signal
                )

        #
        # Keep strongest 3 opportunities per family.
        #
        family_signals.sort(
            key=lambda item:
                item[
                    "net_ev_per_share"
                ],
            reverse=True,
        )

        for signal in (
            family_signals[:3]
        ):

            signals.append(
                signal
            )

            if (
                signal["signal"]
                ==
                "PAPER_BUY"
            ):

                paper_candidates.append(
                    signal
                )

    # --------------------------------------------------------
    # GLOBAL SORT
    # --------------------------------------------------------

    signals.sort(
        key=lambda item:
            item[
                "net_ev_per_share"
            ],
        reverse=True,
    )

    signals = signals[
        :MAX_SIGNALS_PER_RUN
    ]

    print("")

    print(
        "=" * 70
    )

    print(
        "TOP SIGNALS"
    )

    print(
        "=" * 70
    )

    for signal in signals[
        :20
    ]:

        print_signal(
            signal
        )

    # --------------------------------------------------------
    # LATEST JSON
    # --------------------------------------------------------

    paper_candidates.sort(
        key=lambda item:
            item[
                "net_ev_per_share"
            ],
        reverse=True,
    )

    payload = {
        "engine_version": (
            ENGINE_VERSION
        ),

        "run_at": (
            iso_now()
        ),

        "mode": (
            "research_paper"
        ),

        "status": (
            "NO_ORDERS_SENT"
        ),

        "parameters": {
            "min_edge": MIN_EDGE,

            "min_ev": MIN_EV,

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
        },

        "current_markets": (
            len(markets)
        ),

        "families": (
            len(families)
        ),

        "forecast_records": (
            len(forecast_rows)
        ),

        "observation_records": (
            len(observation_rows)
        ),

        "signals_evaluated": (
            len(signals)
        ),

        "paper_buy_candidates": (
            len(paper_candidates)
        ),

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
        "POLYMARKET WEATHER EDGE ENGINE V1.0",
        "RESEARCH / PAPER TRADING ONLY",
        "NO ORDERS SENT",
        f"UTC: {payload['run_at']}",
        "",
        f"Current markets: {len(markets)}",
        f"Families: {len(families)}",
        f"Signals evaluated: {len(signals)}",
        (
            "PAPER_BUY candidates: "
            f"{len(paper_candidates)}"
        ),
        "",
    ]

    for signal in paper_candidates[
        :20
    ]:

        report_lines.extend(
            [
                (
                    f"{signal['city']} | "
                    f"{signal['station']} | "
                    f"{signal['market_date']}"
                ),

                (
                    f"  "
                    f"{signal['group_title']}"
                ),

                (
                    "  model="
                    f"{signal['model_probability']:.4f} "
                    "entry="
                    f"{signal['entry_price']:.4f}"
                ),

                (
                    "  gross_edge="
                    f"{signal['gross_edge']:.4f} "
                    "fee="
                    f"{signal['fee_per_share']:.5f}"
                ),

                (
                    "  net_ev="
                    f"{signal['net_ev_per_share']:.4f}"
                ),

                (
                    "  signal="
                    f"{signal['signal']}"
                ),

                "",
            ]
        )

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as handle:

        handle.write(
            "\n".join(
                report_lines
            )
        )

    # --------------------------------------------------------
    # HISTORICAL SIGNAL LOG
    # --------------------------------------------------------

    append_csv(
        SIGNAL_HISTORY_FILE,
        SIGNAL_FIELDS,
        signals,
    )

    # --------------------------------------------------------
    # PAPER SIGNAL LOG
    #
    # These are signal observations, not executed trades.
    # --------------------------------------------------------

    if paper_candidates:

        append_csv(
            PAPER_SIGNAL_FILE,
            SIGNAL_FIELDS,
            paper_candidates,
        )

    # --------------------------------------------------------
    # FINAL STATUS
    # --------------------------------------------------------

    print("")

    print(
        "=" * 70
    )

    print(
        "EDGE ENGINE COMPLETE"
    )

    print(
        "=" * 70
    )

    print(
        f"Signals evaluated: "
        f"{len(signals)}"
    )

    print(
        f"PAPER_BUY candidates: "
        f"{len(paper_candidates)}"
    )

    print(
        f"Latest signals: "
        f"{LATEST_FILE}"
    )

    print(
        f"Report: "
        f"{REPORT_FILE}"
    )

    print(
        f"Signal history: "
        f"{SIGNAL_HISTORY_FILE}"
    )

    print(
        f"Paper signal log: "
        f"{PAPER_SIGNAL_FILE}"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":
    main()

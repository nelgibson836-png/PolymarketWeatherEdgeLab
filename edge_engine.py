import csv
import json
import math
import os
from datetime import datetime, timezone


ENGINE_VERSION = "1.1"

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
# PARAMETERS
# ============================================================

DEFAULT_WEATHER_FEE_RATE = 0.05

DEFAULT_SIGMA_C = 1.20
MIN_SIGMA_C = 0.90
MAX_SIGMA_C = 3.00

MIN_EDGE = 0.03
MIN_EV = 0.015

MAX_SIGNALS_PER_RUN = 100
MAX_PAPER_SIGNALS_PER_RUN = 1000


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
# HELPERS
# ============================================================

def iso_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def utc_date_string():
    return datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d")


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

    os.makedirs(
        directory,
        exist_ok=True,
    )

    temp_path = (
        path
        + ".tmp"
    )

    with open(
        temp_path,
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
        temp_path,
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

        writer.writerows(
            rows
        )


# ============================================================
# PROBABILITY
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

    return (
        0.5
        * (
            1.0
            + math.erf(z)
        )
    )


def probability_exact(
    value,
    mean_c,
    sigma_c,
    unit,
):

    if unit == "F":

        center_c = (
            value - 32.0
        ) * 5.0 / 9.0

        half_width_c = (
            0.5
            * 5.0
            / 9.0
        )

    else:

        center_c = value

        half_width_c = 0.5

    probability = (
        normal_cdf(
            center_c
            + half_width_c,
            mean_c,
            sigma_c,
        )
        -
        normal_cdf(
            center_c
            - half_width_c,
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


def probability_lower(
    value,
    mean_c,
    sigma_c,
    unit,
):

    if unit == "F":

        threshold_c = (
            value - 32.0
        ) * 5.0 / 9.0

        half_unit_c = (
            0.5
            * 5.0
            / 9.0
        )

    else:

        threshold_c = value
        half_unit_c = 0.5

    probability = normal_cdf(
        threshold_c
        + half_unit_c,
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


def probability_higher(
    value,
    mean_c,
    sigma_c,
    unit,
):

    if unit == "F":

        threshold_c = (
            value - 32.0
        ) * 5.0 / 9.0

        half_unit_c = (
            0.5
            * 5.0
            / 9.0
        )

    else:

        threshold_c = value
        half_unit_c = 0.5

    probability = (
        1.0
        -
        normal_cdf(
            threshold_c
            - half_unit_c,
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


def probability_range(
    low,
    high,
    mean_c,
    sigma_c,
    unit,
):

    if unit == "F":

        low_c = (
            low - 32.0
        ) * 5.0 / 9.0

        high_c = (
            high - 32.0
        ) * 5.0 / 9.0

        half_unit_c = (
            0.5
            * 5.0
            / 9.0
        )

    else:

        low_c = low
        high_c = high
        half_unit_c = 0.5

    probability = (
        normal_cdf(
            high_c
            + half_unit_c,
            mean_c,
            sigma_c,
        )
        -
        normal_cdf(
            low_c
            - half_unit_c,
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


def bucket_probability(
    market,
    mean_c,
    sigma_c,
):

    bucket_type = (
        market.get(
            "bucket_type"
        )
    )

    unit = (
        market.get(
            "temperature_unit"
        )
        or "C"
    ).upper()

    if bucket_type == "exact":

        value = safe_float(
            market.get(
                "bucket_value"
            )
        )

        if value is None:
            return None

        return probability_exact(
            value,
            mean_c,
            sigma_c,
            unit,
        )

    if bucket_type == "or_lower":

        value = safe_float(
            market.get(
                "bucket_value"
            )
        )

        if value is None:
            return None

        return probability_lower(
            value,
            mean_c,
            sigma_c,
            unit,
        )

    if bucket_type == "or_higher":

        value = safe_float(
            market.get(
                "bucket_value"
            )
        )

        if value is None:
            return None

        return probability_higher(
            value,
            mean_c,
            sigma_c,
            unit,
        )

    if bucket_type == "range":

        low = safe_float(
            market.get(
                "bucket_low"
            )
        )

        high = safe_float(
            market.get(
                "bucket_high"
            )
        )

        if (
            low is None
            or
            high is None
        ):

            return None

        return probability_range(
            low,
            high,
            mean_c,
            sigma_c,
            unit,
        )

    return None


# ============================================================
# LATEST WEATHER
# ============================================================

def latest_forecasts(
    rows,
):

    latest = {}

    for row in rows:

        key = (
            row.get("station"),
            row.get("market_date"),
            row.get("model"),
        )

        if None in key:
            continue

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


def latest_observations(
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

def forecast_stats(
    forecasts,
    station,
    market_date,
    market_type,
):

    values = []
    models = []

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

            field = (
                "temperature_max_c"
            )

        elif market_type == (
            "lowest_temperature"
        ):

            field = (
                "temperature_min_c"
            )

        else:

            field = (
                "temperature_max_c"
            )

        value = safe_float(
            row.get(
                field
            )
        )

        if value is not None:

            values.append(
                value
            )

            models.append(
                row.get(
                    "model"
                )
                or "unknown"
            )

    if not values:
        return None

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
        "models": models,
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
# MARKET PRICING / FEES
# ============================================================

def entry_price(
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

    if (
        ask is not None
        and
        0.0 < ask < 1.0
    ):

        return (
            ask,
            "ask",
        )

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
    price,
    rate,
):

    if (
        price is None
        or
        rate <= 0
    ):

        return 0.0

    return (
        rate
        * price
        * (
            1.0
            - price
        )
    )


# ============================================================
# SIGNAL
# ============================================================

def evaluate_market(
    market,
    stats,
    observation,
):

    model_probability = (
        bucket_probability(
            market,
            stats[
                "mean_c"
            ],
            stats[
                "sigma_c"
            ],
        )
    )

    price, entry_source = (
        entry_price(
            market
        )
    )

    if (
        model_probability is None
        or
        price is None
    ):

        return None

    fee_rate, fee_source = (
        get_fee_rate(
            market
        )
    )

    fee = fee_per_share(
        price,
        fee_rate,
    )

    gross_edge = (
        model_probability
        -
        price
    )

    net_ev = (
        gross_edge
        -
        fee
    )

    if price > 0:

        net_return_if_win = (
            (
                1.0
                -
                price
                -
                fee
            )
            /
            price
        )

    else:

        net_return_if_win = None

    if (
        gross_edge >= MIN_EDGE
        and
        net_ev >= MIN_EV
        and
        model_probability > price
    ):

        signal = (
            "PAPER_BUY"
        )

    else:

        signal = (
            "NO_TRADE"
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

        "market_probability": safe_float(
            market.get(
                "yes_price"
            )
        ),

        "entry_price": price,

        "fee_rate": fee_rate,

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
            net_return_if_win
            is not None
            else None
        ),

        "forecast_count": len(
            stats[
                "values"
            ]
        ),

        "forecast_mean_c": round(
            stats[
                "mean_c"
            ],
            4,
        ),

        "forecast_min_c": round(
            stats[
                "min_c"
            ],
            4,
        ),

        "forecast_max_c": round(
            stats[
                "max_c"
            ],
            4,
        ),

        "forecast_std_c": round(
            stats[
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

        "reason": (
            f"entry={entry_source}; "
            f"fee_source={fee_source}; "
            f"gross_edge={gross_edge:.4f}; "
            f"net_ev={net_ev:.4f}"
        ),
    }


# ============================================================
# CONSOLE
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

    if (
        signal[
            "market_probability"
        ]
        is not None
    ):

        print(
            "  Market probability: "
            f"{signal['market_probability']:.2%}"
        )

    else:

        print(
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
        "  Forecast mean: "
        f"{signal['forecast_mean_c']:.2f} C"
        " | range "
        f"{signal['forecast_min_c']:.2f}-"
        f"{signal['forecast_max_c']:.2f} C"
    )

    print(
        "  Observation: "
        f"{signal['observation_temperature_c']} C"
    )

    print(
        f"  SIGNAL: "
        f"{signal['signal']}"
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
        "POLYMARKET WEATHER EDGE ENGINE V1.1"
    )

    print(
        "RESEARCH / PAPER TRADING ONLY"
    )

    print(
        "NO ORDERS SENT"
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

    forecasts = (
        latest_forecasts(
            read_csv(
                FORECAST_FILE
            )
        )
    )

    observations = (
        latest_observations(
            read_csv(
                OBSERVATION_FILE
            )
        )
    )

    families = {}

    for market in markets:

        key = market.get(
            "event_key"
        )

        if key:

            families.setdefault(
                key,
                [],
            ).append(
                market
            )

    print(
        f"Current markets: "
        f"{len(markets)}"
    )

    print(
        f"Forecast records: "
        f"{len(forecasts)}"
    )

    print(
        f"Observation records: "
        f"{len(observations)}"
    )

    print(
        f"Families: "
        f"{len(families)}"
    )

    all_signals = []

    paper_signals = []

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

        if (
            not station
            or
            not market_date
        ):

            continue

        stats = forecast_stats(
            forecasts,
            station,
            market_date,
            market_type,
        )

        if not stats:

            continue

        observation = (
            observations.get(
                station
            )
        )

        family_signals = []

        for market in family:

            signal = (
                evaluate_market(
                    market,
                    stats,
                    observation,
                )
            )

            if signal:

                family_signals.append(
                    signal
                )

        family_signals.sort(
            key=lambda item:
                item[
                    "net_ev_per_share"
                ],
            reverse=True,
        )

        all_signals.extend(
            family_signals
        )

        family_paper = [
            signal
            for signal
            in family_signals
            if signal[
                "signal"
            ]
            ==
            "PAPER_BUY"
        ]

        paper_signals.extend(
            family_paper[:5]
        )

    all_signals.sort(
        key=lambda item:
            item[
                "net_ev_per_share"
            ],
        reverse=True,
    )

    paper_signals.sort(
        key=lambda item:
            item[
                "net_ev_per_share"
            ],
        reverse=True,
    )

    top_signals = (
        all_signals[
            :MAX_SIGNALS_PER_RUN
        ]
    )

    paper_signals = (
        paper_signals[
            :MAX_PAPER_SIGNALS_PER_RUN
        ]
    )

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

    for signal in top_signals[:20]:

        print_signal(
            signal
        )

    paper_count = sum(
        1
        for signal
        in all_signals
        if signal[
            "signal"
        ]
        ==
        "PAPER_BUY"
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
            len(forecasts)
        ),

        "observation_records": (
            len(observations)
        ),

        "signals_evaluated": (
            len(all_signals)
        ),

        "signals_with_edge": (
            paper_count
        ),

        "signals_saved_top": (
            len(top_signals)
        ),

        "signals": top_signals,
    }

    write_json(
        LATEST_FILE,
        payload,
    )

    append_csv(
        SIGNAL_HISTORY_FILE,
        SIGNAL_FIELDS,
        all_signals,
    )

    append_csv(
        PAPER_SIGNAL_FILE,
        SIGNAL_FIELDS,
        paper_signals,
    )

    report_lines = [
        "POLYMARKET WEATHER EDGE ENGINE V1.1",
        "RESEARCH / PAPER TRADING ONLY",
        "NO ORDERS SENT",
        "",
        f"UTC: {payload['run_at']}",
        f"Current markets: {len(markets)}",
        f"Families: {len(families)}",
        f"Signals evaluated: {len(all_signals)}",
        f"PAPER_BUY candidates: {paper_count}",
        "",
    ]

    for signal in paper_signals[:50]:

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
        f"{len(all_signals)}"
    )

    print(
        f"PAPER_BUY candidates: "
        f"{paper_count}"
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

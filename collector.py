import csv
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone


GAMMA_API = "https://gamma-api.polymarket.com"

PAGE_SIZE = 100

DATA_DIR = "data"
SNAPSHOT_DIR = os.path.join(DATA_DIR, "snapshots")

LATEST_JSON = os.path.join(
    DATA_DIR,
    "temperature_markets_latest.json"
)

HISTORY_CSV = os.path.join(
    DATA_DIR,
    "temperature_markets.csv"
)


def get_json(url):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent":
                "PolymarketWeatherEdgeLab/6.0",
            "Accept":
                "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


def parse_json_field(value):

    if value is None:
        return None

    if isinstance(value, (list, dict)):
        return value

    if isinstance(value, str):

        try:
            return json.loads(value)

        except Exception:
            return value

    return value


def get_weather_tag():

    return get_json(
        f"{GAMMA_API}/tags/slug/weather"
    )


def get_weather_events():

    events = []

    offset = 0

    while True:

        params = {
            "tag_slug": "weather",
            "active": "true",
            "closed": "false",
            "limit": PAGE_SIZE,
            "offset": offset,
        }

        url = (
            f"{GAMMA_API}/events?"
            f"{urllib.parse.urlencode(params)}"
        )

        page = get_json(url)

        if not page:
            break

        events.extend(page)

        print(
            f"  Eventos descargados: "
            f"{len(events)}",
            end="\r"
        )

        if len(page) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    print()

    return events


def is_temperature_market(market):

    question = (
        market.get("question")
        or ""
    ).lower()

    return (
        "highest temperature" in question
        or
        "lowest temperature" in question
    )


def extract_city(text):

    if not text:
        return None

    patterns = [

        r"temperature in ([^?]+?) on "
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)",

        r"temperature in ([^?]+?) on "
        r"\w+ \d+",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            return (
                match.group(1)
                .strip()
                .rstrip(" ,")
            )

    return None


def extract_market_date(text):

    if not text:
        return None

    match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2}",
        text,
        re.IGNORECASE
    )

    if match:

        return match.group(0)

    return None


def extract_temperature(text):

    if not text:
        return None

    match = re.search(
        r"(-?\d+(?:\.\d+)?)"
        r"\s*°([CF])",
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    return {
        "value":
            float(match.group(1)),

        "unit":
            match.group(2).upper()
    }


def extract_token_ids(market):

    value = market.get(
        "clobTokenIds"
    )

    parsed = parse_json_field(value)

    if isinstance(parsed, list):

        return parsed

    return []


def extract_outcomes(market):

    return parse_json_field(
        market.get("outcomes")
    )


def extract_prices(market):

    return parse_json_field(
        market.get("outcomePrices")
    )


def safe_float(value):

    if value is None:
        return None

    try:

        return float(value)

    except Exception:

        return None


def safe_int(value):

    if value is None:
        return None

    try:

        return int(value)

    except Exception:

        return None


def normalize_market(
    event,
    market,
    collected_at
):

    question = (
        market.get("question")
        or ""
    )

    group_title = (
        market.get("groupItemTitle")
        or ""
    )

    combined_text = (
        question
        + " "
        + group_title
    )

    question_lower = question.lower()

    if "highest temperature" in question_lower:

        temperature_type = (
            "highest_temperature"
        )

    elif "lowest temperature" in question_lower:

        temperature_type = (
            "lowest_temperature"
        )

    else:

        temperature_type = "unknown"

    temperature = extract_temperature(
        combined_text
    )

    outcomes = extract_outcomes(
        market
    )

    prices = extract_prices(
        market
    )

    token_ids = extract_token_ids(
        market
    )

    yes_price = None
    no_price = None

    if (
        isinstance(outcomes, list)
        and
        isinstance(prices, list)
    ):

        for index, outcome in enumerate(
            outcomes
        ):

            if index >= len(prices):
                continue

            price = safe_float(
                prices[index]
            )

            if (
                str(outcome).lower()
                == "yes"
            ):

                yes_price = price

            elif (
                str(outcome).lower()
                == "no"
            ):

                no_price = price

    yes_token_id = None
    no_token_id = None

    if (
        isinstance(outcomes, list)
        and
        isinstance(token_ids, list)
    ):

        for index, outcome in enumerate(
            outcomes
        ):

            if index >= len(token_ids):
                continue

            token = token_ids[index]

            if (
                str(outcome).lower()
                == "yes"
            ):

                yes_token_id = token

            elif (
                str(outcome).lower()
                == "no"
            ):

                no_token_id = token

    fee_schedule = parse_json_field(
        market.get("feeSchedule")
    )

    return {

        "collected_at":
            collected_at,

        "event_id":
            event.get("id"),

        "event_title":
            event.get("title"),

        "event_slug":
            event.get("slug"),

        "market_id":
            market.get("id"),

        "market_slug":
            market.get("slug"),

        "condition_id":
            market.get("conditionId"),

        "question_id":
            market.get("questionID"),

        "question":
            question,

        "city":
            extract_city(question),

        "market_date":
            extract_market_date(question),

        "temperature_type":
            temperature_type,

        "temperature":
            (
                temperature["value"]
                if temperature
                else None
            ),

        "temperature_unit":
            (
                temperature["unit"]
                if temperature
                else None
            ),

        "group_item_title":
            group_title,

        "group_item_threshold":
            market.get(
                "groupItemThreshold"
            ),

        "lower_bound":
            market.get("lowerBound"),

        "upper_bound":
            market.get("upperBound"),

        "outcomes":
            outcomes,

        "outcome_prices":
            prices,

        "yes_price":
            yes_price,

        "no_price":
            no_price,

        "clob_token_ids":
            token_ids,

        "yes_token_id":
            yes_token_id,

        "no_token_id":
            no_token_id,

        "best_bid":
            safe_float(
                market.get("bestBid")
            ),

        "best_ask":
            safe_float(
                market.get("bestAsk")
            ),

        "spread":
            safe_float(
                market.get("spread")
            ),

        "one_hour_price_change":
            safe_float(
                market.get(
                    "oneHourPriceChange"
                )
            ),

        "volume":
            safe_float(
                market.get("volume")
            ),

        "volume_24hr":
            safe_float(
                market.get("volume24hr")
            ),

        "volume_1wk":
            safe_float(
                market.get("volume1wk")
            ),

        "volume_1mo":
            safe_float(
                market.get("volume1mo")
            ),

        "volume_1yr":
            safe_float(
                market.get("volume1yr")
            ),

        "volume_clob":
            safe_float(
                market.get("volumeClob")
            ),

        "volume_24hr_clob":
            safe_float(
                market.get(
                    "volume24hrClob"
                )
            ),

        "liquidity":
            safe_float(
                market.get("liquidity")
            ),

        "liquidity_clob":
            safe_float(
                market.get(
                    "liquidityClob"
                )
            ),

        "order_min_size":
            safe_float(
                market.get(
                    "orderMinSize"
                )
            ),

        "order_price_min_tick_size":
            safe_float(
                market.get(
                    "orderPriceMinTickSize"
                )
            ),

        "maker_base_fee":
            safe_int(
                market.get(
                    "makerBaseFee"
                )
            ),

        "taker_base_fee":
            safe_int(
                market.get(
                    "takerBaseFee"
                )
            ),

        "fee_type":
            market.get("feeType"),

        "fee_schedule":
            fee_schedule,

        "active":
            market.get("active"),

        "closed":
            market.get("closed"),

        "accepting_orders":
            market.get(
                "acceptingOrders"
            ),

        "enable_order_book":
            market.get(
                "enableOrderBook"
            ),

        "neg_risk":
            market.get("negRisk"),

        "resolution_source":
            market.get(
                "resolutionSource"
            ),

        "start_date":
            market.get("startDate"),

        "end_date":
            market.get("endDate"),

        "updated_at":
            market.get("updatedAt"),

        "created_at":
            market.get("createdAt"),

        "seconds_delay":
            safe_int(
                market.get(
                    "secondsDelay"
                )
            ),

        "rewards_min_size":
            safe_float(
                market.get(
                    "rewardsMinSize"
                )
            ),

        "rewards_max_spread":
            safe_float(
                market.get(
                    "rewardsMaxSpread"
                )
            ),

        "competitive":
            safe_float(
                market.get(
                    "competitive"
                )
            ),
    }


def save_latest(data):

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    with open(
        LATEST_JSON,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


def save_snapshot(
    data,
    now
):

    date_dir = os.path.join(
        SNAPSHOT_DIR,
        now.strftime(
            "%Y-%m-%d"
        )
    )

    os.makedirs(
        date_dir,
        exist_ok=True
    )

    filename = (
        now.strftime("%H%M%S")
        + ".json"
    )

    filepath = os.path.join(
        date_dir,
        filename
    )

    with open(
        filepath,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    return filepath


CSV_FIELDS = [

    "collected_at",

    "event_id",
    "event_title",
    "event_slug",

    "market_id",
    "market_slug",
    "condition_id",
    "question_id",

    "question",

    "city",
    "market_date",

    "temperature_type",
    "temperature",
    "temperature_unit",

    "group_item_title",
    "group_item_threshold",

    "lower_bound",
    "upper_bound",

    "outcomes",
    "outcome_prices",

    "yes_price",
    "no_price",

    "clob_token_ids",
    "yes_token_id",
    "no_token_id",

    "best_bid",
    "best_ask",
    "spread",

    "one_hour_price_change",

    "volume",
    "volume_24hr",
    "volume_1wk",
    "volume_1mo",
    "volume_1yr",

    "volume_clob",
    "volume_24hr_clob",

    "liquidity",
    "liquidity_clob",

    "order_min_size",
    "order_price_min_tick_size",

    "maker_base_fee",
    "taker_base_fee",

    "fee_type",
    "fee_schedule",

    "active",
    "closed",
    "accepting_orders",
    "enable_order_book",

    "neg_risk",

    "resolution_source",

    "start_date",
    "end_date",

    "updated_at",
    "created_at",

    "seconds_delay",

    "rewards_min_size",
    "rewards_max_spread",

    "competitive",
]


def save_csv(markets):

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    exists = os.path.exists(
        HISTORY_CSV
    )

    with open(
        HISTORY_CSV,
        "a",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=CSV_FIELDS,
            extrasaction="ignore"
        )

        if not exists:
            writer.writeheader()

        for market in markets:

            row = market.copy()

            for field in [
                "outcomes",
                "outcome_prices",
                "clob_token_ids",
                "fee_schedule",
            ]:

                row[field] = json.dumps(
                    row[field],
                    ensure_ascii=False
                )

            writer.writerow(row)


def main():

    now = datetime.now(
        timezone.utc
    )

    collected_at = now.isoformat()

    print("=" * 60)
    print(
        "Polymarket Weather Edge Lab"
    )
    print(
        "Temperature Collector v6.0"
    )
    print("=" * 60)

    print()

    print(
        f"UTC: {collected_at}"
    )

    print()

    weather_tag = get_weather_tag()

    print(
        "Weather tag: "
        f"{weather_tag.get('id')} "
        f"({weather_tag.get('slug')})"
    )

    print()

    print(
        "Consultando eventos Weather..."
    )

    events = get_weather_events()

    print(
        f"Weather events: {len(events)}"
    )

    markets = []

    seen = set()

    for event in events:

        event_markets = (
            event.get("markets")
            or []
        )

        for market in event_markets:

            market_id = market.get(
                "id"
            )

            if not market_id:
                continue

            if market_id in seen:
                continue

            seen.add(market_id)

            if not is_temperature_market(
                market
            ):
                continue

            markets.append(
                normalize_market(
                    event,
                    market,
                    collected_at
                )
            )

    data = {

        "collector_version":
            "6.0",

        "collected_at":
            collected_at,

        "weather_tag":
            weather_tag,

        "events_analyzed":
            len(events),

        "temperature_markets":
            len(markets),

        "cities":
            len({
                m["city"]
                for m in markets
                if m["city"]
            }),

        "markets":
            markets,
    }

    save_latest(data)

    snapshot = save_snapshot(
        data,
        now
    )

    save_csv(markets)

    cities = sorted({
        m["city"]
        for m in markets
        if m["city"]
    })

    with_prices = sum(
        1
        for m in markets
        if (
            m["yes_price"] is not None
            and
            m["no_price"] is not None
        )
    )

    with_tokens = sum(
        1
        for m in markets
        if (
            m["yes_token_id"]
            and
            m["no_token_id"]
        )
    )

    with_orderbook = sum(
        1
        for m in markets
        if (
            m["best_bid"] is not None
            and
            m["best_ask"] is not None
        )
    )

    print()

    print("=" * 60)
    print("COLLECTION COMPLETE")
    print("=" * 60)

    print(
        f"Events: {len(events)}"
    )

    print(
        f"Temperature markets: "
        f"{len(markets)}"
    )

    print(
        f"Cities: {len(cities)}"
    )

    print(
        f"Markets with prices: "
        f"{with_prices}"
    )

    print(
        f"Markets with YES/NO tokens: "
        f"{with_tokens}"
    )

    print(
        f"Markets with bid/ask: "
        f"{with_orderbook}"
    )

    print()

    print(
        f"Latest: {LATEST_JSON}"
    )

    print(
        f"Snapshot: {snapshot}"
    )

    print(
        f"History: {HISTORY_CSV}"
    )

    print()

    print("=" * 60)
    print("VALIDATION")
    print("=" * 60)

    for market in markets[:10]:

        print()

        print(
            f"{market['city']} | "
            f"{market['market_date']} | "
            f"{market['group_item_title']}"
        )

        print(
            f"  YES: {market['yes_price']} | "
            f"NO: {market['no_price']}"
        )

        print(
            f"  Bid: {market['best_bid']} | "
            f"Ask: {market['best_ask']} | "
            f"Spread: {market['spread']}"
        )

        print(
            f"  Volume: {market['volume']} | "
            f"Liquidity: {market['liquidity']}"
        )

        print(
            f"  YES token: "
            f"{str(market['yes_token_id'])[:25]}..."
        )

        print(
            f"  NO token: "
            f"{str(market['no_token_id'])[:25]}..."
        )


if __name__ == "__main__":
    main()

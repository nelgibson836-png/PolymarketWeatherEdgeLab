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

LATEST_JSON = os.path.join(DATA_DIR, "temperature_markets_latest.json")
HISTORY_CSV = os.path.join(DATA_DIR, "temperature_markets.csv")


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PolymarketWeatherEdgeLab/3.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_weather_tag():
    return get_json(f"{GAMMA_API}/tags/slug/weather")


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

        url = f"{GAMMA_API}/events?{urllib.parse.urlencode(params)}"

        page = get_json(url)

        if not page:
            break

        events.extend(page)

        if len(page) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    return events


def is_temperature_market(market):
    question = (market.get("question") or "").lower()

    return (
        "highest temperature" in question
        or "lowest temperature" in question
    )


def extract_city(question):
    patterns = [
        r"in (.+?) on [A-Z][a-z]+ \d+",
        r"in (.+?) on \w+ \d+",
    ]

    for pattern in patterns:
        match = re.search(pattern, question)

        if match:
            return match.group(1).strip()

    return None


def extract_date(text):
    match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2}",
        text,
        re.IGNORECASE,
    )

    if match:
        return match.group(0)

    return None


def extract_temperature(text):
    match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*°[CF]",
        text,
        re.IGNORECASE,
    )

    if match:
        return float(match.group(1))

    return None


def extract_unit(text):
    match = re.search(
        r"°([CF])",
        text,
        re.IGNORECASE,
    )

    if match:
        return match.group(1).upper()

    return None


def normalize_market(event, market, collected_at):

    question = market.get("question") or ""
    group_title = market.get("groupItemTitle") or ""

    question_lower = question.lower()

    if "highest temperature" in question_lower:
        temperature_type = "highest_temperature"
    elif "lowest temperature" in question_lower:
        temperature_type = "lowest_temperature"
    else:
        temperature_type = "unknown"

    return {
        "collected_at": collected_at,

        "event_id": event.get("id"),
        "event_title": event.get("title"),
        "event_slug": event.get("slug"),

        "market_id": market.get("id"),
        "market_slug": market.get("slug"),

        "question": question,

        "city": extract_city(event.get("title") or question),

        "temperature_type": temperature_type,

        "market_date": extract_date(
            event.get("title") or question
        ),

        "temperature": extract_temperature(
            question + " " + group_title
        ),

        "unit": extract_unit(
            question + " " + group_title
        ),

        "group_item_title": group_title,

        "group_item_threshold": market.get(
            "groupItemThreshold"
        ),

        "lower_bound": market.get("lowerBound"),

        "upper_bound": market.get("upperBound"),

        "outcomes": market.get("outcomes"),

        "outcome_prices": market.get(
            "outcomePrices"
        ),

        "volume": market.get("volume"),

        "liquidity": market.get("liquidity"),

        "start_date": market.get("startDate"),

        "end_date": market.get("endDate"),

        "resolution_source": market.get(
            "resolutionSource"
        ),
    }


def save_json(data):
    os.makedirs(DATA_DIR, exist_ok=True)

    with open(
        LATEST_JSON,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


def save_snapshot(data, now):
    date_dir = os.path.join(
        SNAPSHOT_DIR,
        now.strftime("%Y-%m-%d"),
    )

    os.makedirs(date_dir, exist_ok=True)

    filename = now.strftime("%H%M%S") + ".json"

    filepath = os.path.join(
        date_dir,
        filename,
    )

    with open(
        filepath,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

    return filepath


def save_csv(markets):
    os.makedirs(DATA_DIR, exist_ok=True)

    fieldnames = [
        "collected_at",
        "event_id",
        "event_title",
        "event_slug",
        "market_id",
        "market_slug",
        "question",
        "city",
        "temperature_type",
        "market_date",
        "temperature",
        "unit",
        "group_item_title",
        "group_item_threshold",
        "lower_bound",
        "upper_bound",
        "outcomes",
        "outcome_prices",
        "volume",
        "liquidity",
        "start_date",
        "end_date",
        "resolution_source",
    ]

    existing = os.path.exists(HISTORY_CSV)

    with open(
        HISTORY_CSV,
        "a",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        if not existing:
            writer.writeheader()

        for market in markets:

            row = market.copy()

            row["outcomes"] = json.dumps(
                row["outcomes"],
                ensure_ascii=False,
            )

            row["outcome_prices"] = json.dumps(
                row["outcome_prices"],
                ensure_ascii=False,
            )

            writer.writerow(row)


def main():

    now = datetime.now(timezone.utc)

    collected_at = now.isoformat()

    print("=" * 60)
    print("Polymarket Weather Edge Lab")
    print("Temperature Collector v3.0")
    print("=" * 60)

    print(f"UTC: {collected_at}")
    print()

    try:

        weather_tag = get_weather_tag()

        print(
            f"Weather tag: "
            f"{weather_tag.get('id')} "
            f"({weather_tag.get('slug')})"
        )

        events = get_weather_events()

        print(
            f"Weather events: {len(events)}"
        )

        markets = []

        seen = set()

        for event in events:

            for market in event.get("markets") or []:

                market_id = market.get("id")

                if not market_id:
                    continue

                if market_id in seen:
                    continue

                seen.add(market_id)

                if not is_temperature_market(market):
                    continue

                markets.append(
                    normalize_market(
                        event,
                        market,
                        collected_at,
                    )
                )

        data = {
            "collector_version": "3.0",
            "collected_at": collected_at,
            "weather_tag": weather_tag,
            "events_analyzed": len(events),
            "temperature_markets": len(markets),
            "markets": markets,
        }

        save_json(data)

        snapshot = save_snapshot(
            data,
            now,
        )

        save_csv(markets)

        cities = sorted(
            {
                m["city"]
                for m in markets
                if m["city"]
            }
        )

        print()
        print("=" * 60)
        print("COLLECTION COMPLETE")
        print("=" * 60)

        print(
            f"Temperature markets: {len(markets)}"
        )

        print(
            f"Cities detected: {len(cities)}"
        )

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
        print("Primeros mercados:")

        for market in markets[:10]:

            print(
                f"  {market['city']} | "
                f"{market['temperature_type']} | "
                f"{market['group_item_title']} | "
                f"{market['outcome_prices']}"
            )

    except Exception as error:

        print()
        print("=" * 60)
        print("ERROR")
        print("=" * 60)

        print(
            f"{type(error).__name__}: {error}"
        )

        raise


if __name__ == "__main__":
    main()

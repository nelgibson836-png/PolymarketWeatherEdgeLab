import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


GAMMA_API = "https://gamma-api.polymarket.com"

PAGE_SIZE = 100

TEMPERATURE_KEYWORDS = (
    "highest temperature",
    "lowest temperature",
)


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PolymarketWeatherEdgeLab/2.0",
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

        print(
            f"  Página: offset={offset} | "
            f"eventos={len(page)} | acumulado={len(events)}"
        )

        if len(page) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    return events


def is_temperature_market(market):
    question = (market.get("question") or "").lower()

    return any(
        keyword in question
        for keyword in TEMPERATURE_KEYWORDS
    )


def parse_market(event, market):
    question = market.get("question") or ""

    question_lower = question.lower()

    if "highest temperature" in question_lower:
        temperature_type = "highest_temperature"
    elif "lowest temperature" in question_lower:
        temperature_type = "lowest_temperature"
    else:
        temperature_type = "unknown"

    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),

        "event_id": event.get("id"),
        "event_title": event.get("title"),
        "event_slug": event.get("slug"),

        "market_id": market.get("id"),
        "market_slug": market.get("slug"),
        "question": question,

        "temperature_type": temperature_type,

        "start_date": market.get("startDate"),
        "end_date": market.get("endDate"),

        "resolution_source": market.get("resolutionSource"),

        "outcomes": market.get("outcomes"),
        "outcome_prices": market.get("outcomePrices"),

        "group_item_title": market.get("groupItemTitle"),
        "group_item_threshold": market.get("groupItemThreshold"),

        "lower_bound": market.get("lowerBound"),
        "upper_bound": market.get("upperBound"),

        "volume": market.get("volume"),
        "liquidity": market.get("liquidity"),
    }


def main():

    print("=" * 64)
    print("Polymarket Weather Edge Lab")
    print("Temperature Market Collector v2.0")
    print("=" * 64)

    now = datetime.now(timezone.utc)

    print(f"UTC: {now.isoformat()}")
    print()

    try:

        print("1. Consultando tag Weather...")

        weather_tag = get_weather_tag()

        print(
            f"  ID: {weather_tag.get('id')} | "
            f"Slug: {weather_tag.get('slug')}"
        )

        print()

        print("2. Descubriendo eventos Weather...")

        events = get_weather_events()

        print()
        print(f"Eventos Weather totales: {len(events)}")
        print()

        temperature_markets = []

        seen_market_ids = set()

        for event in events:

            markets = event.get("markets") or []

            for market in markets:

                market_id = market.get("id")

                if not market_id:
                    continue

                if market_id in seen_market_ids:
                    continue

                seen_market_ids.add(market_id)

                if not is_temperature_market(market):
                    continue

                temperature_markets.append(
                    parse_market(event, market)
                )

        print("=" * 64)
        print("RESULTADO")
        print("=" * 64)

        print(
            f"Eventos analizados: {len(events)}"
        )

        print(
            f"Mercados totales examinados: "
            f"{len(seen_market_ids)}"
        )

        print(
            f"Mercados de temperatura: "
            f"{len(temperature_markets)}"
        )

        print()

        if not temperature_markets:
            print("No se encontraron mercados de temperatura.")
            return

        highest = sum(
            1
            for m in temperature_markets
            if m["temperature_type"] == "highest_temperature"
        )

        lowest = sum(
            1
            for m in temperature_markets
            if m["temperature_type"] == "lowest_temperature"
        )

        print(f"Highest temperature: {highest}")
        print(f"Lowest temperature:  {lowest}")
        print()

        print("=" * 64)
        print("MERCADOS ENCONTRADOS")
        print("=" * 64)

        for market in temperature_markets:

            print()
            print(f"Market ID: {market['market_id']}")
            print(f"Evento: {market['event_title']}")
            print(f"Pregunta: {market['question']}")
            print(f"Tipo: {market['temperature_type']}")
            print(f"Fin: {market['end_date']}")
            print(f"Outcomes: {market['outcomes']}")
            print(f"Prices: {market['outcome_prices']}")
            print(f"Group title: {market['group_item_title']}")
            print(f"Threshold: {market['group_item_threshold']}")
            print(f"Volume: {market['volume']}")
            print(f"Liquidity: {market['liquidity']}")

        output = {
            "collector_version": "2.0",
            "collected_at": now.isoformat(),
            "weather_tag": weather_tag,
            "events_analyzed": len(events),
            "markets_found": len(temperature_markets),
            "markets": temperature_markets,
        }

        with open(
            "temperature_markets.json",
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                output,
                file,
                ensure_ascii=False,
                indent=2,
            )

        print()
        print("=" * 64)
        print("Archivo generado:")
        print("temperature_markets.json")
        print("=" * 64)

    except Exception as error:

        print()
        print("=" * 64)
        print("ERROR")
        print("=" * 64)

        print(
            f"{type(error).__name__}: {error}"
        )


if __name__ == "__main__":
    main()

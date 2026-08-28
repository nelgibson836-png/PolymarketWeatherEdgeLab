import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


GAMMA_API = "https://gamma-api.polymarket.com"


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PolymarketWeatherEdgeLab/1.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_weather_tag():
    url = f"{GAMMA_API}/tags/slug/weather"
    return get_json(url)


def get_weather_events():
    params = {
        "tag_slug": "weather",
        "active": "true",
        "closed": "false",
        "limit": "100",
        "offset": "0",
    }

    url = f"{GAMMA_API}/events?{urllib.parse.urlencode(params)}"

    return get_json(url)


def main():
    now = datetime.now(timezone.utc)

    print("=" * 60)
    print("Polymarket Weather Edge Lab")
    print("=" * 60)
    print(f"UTC: {now.isoformat()}")
    print()

    try:
        print("1. Consultando tag Weather...")
        weather_tag = get_weather_tag()

        print("Tag encontrado:")
        print(f"  ID: {weather_tag.get('id')}")
        print(f"  Label: {weather_tag.get('label')}")
        print(f"  Slug: {weather_tag.get('slug')}")
        print()

        print("2. Consultando eventos Weather...")
        events = get_weather_events()

        print(f"Eventos Weather encontrados: {len(events)}")
        print()

        for event in events[:20]:

            print(f"ID: {event.get('id')}")
            print(f"Titulo: {event.get('title')}")
            print(f"Slug: {event.get('slug')}")
            print(f"Inicio: {event.get('startDate')}")
            print(f"Fin: {event.get('endDate')}")
            print(f"Liquidez: {event.get('liquidity')}")
            print(f"Volumen: {event.get('volume')}")

            markets = event.get("markets", [])

            print(f"Mercados dentro del evento: {len(markets)}")

            for market in markets[:10]:
                print(f"  Market ID: {market.get('id')}")
                print(f"  Pregunta: {market.get('question')}")
                print(f"  Slug: {market.get('slug')}")
                print(f"  Resolución: {market.get('resolutionSource')}")
                print(f"  Fin: {market.get('endDate')}")
                print(f"  Outcomes: {market.get('outcomes')}")
                print(f"  Prices: {market.get('outcomePrices')}")
                print(f"  Lower bound: {market.get('lowerBound')}")
                print(f"  Upper bound: {market.get('upperBound')}")
                print(f"  Group title: {market.get('groupItemTitle')}")
                print(f"  Group threshold: {market.get('groupItemThreshold')}")
                print()

            print("-" * 60)

    except Exception as e:
        print()
        print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

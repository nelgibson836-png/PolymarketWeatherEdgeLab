import json
import urllib.request
from datetime import datetime, timezone


GAMMA_API = "https://gamma-api.polymarket.com"


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "PolymarketWeatherEdgeLab/1.0"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_weather_markets():
    url = (
        f"{GAMMA_API}/events"
        "?active=true"
        "&closed=false"
        "&limit=100"
        "&tag=weather"
    )

    return get_json(url)


def main():
    now = datetime.now(timezone.utc)

    print("=" * 60)
    print("Polymarket Weather Edge Lab")
    print("=" * 60)
    print(f"UTC: {now.isoformat()}")
    print()

    try:
        events = get_weather_markets()

        print(f"Eventos encontrados: {len(events)}")
        print()

        for event in events[:20]:
            print(f"ID: {event.get('id')}")
            print(f"Titulo: {event.get('title')}")
            print(f"Slug: {event.get('slug')}")
            print("-" * 60)

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

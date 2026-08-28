import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone


GAMMA_API = "https://gamma-api.polymarket.com"


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PolymarketWeatherEdgeLab/1.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_tags():
    url = f"{GAMMA_API}/tags?limit=500&offset=0"
    return get_json(url)


def find_weather_tag(tags):
    for tag in tags:
        label = str(tag.get("label") or "").lower()
        slug = str(tag.get("slug") or "").lower()

        if label == "weather" or slug == "weather":
            return tag

    return None


def get_weather_markets(tag_id):
    params = {
        "tag_id": tag_id,
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
        print("Consultando tags de Polymarket...")

        tags = get_tags()

        print(f"Tags encontradas: {len(tags)}")
        print()

        weather_tag = find_weather_tag(tags)

        if not weather_tag:
            print("ERROR: No se encontró el tag Weather.")
            print()
            print("Tags disponibles relacionadas con clima:")
            
            for tag in tags:
                label = str(tag.get("label") or "").lower()
                slug = str(tag.get("slug") or "").lower()

                if any(
                    word in label or word in slug
                    for word in ["weather", "temperature", "climate"]
                ):
                    print(
                        f"ID={tag.get('id')} "
                        f"LABEL={tag.get('label')} "
                        f"SLUG={tag.get('slug')}"
                    )

            return

        print("Tag Weather encontrado:")
        print(f"ID: {weather_tag.get('id')}")
        print(f"Label: {weather_tag.get('label')}")
        print(f"Slug: {weather_tag.get('slug')}")
        print()

        events = get_weather_markets(weather_tag["id"])

        print(f"Eventos Weather encontrados: {len(events)}")
        print()

        for event in events[:30]:
            print(f"ID: {event.get('id')}")
            print(f"Titulo: {event.get('title')}")
            print(f"Slug: {event.get('slug')}")
            print(f"Ciudad/categoria: {event.get('category')}")
            print(f"Fecha inicio: {event.get('startDate')}")
            print(f"Fecha fin: {event.get('endDate')}")
            print("-" * 60)

    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

import json
import urllib.request


GAMMA_API = "https://gamma-api.polymarket.com"

TEST_MARKET_ID = "3945819"


def get_json(url):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PolymarketWeatherEdgeLab/5.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        return json.loads(
            response.read().decode("utf-8")
        )


def main():

    print("=" * 60)
    print("Polymarket Weather Edge Lab")
    print("Market Structure Diagnostic v5.0")
    print("=" * 60)

    print()
    print(f"Consultando market ID: {TEST_MARKET_ID}")
    print()

    url = f"{GAMMA_API}/markets/{TEST_MARKET_ID}"

    print(f"URL: {url}")
    print()

    try:

        market = get_json(url)

    except Exception as error:

        print("=" * 60)
        print("ERROR")
        print("=" * 60)

        print(
            f"{type(error).__name__}: {error}"
        )

        return

    print("=" * 60)
    print("RAW MARKET RESPONSE")
    print("=" * 60)

    print(
        json.dumps(
            market,
            ensure_ascii=False,
            indent=2,
        )
    )

    print()
    print("=" * 60)
    print("IMPORTANT FIELDS")
    print("=" * 60)

    fields = [
        "id",
        "question",
        "slug",
        "conditionId",
        "condition_id",
        "outcomes",
        "outcomePrices",
        "outcome_prices",
        "clobTokenIds",
        "clob_token_ids",
        "volume",
        "liquidity",
        "active",
        "closed",
        "endDate",
        "startDate",
        "resolutionSource",
        "groupItemTitle",
        "groupItemThreshold",
    ]

    for field in fields:

        value = market.get(field)

        print(
            f"{field}: {value}"
        )

    print()
    print("=" * 60)
    print("ALL AVAILABLE KEYS")
    print("=" * 60)

    for key in sorted(market.keys()):

        print(key)

    print()
    print("=" * 60)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()

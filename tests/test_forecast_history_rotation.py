import csv
import os
import tempfile

import edge_engine_v17


def test_legacy_and_daily_forecast_shards_are_combined():
    with tempfile.TemporaryDirectory() as tmp:
        legacy = os.path.join(tmp, "forecasts.csv")
        daily = os.path.join(tmp, "forecasts_2026-10-26.csv")
        for path, city in ((legacy, "London"), (daily, "Paris")):
            with open(path, "w", newline="", encoding="utf-8") as h:
                writer = csv.DictWriter(h, fieldnames=["city", "forecast_at"])
                writer.writeheader()
                writer.writerow({"city": city, "forecast_at": "2026-10-26T00:00:00Z"})

        old_path = edge_engine_v17.FORECAST_FILE
        try:
            edge_engine_v17.FORECAST_FILE = legacy
            rows = edge_engine_v17.read_forecast_history()
        finally:
            edge_engine_v17.FORECAST_FILE = old_path

        assert len(rows) == 2
        assert {row["city"] for row in rows} == {"London", "Paris"}


if __name__ == "__main__":
    test_legacy_and_daily_forecast_shards_are_combined()
    print("forecast history rotation tests: PASS")

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observation_lock_model import fit_lock_table, lookup_lock


def test_lock_table_is_station_specific():
    rows = []
    for day in range(35):
        date = f"2026-01-{day+1:02d}"
        rows.extend([
            {"station":"TEST","observation_time":f"{date}T12:00:00Z","temperature_c":"30"},
            {"station":"TEST","observation_time":f"{date}T15:00:00Z","temperature_c":"28"},
            {"station":"TEST","observation_time":f"{date}T18:00:00Z","temperature_c":"27"},
        ])
    table = fit_lock_table(rows, {"TEST":"UTC"})
    cell = lookup_lock(table, "TEST", 18, 3, "high")
    assert cell is not None
    assert cell["days"] >= 30
    assert 0 <= cell["probability_lower95"] <= cell["probability_mean"] <= 1


def test_sparse_station_has_no_fallback():
    rows = []
    for day in range(10):
        date = f"2026-02-{day+1:02d}"
        rows.append({"station":"TEST","observation_time":f"{date}T18:00:00Z","temperature_c":"20"})
    table = fit_lock_table(rows, {"TEST":"UTC"})
    assert lookup_lock(table, "OTHER", 18, 2, "high") is None


if __name__ == "__main__":
    test_lock_table_is_station_specific()
    test_sparse_station_has_no_fallback()
    print("observation lock tests: PASS")

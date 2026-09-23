import math
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

from v18_probability_calibration import calibrate_probability, fit_platt


def _rows(n=80):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        rows.append({
            "raw_model_probability": 0.5,
            "outcome": 1.0 if i % 2 == 0 else 0.0,
            "calibration_eligible": "true",
            "event_key": f"event-{i}",
            "market_date": (base + timedelta(days=i)).date().isoformat(),
            "decision_at": (base + timedelta(days=i)).isoformat(),
            "resolved_at": (base + timedelta(days=i, hours=1)).isoformat(),
        })
    return rows


def test_ready_requires_real_point_in_time_rows():
    p = fit_platt(_rows())
    assert p["ready"] is True
    assert p["independent_groups"] >= 75
    assert 0.25 <= p["slope"] <= 2.5


def test_calibration_is_bounded():
    assert 0 < calibrate_probability(0.001, -2, 1) < 1
    assert 0 < calibrate_probability(0.999, -2, 1) < 1


def test_insufficient_history_is_not_used():
    p = fit_platt(_rows(20))
    assert p["ready"] is False
    assert p["intercept"] == 0.0
    assert p["slope"] == 1.0


if __name__ == "__main__":
    test_ready_requires_real_point_in_time_rows()
    test_calibration_is_bounded()
    test_insufficient_history_is_not_used()
    print("v18 calibration tests: PASS")

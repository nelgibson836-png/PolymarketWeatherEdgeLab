import math
import sys

sys.path.insert(0,".")

from v18_probability_calibration import calibrate_probability, fit_platt

def test_identity_when_ready_is_not_forced():
    rows=[{"raw_model_probability":0.5,"outcome":1},{"raw_model_probability":0.5,"outcome":0}]*40
    p=fit_platt(rows)
    assert p["ready"] is True
    assert 0.25 <= p["slope"] <= 2.5

def test_calibration_is_bounded():
    assert 0 < calibrate_probability(0.001,-2,1) < 1
    assert 0 < calibrate_probability(0.999,-2,1) < 1

def test_insufficient_history_is_not_used():
    p=fit_platt([{"raw_model_probability":0.5,"outcome":1}]*20)
    assert p["ready"] is False
    assert p["intercept"] == 0.0
    assert p["slope"] == 1.0

if __name__=="__main__":
    test_identity_when_ready_is_not_forced()
    test_calibration_is_bounded()
    test_insufficient_history_is_not_used()
    print("v18 calibration tests: PASS")

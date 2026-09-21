import math


MIN_CALIBRATION_SAMPLES = 75
MIN_TYPE_CALIBRATION_SAMPLES = 150
SLOPE_MIN = 0.25
SLOPE_MAX = 2.50
INTERCEPT_MIN = -5.0
INTERCEPT_MAX = 2.0
RIDGE = 0.02


def _logit(p):
    p = max(1e-6, min(1.0 - 1e-6, float(p)))
    return math.log(p / (1.0 - p))


def _sigmoid(z):
    z = max(-35.0, min(35.0, float(z)))
    return 1.0 / (1.0 + math.exp(-z))


def calibrate_probability(raw_probability, intercept, slope):
    return _sigmoid(intercept + slope * _logit(raw_probability))


def fit_platt(rows, min_samples=MIN_CALIBRATION_SAMPLES):
    usable = []
    for row in rows:
        p = row.get("raw_model_probability")
        outcome = row.get("outcome")
        try:
            p = float(p)
            y = float(outcome)
        except (TypeError, ValueError):
            continue
        if not 0.0 < p < 1.0 or y not in (0.0, 1.0):
            continue
        usable.append((p, y))

    if len(usable) < min_samples:
        return {
            "ready": False,
            "n": len(usable),
            "intercept": 0.0,
            "slope": 1.0,
            "reason": "insufficient_resolved_history",
        }

    # Newton optimization for logistic recalibration:
    # calibrated_p = sigmoid(intercept + slope * logit(raw_p)).
    intercept = -1.0
    slope = 0.75

    for _ in range(40):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for p, y in usable:
            x = _logit(p)
            pred = _sigmoid(intercept + slope * x)
            weight = pred * (1.0 - pred)
            error = pred - y
            g0 += error
            g1 += error * x
            h00 += weight
            h01 += weight * x
            h11 += weight * x * x

        # Mild ridge stabilizes the fit and discourages extreme slope.
        g0 += RIDGE * intercept
        g1 += RIDGE * (slope - 1.0)
        h00 += RIDGE
        h11 += RIDGE

        determinant = h00 * h11 - h01 * h01
        if determinant <= 1e-12:
            break

        delta0 = (g0 * h11 - g1 * h01) / determinant
        delta1 = (g1 * h00 - g0 * h01) / determinant

        intercept -= delta0
        slope -= delta1
        intercept = max(INTERCEPT_MIN, min(INTERCEPT_MAX, intercept))
        slope = max(SLOPE_MIN, min(SLOPE_MAX, slope))

        if abs(delta0) < 1e-7 and abs(delta1) < 1e-7:
            break

    return {
        "ready": True,
        "n": len(usable),
        "intercept": round(intercept, 8),
        "slope": round(slope, 8),
        "reason": "platt_logit_recalibration",
    }


def _score(rows, params):
    brier = []
    log_loss = []
    for row in rows:
        p = float(row["raw_model_probability"])
        y = float(row["outcome"])
        q = calibrate_probability(p, params["intercept"], params["slope"])
        brier.append((q - y) ** 2)
        q = max(1e-9, min(1.0 - 1e-9, q))
        log_loss.append(-(y * math.log(q) + (1.0 - y) * math.log(1.0 - q)))
    if not brier:
        return {"n": 0, "brier": None, "log_loss": None}
    return {
        "n": len(brier),
        "brier": sum(brier) / len(brier),
        "log_loss": sum(log_loss) / len(log_loss),
    }


def walkforward_diagnostics(rows, min_train=MIN_CALIBRATION_SAMPLES):
    ordered = sorted(rows, key=lambda r: str(r.get("decision_at") or r.get("opened_at") or ""))
    history = []
    baseline_brier = []
    calibrated_brier = []
    baseline_log = []
    calibrated_log = []

    for row in ordered:
        if len(history) >= min_train:
            params = fit_platt(history, min_samples=min_train)
            if params["ready"]:
                raw = float(row["raw_model_probability"])
                y = float(row["outcome"])
                cal = calibrate_probability(raw, params["intercept"], params["slope"])
                baseline_brier.append((raw - y) ** 2)
                calibrated_brier.append((cal - y) ** 2)
                raw = max(1e-9, min(1.0 - 1e-9, raw))
                cal = max(1e-9, min(1.0 - 1e-9, cal))
                baseline_log.append(-(y * math.log(raw) + (1.0 - y) * math.log(1.0 - raw)))
                calibrated_log.append(-(y * math.log(cal) + (1.0 - y) * math.log(1.0 - cal)))
        history.append(row)

    n = len(baseline_brier)
    return {
        "scored_oos": n,
        "baseline_brier": (sum(baseline_brier) / n) if n else None,
        "calibrated_brier": (sum(calibrated_brier) / n) if n else None,
        "baseline_log_loss": (sum(baseline_log) / n) if n else None,
        "calibrated_log_loss": (sum(calibrated_log) / n) if n else None,
        "improved_brier": (sum(calibrated_brier) < sum(baseline_brier)) if n else None,
        "improved_log_loss": (sum(calibrated_log) < sum(baseline_log)) if n else None,
    }

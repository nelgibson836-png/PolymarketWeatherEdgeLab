import math
from collections import defaultdict
from datetime import datetime, timezone

MIN_GROUPS = 75
SLOPE_MIN = 0.25
SLOPE_MAX = 2.50
INTERCEPT_MIN = -5.0
INTERCEPT_MAX = 2.0
RIDGE = 0.02


def parse_dt(value):
    text = str(value or "")
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _logit(p):
    p = max(1e-6, min(1.0 - 1e-6, float(p)))
    return math.log(p / (1.0 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, float(z)))))


def calibrate_probability(raw_probability, intercept, slope):
    return _sigmoid(intercept + slope * _logit(raw_probability))


def eligible_rows(rows, cutoff=None, market_type=None):
    cutoff_dt = parse_dt(cutoff) if cutoff else None
    selected = []
    for row in rows:
        if str(row.get("calibration_eligible", "true")).lower() != "true":
            continue
        if market_type and str(row.get("market_type") or "") != market_type:
            continue
        resolved_at = parse_dt(row.get("resolved_at"))
        decision_at = parse_dt(row.get("decision_at") or row.get("opened_at"))
        if resolved_at is None or decision_at is None:
            continue
        if cutoff_dt is not None and resolved_at >= cutoff_dt:
            continue
        try:
            p = float(row.get("raw_model_probability"))
            y = float(row.get("outcome"))
        except (TypeError, ValueError):
            continue
        if not 0.0 < p < 1.0 or y not in (0.0, 1.0):
            continue
        selected.append(row)
    return selected


def _group_weights(rows):
    groups = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("event_key") or row.get("market_date") or row.get("market_id") or ""),
            str(row.get("market_date") or ""),
        )
        groups[key].append(row)
    return groups


def fit_platt(rows, cutoff=None, min_groups=MIN_GROUPS, market_type=None):
    usable = eligible_rows(rows, cutoff=cutoff, market_type=market_type)
    grouped = _group_weights(usable)
    if len(grouped) < min_groups:
        return {
            "ready": False,
            "rows": len(usable),
            "independent_groups": len(grouped),
            "intercept": 0.0,
            "slope": 1.0,
            "reason": "insufficient_resolved_independent_groups",
        }

    intercept = -1.0
    slope = 0.75

    for _ in range(50):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for group_rows in grouped.values():
            weight = 1.0 / len(group_rows)
            for row in group_rows:
                x = _logit(float(row["raw_model_probability"]))
                y = float(row["outcome"])
                pred = _sigmoid(intercept + slope * x)
                variance = pred * (1.0 - pred)
                error = pred - y
                g0 += weight * error
                g1 += weight * error * x
                h00 += weight * variance
                h01 += weight * variance * x
                h11 += weight * variance * x * x

        g0 += RIDGE * intercept
        g1 += RIDGE * (slope - 1.0)
        h00 += RIDGE
        h11 += RIDGE

        determinant = h00 * h11 - h01 * h01
        if determinant <= 1e-12:
            break

        d0 = (g0 * h11 - g1 * h01) / determinant
        d1 = (g1 * h00 - g0 * h01) / determinant
        intercept = max(INTERCEPT_MIN, min(INTERCEPT_MAX, intercept - d0))
        slope = max(SLOPE_MIN, min(SLOPE_MAX, slope - d1))

        if abs(d0) < 1e-7 and abs(d1) < 1e-7:
            break

    return {
        "ready": True,
        "rows": len(usable),
        "independent_groups": len(grouped),
        "intercept": round(intercept, 8),
        "slope": round(slope, 8),
        "reason": "point_in_time_platt_logit_recalibration",
    }


def choose_params(rows, cutoff, market_type):
    typed = fit_platt(rows, cutoff=cutoff, market_type=market_type)
    if typed["ready"]:
        typed["scope"] = "market_type"
        return typed
    global_params = fit_platt(rows, cutoff=cutoff)
    global_params["scope"] = "global"
    return global_params


def walkforward_diagnostics(rows, min_groups=MIN_GROUPS):
    ordered = sorted(
        [r for r in rows if parse_dt(r.get("decision_at") or r.get("opened_at"))],
        key=lambda r: parse_dt(r.get("decision_at") or r.get("opened_at")),
    )
    base_brier = []
    cal_brier = []
    base_log = []
    cal_log = []

    for row in ordered:
        decision_at = row.get("decision_at") or row.get("opened_at")
        params = choose_params(ordered, decision_at, row.get("market_type"))
        if not params["ready"]:
            continue
        if str(row.get("calibration_eligible", "true")).lower() != "true":
            continue
        try:
            raw = float(row["raw_model_probability"])
            y = float(row["outcome"])
        except (TypeError, ValueError):
            continue
        resolved_at = parse_dt(row.get("resolved_at"))
        decision_dt = parse_dt(decision_at)
        if resolved_at is None or decision_dt is None or resolved_at >= decision_dt:
            continue
        cal = calibrate_probability(raw, params["intercept"], params["slope"])
        base_brier.append((raw - y) ** 2)
        cal_brier.append((cal - y) ** 2)
        raw = max(1e-9, min(1.0 - 1e-9, raw))
        cal = max(1e-9, min(1.0 - 1e-9, cal))
        base_log.append(-(y * math.log(raw) + (1.0 - y) * math.log(1.0 - raw)))
        cal_log.append(-(y * math.log(cal) + (1.0 - y) * math.log(1.0 - cal)))

    n = len(base_brier)
    return {
        "scored_oos": n,
        "baseline_brier": sum(base_brier) / n if n else None,
        "calibrated_brier": sum(cal_brier) / n if n else None,
        "baseline_log_loss": sum(base_log) / n if n else None,
        "calibrated_log_loss": sum(cal_log) / n if n else None,
        "improved_brier": sum(cal_brier) < sum(base_brier) if n else None,
        "improved_log_loss": sum(cal_log) < sum(base_log) if n else None,
        "method": "point_in_time_by_resolution_availability",
        "group_weighting": "equal_event_weight",
    }

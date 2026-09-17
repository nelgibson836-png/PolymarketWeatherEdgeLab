import csv
import json
import math
import os
from datetime import datetime, timezone
from statistics import mean, pstdev

ENGINE_VERSION = "1.7"
DATA_DIR = "data"
ACTIVE_FILE = os.path.join(DATA_DIR, "active_temperature_markets.json")
FORECAST_FILE = os.path.join(DATA_DIR, "weather", "history", "forecasts.csv")
SUMMARY_FILE = os.path.join(DATA_DIR, "edge", "calibration", "station_calibration_summary.csv")
MATCHES_FILE = os.path.join(DATA_DIR, "edge", "calibration", "station_calibration_matches.csv")
EDGE_DIR = os.path.join(DATA_DIR, "edge")
LATEST_FILE = os.path.join(EDGE_DIR, "latest_signals_v17.json")
REPORT_FILE = os.path.join(EDGE_DIR, "latest_report_v17.txt")
SIGNAL_FILE = os.path.join(EDGE_DIR, "history", "signals_v17.csv")

DEFAULT_WEATHER_FEE_RATE = 0.05
MIN_CALIBRATION_SAMPLES = 15
MIN_EDGE = 0.03
MIN_EV = 0.015
STRONG_MIN_EDGE = 0.06
STRONG_MIN_EV = 0.02
THIN_ASK_MAX = 0.002
FAMILY_MIN_COVERAGE = 0.90
FAMILY_MAX_COVERAGE = 1.05
MAX_SIGNALS_PER_RUN = 100

MODEL_ALIASES = {"ecmwf": "ecmwf_ifs025", "ecmwf_ifs025": "ecmwf_ifs025"}

FIELDS = [
    "run_at","city","station","market_date","market_type","market_id","event_key",
    "bucket_type","bucket_value","bucket_low","bucket_high","probability_method",
    "model_probability","market_probability","entry_price","entry_source","fee_rate",
    "fee_per_share","gross_edge","net_ev_per_share","forecast_count","forecast_models",
    "forecast_mean_c","calibration_sigma_c","calibration_source","calibration_samples",
    "calibration_bias_c","calibration_flag","calibration_lead_days","family_probability_sum",
    "family_status","execution_quality","signal","reason"
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def i(v):
    try:
        return None if v in (None, "") else int(float(v))
    except (TypeError, ValueError):
        return None


def r(v, digits=6):
    return None if v is None else round(float(v), digits)


def read_json(path):
    with open(path, "r", encoding="utf-8") as h:
        return json.load(h)


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as h:
        json.dump(payload, h, ensure_ascii=False, indent=2)
        h.write("\n")
    os.replace(tmp, path)


def append_csv(path, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as h:
        writer = csv.DictWriter(h, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def canonical_model(model):
    raw = str(model or "").strip()
    return MODEL_ALIASES.get(raw, raw)


def infer_lead_days(row):
    try:
        target = datetime.strptime(str(row.get("market_date") or "")[:10], "%Y-%m-%d").date()
        text = str(row.get("collected_at") or "")
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(1, (target - dt.astimezone(timezone.utc).date()).days)
    except Exception:
        return None


def load_calibration_groups():
    groups = {}
    rejected_sanity = 0
    usable_count = 0
    for row in read_csv(SUMMARY_FILE):
        station = str(row.get("station") or "").upper().strip()
        model = canonical_model(row.get("model"))
        lead = i(row.get("lead_days"))
        samples = i(row.get("samples")) or 0
        usable = str(row.get("usable") or "").upper().strip() == "YES"
        if not station or not model or lead is None:
            continue
        min_bias = f(row.get("min_bias_c")) or 0.0
        max_bias = f(row.get("max_bias_c")) or 0.0
        min_rmse = f(row.get("min_rmse_c"))
        max_rmse = f(row.get("max_rmse_c"))
        min_sigma = f(row.get("min_sigma_c"))
        max_sigma = f(row.get("max_sigma_c"))
        sanity_ok = (
            abs(min_bias) <= 3.0 and
            abs(max_bias) <= 3.0 and
            (min_rmse is None or min_rmse <= 4.0) and
            (max_rmse is None or max_rmse <= 4.0)
        )
        accepted = usable and samples >= MIN_CALIBRATION_SAMPLES and sanity_ok
        if usable and not sanity_ok:
            rejected_sanity += 1
        if accepted:
            usable_count += 1
        groups[(station, model, lead)] = {
            "samples": samples,
            "min_bias": min_bias,
            "max_bias": max_bias,
            "min_sigma": min_sigma,
            "max_sigma": max_sigma,
            "usable": accepted,
        }
    return groups, usable_count, rejected_sanity


def load_residuals(groups):
    residuals = {}
    for row in read_csv(MATCHES_FILE):
        station = str(row.get("station") or "").upper().strip()
        model = canonical_model(row.get("model"))
        lead = i(row.get("lead_days"))
        key = (station, model, lead)
        group = groups.get(key)
        if group is None or not group["usable"]:
            continue
        min_error = f(row.get("error_min_c"))
        max_error = f(row.get("error_max_c"))
        bucket = residuals.setdefault(key, {"min": [], "max": []})
        if min_error is not None:
            bucket["min"].append(min_error)
        if max_error is not None:
            bucket["max"].append(max_error)
    return residuals


def latest_forecasts(rows):
    latest = {}
    for row in rows:
        station = str(row.get("station") or "").upper().strip()
        date = str(row.get("market_date") or "")[:10]
        model = str(row.get("model") or "").strip()
        if not station or not date or not model:
            continue
        key = (station, date, model)
        if key not in latest or str(row.get("collected_at") or "") > str(latest[key].get("collected_at") or ""):
            latest[key] = row
    return list(latest.values())


def normalize_market(market):
    out = dict(market)
    if str(market.get("temperature_unit") or "C").upper() == "F":
        for key in ("bucket_value", "bucket_low", "bucket_high"):
            value = f(market.get(key))
            out[key] = None if value is None else (value - 32.0) * 5.0 / 9.0
    return out


def empirical_probability(bucket_type, value, low, high, forecast, errors):
    if not errors:
        return None
    hits = 0
    for error in errors:
        actual = forecast + error
        if bucket_type == "exact" and value is not None and value - 0.5 <= actual < value + 0.5:
            hits += 1
        elif bucket_type == "or_lower" and value is not None and actual <= value + 0.5:
            hits += 1
        elif bucket_type == "or_higher" and value is not None and actual >= value - 0.5:
            hits += 1
        elif bucket_type == "range" and low is not None and high is not None and low - 0.5 <= actual < high + 0.5:
            hits += 1
    # Jeffreys prior prevents exact 0/1 probabilities with finite samples.
    return (hits + 0.5) / (len(errors) + 1.0)


def model_stats(forecasts, station, market_date, market_type, groups, residuals):
    entries = []
    for row in forecasts:
        row_station = str(row.get("station") or "").upper().strip()
        if row_station != str(station).upper().strip() or str(row.get("market_date") or "")[:10] != market_date:
            continue
        raw_model = str(row.get("model") or "").strip()
        model = canonical_model(raw_model)
        forecast = f(row.get("temperature_min_c" if market_type == "lowest_temperature" else "temperature_max_c"))
        lead = infer_lead_days(row)
        if forecast is None or lead is None:
            continue
        key = (row_station, model, lead)
        group = groups.get(key)
        dist = residuals.get(key)
        if not group or not group["usable"] or not dist:
            continue
        metric = "min" if market_type == "lowest_temperature" else "max"
        errors = dist[metric]
        if len(errors) < MIN_CALIBRATION_SAMPLES:
            continue
        bias = group["min_bias"] if metric == "min" else group["max_bias"]
        sigma_values = [x for x in errors]
        sigma = pstdev(sigma_values) if len(sigma_values) > 1 else 1.0
        entries.append({
            "raw_model": raw_model,
            "model": model,
            "forecast": forecast,
            "lead": lead,
            "errors": errors,
            "bias": bias,
            "sigma": sigma,
            "samples": len(errors),
        })
    if not entries:
        return None
    # A single calibrated model is intentionally used rather than mixing uncalibrated models.
    chosen = entries[0]
    return {
        "forecast": chosen["forecast"],
        "models": [e["raw_model"] for e in entries],
        "entries": entries,
        "samples": min(e["samples"] for e in entries),
        "bias": mean([e["bias"] for e in entries]),
        "sigma": max(0.75, min(5.0, mean([e["sigma"] for e in entries]))),
        "lead_days": sorted(set(e["lead"] for e in entries)),
    }


def fee_per_share(price, rate):
    return 0.0 if price is None or rate <= 0 else rate * price * (1.0 - price)


def family_sum(family, stats):
    exact = []
    for market in family:
        m = normalize_market(market)
        p = None
        for entry in stats["entries"]:
            forecast = entry["forecast"]
            value = f(m.get("bucket_value"))
            low = f(m.get("bucket_low"))
            high = f(m.get("bucket_high"))
            p = empirical_probability(m.get("bucket_type"), value, low, high, forecast, entry["errors"])
            break
        if p is not None and m.get("bucket_type") in ("exact", "range"):
            exact.append(p)
    if not exact:
        return None, "NO_EXCLUSIVE_BUCKETS"
    total = sum(exact)
    if total < FAMILY_MIN_COVERAGE:
        return total, "INCOMPLETE"
    if total > FAMILY_MAX_COVERAGE:
        return total, "OVERLAP_OR_PARSE_ERROR"
    return total, "PASS"


def make_signal(market, stats, family_value, family_status):
    m = normalize_market(market)
    entry = stats["entries"][0]
    raw_p = empirical_probability(
        m.get("bucket_type"), f(m.get("bucket_value")), f(m.get("bucket_low")), f(m.get("bucket_high")),
        entry["forecast"], entry["errors"]
    )
    if raw_p is None:
        return None
    model_p = raw_p
    if m.get("bucket_type") in ("exact", "range") and family_value and family_value > 0:
        model_p = max(0.0, min(1.0, raw_p / family_value))
    ask = f(m.get("best_ask"))
    if ask is None or not 0.0 < ask < 1.0:
        return None
    fee_rate = DEFAULT_WEATHER_FEE_RATE
    fee = fee_per_share(ask, fee_rate)
    edge = model_p - ask
    ev = edge - fee
    execution = "THIN_ASK" if ask <= THIN_ASK_MAX else "EXECUTABLE_ASK"
    reasons = []
    if execution == "THIN_ASK": reasons.append("thin_ask")
    if m.get("bucket_type") in ("exact", "range") and family_status != "PASS": reasons.append("family=" + family_status)
    if edge < MIN_EDGE: reasons.append("edge_below_min")
    if ev < MIN_EV: reasons.append("ev_below_min")
    strong = (
        execution == "EXECUTABLE_ASK" and
        (m.get("bucket_type") not in ("exact", "range") or family_status == "PASS") and
        edge >= STRONG_MIN_EDGE and ev >= STRONG_MIN_EV and
        stats["samples"] >= MIN_CALIBRATION_SAMPLES
    )
    signal = "PAPER_BUY" if strong else ("POSSIBLE_EDGE" if edge >= MIN_EDGE and ev >= MIN_EV else "NO_TRADE")
    if strong: reasons.append("empirical_calibration_thresholds_pass")
    return {
        "run_at": now_iso(), "city": m.get("city"), "station": m.get("resolution_station"),
        "market_date": m.get("market_date"), "market_type": m.get("market_type"),
        "market_id": m.get("market_id"), "event_key": m.get("event_key"),
        "bucket_type": m.get("bucket_type"), "bucket_value": m.get("bucket_value"),
        "bucket_low": m.get("bucket_low"), "bucket_high": m.get("bucket_high"),
        "probability_method": "empirical_station_model_lead_error_distribution",
        "model_probability": r(model_p), "market_probability": r(f(m.get("yes_price"))),
        "entry_price": r(ask), "entry_source": "ask", "fee_rate": fee_rate,
        "fee_per_share": r(fee, 8), "gross_edge": r(edge), "net_ev_per_share": r(ev),
        "forecast_count": stats["entries"].__len__(), "forecast_models": ",".join(stats["models"]),
        "forecast_mean_c": r(stats["forecast"], 4), "calibration_sigma_c": r(stats["sigma"], 4),
        "calibration_source": "station_model_lead_empirical", "calibration_samples": stats["samples"],
        "calibration_bias_c": r(stats["bias"], 4), "calibration_flag": "OK",
        "calibration_lead_days": ",".join(str(x) for x in stats["lead_days"]),
        "family_probability_sum": r(family_value), "family_status": family_status,
        "execution_quality": execution, "signal": signal, "reason": "; ".join(reasons) or "no_trade_conditions",
    }


def main():
    print("=" * 72)
    print("POLYMARKET WEATHER EDGE ENGINE V1.7")
    print("EMPIRICAL CALIBRATION — SHADOW PAPER VALIDATION")
    print("NO ORDERS SENT")
    print("=" * 72)
    run_at = now_iso()
    markets = read_json(ACTIVE_FILE).get("markets", [])
    forecasts = latest_forecasts(read_csv(FORECAST_FILE))
    groups, usable_count, rejected_sanity = load_calibration_groups()
    residuals = load_residuals(groups)
    print(f"UTC: {run_at}")
    print(f"Current markets: {len(markets)}")
    print(f"Latest forecast records: {len(forecasts)}")
    print(f"Calibration groups accepted: {usable_count}")
    print(f"Calibration groups rejected by sanity: {rejected_sanity}")
    families = {}
    for market in markets:
        if market.get("event_key"):
            families.setdefault(market.get("event_key"), []).append(market)
    signals = []
    families_with_cal = 0
    skipped_no_station = 0
    skipped_no_forecast = 0
    for _, family in families.items():
        first = family[0]
        station = first.get("resolution_station")
        date = str(first.get("market_date") or "")[:10]
        market_type = first.get("market_type")
        if not station or not date:
            skipped_no_station += len(family)
            continue
        stats = model_stats(forecasts, station, date, market_type, groups, residuals)
        if not stats:
            skipped_no_forecast += len(family)
            continue
        families_with_cal += 1
        family_value, family_status = family_sum(family, stats)
        for market in family:
            signal = make_signal(market, stats, family_value, family_status)
            if signal:
                signals.append(signal)
    rank = {"PAPER_BUY": 3, "POSSIBLE_EDGE": 1, "NO_TRADE": 0}
    signals.sort(key=lambda x: (rank.get(x["signal"], 0), x["net_ev_per_share"] or -9, x["gross_edge"] or -9), reverse=True)
    retained = signals[:MAX_SIGNALS_PER_RUN]
    paper = [x for x in retained if x["signal"] == "PAPER_BUY"]
    print("\nTOP SHADOW PAPER SIGNALS")
    for s in paper[:20]:
        print(f"{s['city']} | {s['station']} | {s['market_date']} | {s['bucket_type']} {s['bucket_value']} | p={s['model_probability']} ask={s['entry_price']} edge={s['gross_edge']} | n={s['calibration_samples']} | {s['signal']}")
    payload = {
        "engine_version": ENGINE_VERSION, "run_at": run_at, "mode": "shadow_paper", "no_orders_sent": True,
        "probability_method": "empirical_station_model_lead_error_distribution",
        "calibration_groups_accepted": usable_count, "calibration_groups_rejected_sanity": rejected_sanity,
        "signals_evaluated": len(signals), "signals_retained": len(retained), "paper_buy_candidates": len(paper),
        "diagnostics": {"families": len(families), "families_with_calibration": families_with_cal, "skipped_no_station": skipped_no_station, "skipped_no_forecast": skipped_no_forecast},
        "signals": retained,
    }
    write_json(LATEST_FILE, payload)
    append_csv(SIGNAL_FILE, retained)
    with open(REPORT_FILE, "w", encoding="utf-8") as h:
        h.write(f"POLYMARKET WEATHER EDGE ENGINE V{ENGINE_VERSION}\n")
        h.write(f"UTC: {run_at}\n")
        h.write(f"Calibration groups accepted: {usable_count}\n")
        h.write(f"Calibration groups rejected by sanity: {rejected_sanity}\n")
        h.write(f"Signals evaluated: {len(signals)}\n")
        h.write(f"PAPER_BUY candidates: {len(paper)}\n\n")
        for s in paper[:50]:
            h.write(f"{s['city']}|{s['market_id']}|p={s['model_probability']}|ask={s['entry_price']}|edge={s['gross_edge']}|n={s['calibration_samples']}\n")
    print("\nSUMMARY")
    print(f"Signals evaluated: {len(signals)}")
    print(f"PAPER_BUY candidates: {len(paper)}")
    print(f"Families with empirical calibration: {families_with_cal}")


if __name__ == "__main__":
    main()

import csv
import json
import math
import os
from datetime import datetime, timezone
from statistics import mean

ENGINE_VERSION = "1.6"
DATA_DIR = "data"
ACTIVE_FILE = os.path.join(DATA_DIR, "active_temperature_markets.json")
FORECAST_FILE = os.path.join(DATA_DIR, "weather", "history", "forecasts.csv")
OBSERVATION_FILE = os.path.join(DATA_DIR, "weather", "history", "observations.csv")
CALIBRATION_FILE = os.path.join(DATA_DIR, "edge", "calibration", "station_calibration_summary.csv")
EDGE_DIR = os.path.join(DATA_DIR, "edge")
LATEST_FILE = os.path.join(EDGE_DIR, "latest_signals_v16.json")
REPORT_FILE = os.path.join(EDGE_DIR, "latest_report_v16.txt")
SIGNAL_FILE = os.path.join(EDGE_DIR, "history", "signals_v16.csv")

DEFAULT_SIGMA_C = 2.0
MIN_SIGMA_C = 0.75
MAX_SIGMA_C = 5.0
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

MODEL_ALIASES = {
    "ecmwf": "ecmwf_ifs025",
    "ecmwf_ifs025": "ecmwf_ifs025",
}

SIGNAL_FIELDS = [
    "run_at", "city", "station", "market_date", "market_type", "market_id",
    "event_key", "bucket_type", "bucket_value", "bucket_low", "bucket_high",
    "model_probability", "market_probability", "entry_price", "entry_source",
    "fee_rate", "fee_per_share", "gross_edge", "net_ev_per_share",
    "forecast_count", "forecast_models", "forecast_mean_c", "forecast_sigma_c",
    "calibration_source", "calibration_samples", "calibration_bias_c",
    "calibration_sigma_c", "calibration_flag", "calibration_lead_days",
    "family_probability_sum", "family_status", "execution_quality", "signal", "reason"
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def f(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def i(value):
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def r(value, digits=6):
    return None if value is None else round(float(value), digits)


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


def append_csv(path, fields, rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as h:
        writer = csv.DictWriter(h, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def normal_cdf(x, mu, sigma):
    z = (x - mu) / (sigma * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def clamp(p):
    return max(0.0, min(1.0, float(p)))


def bucket_probability(bucket_type, value, low, high, mu, sigma):
    if mu is None or sigma is None or sigma <= 0:
        return None
    if bucket_type == "exact" and value is not None:
        return clamp(normal_cdf(value + 0.5, mu, sigma) - normal_cdf(value - 0.5, mu, sigma))
    if bucket_type == "or_lower" and value is not None:
        return clamp(normal_cdf(value + 0.5, mu, sigma))
    if bucket_type == "or_higher" and value is not None:
        return clamp(1.0 - normal_cdf(value - 0.5, mu, sigma))
    if bucket_type == "range" and low is not None and high is not None and high >= low:
        return clamp(normal_cdf(high + 0.5, mu, sigma) - normal_cdf(low - 0.5, mu, sigma))
    return None


def normalize_market(market):
    out = dict(market)
    unit = str(market.get("temperature_unit") or "C").upper()
    if unit == "F":
        for key in ("bucket_value", "bucket_low", "bucket_high"):
            value = f(market.get(key))
            out[key] = None if value is None else (value - 32.0) * 5.0 / 9.0
    return out


def infer_lead_days(row):
    try:
        target = datetime.strptime(str(row.get("market_date"))[:10], "%Y-%m-%d").date()
        text = str(row.get("collected_at") or "")
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        lead = (target - dt.astimezone(timezone.utc).date()).days
        return max(1, lead)
    except Exception:
        return None


def canonical_model(model):
    raw = str(model or "").strip()
    return MODEL_ALIASES.get(raw, raw)


def load_calibration():
    result = {}
    for row in read_csv(CALIBRATION_FILE):
        station = str(row.get("station") or "").upper().strip()
        model = canonical_model(row.get("model"))
        lead = i(row.get("lead_days"))
        samples = i(row.get("samples")) or 0
        if not station or not model or lead is None:
            continue
        min_bias = f(row.get("min_bias_c")) or 0.0
        max_bias = f(row.get("max_bias_c")) or 0.0
        min_sigma = max(MIN_SIGMA_C, min(MAX_SIGMA_C, f(row.get("min_sigma_c")) or DEFAULT_SIGMA_C))
        max_sigma = max(MIN_SIGMA_C, min(MAX_SIGMA_C, f(row.get("max_sigma_c")) or DEFAULT_SIGMA_C))
        min_rmse = f(row.get("min_rmse_c"))
        max_rmse = f(row.get("max_rmse_c"))
        result[(station, model, lead)] = {
            "samples": samples,
            "min_bias": min_bias,
            "min_sigma": min_sigma,
            "min_rmse": min_rmse,
            "max_bias": max_bias,
            "max_sigma": max_sigma,
            "max_rmse": max_rmse,
            "flag": str(row.get("calibration_flag") or "OK").strip().upper(),
        }
        if result[(station, model, lead)]["flag"] not in ("OK", ""):
            result[(station, model, lead)]["usable"] = False
        else:
            result[(station, model, lead)]["usable"] = samples >= MIN_CALIBRATION_SAMPLES
    return result


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


def forecast_stats(forecasts, station, market_date, market_type, calibration):
    entries = []
    for row in forecasts:
        row_station = str(row.get("station") or "").upper().strip()
        if row_station != str(station).upper().strip() or str(row.get("market_date") or "")[:10] != market_date:
            continue
        raw_model = str(row.get("model") or "").strip()
        model = canonical_model(raw_model)
        raw = f(row.get("temperature_min_c" if market_type == "lowest_temperature" else "temperature_max_c"))
        if raw is None:
            continue
        lead = infer_lead_days(row)
        if lead is None:
            continue
        cal = calibration.get((row_station, model, lead))
        if not cal or not cal["usable"]:
            continue
        bias = cal["min_bias"] if market_type == "lowest_temperature" else cal["max_bias"]
        sigma = cal["min_sigma"] if market_type == "lowest_temperature" else cal["max_sigma"]
        rmse = cal["min_rmse"] if market_type == "lowest_temperature" else cal["max_rmse"]
        flag = cal["flag"] or "OK"
        entries.append({
            "raw_model": raw_model, "model": model, "raw": raw,
            "corrected": raw + bias, "sigma": sigma, "bias": bias,
            "samples": cal["samples"], "rmse": rmse, "flag": flag, "lead": lead,
        })
    if not entries:
        return None
    values = [x["corrected"] for x in entries]
    sigmas = [x["sigma"] for x in entries]
    mu = mean(values)
    between = 0.0 if len(values) < 2 else sum((x - mu) ** 2 for x in values) / (len(values) - 1)
    sigma = math.sqrt(max(0.0, mean([x * x for x in sigmas]) + between))
    sigma = max(MIN_SIGMA_C, min(MAX_SIGMA_C, sigma))
    total_samples = sum(x["samples"] for x in entries)
    weighted_bias = sum(x["bias"] * max(1, x["samples"]) for x in entries) / max(1, total_samples)
    return {
        "mean_c": mu,
        "sigma_c": sigma,
        "models": [x["raw_model"] for x in entries],
        "canonical_models": [x["model"] for x in entries],
        "calibration_samples": total_samples,
        "calibration_bias_c": weighted_bias,
        "calibration_source": ",".join(sorted(set("station_model_lead_min" if market_type == "lowest_temperature" else "station_model_lead_max" for _ in entries))),
        "calibration_flag": "OK" if all(x["flag"] in ("OK", "") for x in entries) else ",".join(sorted(set(x["flag"] for x in entries))),
        "calibration_lead_days": ",".join(str(x["lead"]) for x in entries),
        "forecast_count": len(entries),
    }


def fee_per_share(price, rate):
    if price is None or rate <= 0:
        return 0.0
    return rate * price * (1.0 - price)


def family_sum(family, stats):
    exclusive = []
    for market in family:
        m = normalize_market(market)
        p = bucket_probability(m.get("bucket_type"), f(m.get("bucket_value")), f(m.get("bucket_low")), f(m.get("bucket_high")), stats["mean_c"], stats["sigma_c"])
        if p is not None and m.get("bucket_type") in ("exact", "range"):
            exclusive.append(p)
    if not exclusive:
        return None, "NO_EXCLUSIVE_BUCKETS"
    total = sum(exclusive)
    if total < FAMILY_MIN_COVERAGE:
        return total, "INCOMPLETE"
    if total > FAMILY_MAX_COVERAGE:
        return total, "OVERLAP_OR_PARSE_ERROR"
    return total, "PASS"


def make_signal(market, stats, family_sum_value, family_status):
    m = normalize_market(market)
    raw_p = bucket_probability(m.get("bucket_type"), f(m.get("bucket_value")), f(m.get("bucket_low")), f(m.get("bucket_high")), stats["mean_c"], stats["sigma_c"])
    if raw_p is None:
        return None
    if m.get("bucket_type") in ("exact", "range") and family_sum_value and family_sum_value > 0:
        model_p = clamp(raw_p / family_sum_value)
    else:
        model_p = raw_p
    ask = f(m.get("best_ask"))
    if ask is None or not (0.0 < ask < 1.0):
        return None
    fee_rate = DEFAULT_WEATHER_FEE_RATE
    fee = fee_per_share(ask, fee_rate)
    edge = model_p - ask
    ev = edge - fee
    exec_quality = "THIN_ASK" if ask <= THIN_ASK_MAX else "EXECUTABLE_ASK"
    reasons = []
    if exec_quality == "THIN_ASK":
        reasons.append("thin_ask")
    if m.get("bucket_type") in ("exact", "range") and family_status != "PASS":
        reasons.append("family=" + family_status)
    if edge < MIN_EDGE:
        reasons.append("edge_below_min")
    if ev < MIN_EV:
        reasons.append("ev_below_min")
    strong = (exec_quality == "EXECUTABLE_ASK" and (m.get("bucket_type") not in ("exact", "range") or family_status == "PASS") and edge >= STRONG_MIN_EDGE and ev >= STRONG_MIN_EV and stats["calibration_samples"] >= MIN_CALIBRATION_SAMPLES and stats["calibration_flag"] == "OK")
    signal = "PAPER_BUY" if strong else ("POSSIBLE_EDGE" if edge >= MIN_EDGE and ev >= MIN_EV else "NO_TRADE")
    if strong:
        reasons.append("calibrated_thresholds_pass")
    return {
        "run_at": now_iso(),
        "city": m.get("city"), "station": m.get("resolution_station"),
        "market_date": m.get("market_date"), "market_type": m.get("market_type"),
        "market_id": m.get("market_id"), "event_key": m.get("event_key"),
        "bucket_type": m.get("bucket_type"), "bucket_value": m.get("bucket_value"),
        "bucket_low": m.get("bucket_low"), "bucket_high": m.get("bucket_high"),
        "model_probability": r(model_p), "market_probability": r(f(m.get("yes_price"))),
        "entry_price": r(ask), "entry_source": "ask", "fee_rate": r(fee_rate),
        "fee_per_share": r(fee, 8), "gross_edge": r(edge), "net_ev_per_share": r(ev),
        "forecast_count": stats["forecast_count"], "forecast_models": ",".join(stats["models"]),
        "forecast_mean_c": r(stats["mean_c"], 4), "forecast_sigma_c": r(stats["sigma_c"], 4),
        "calibration_source": stats["calibration_source"], "calibration_samples": stats["calibration_samples"],
        "calibration_bias_c": r(stats["calibration_bias_c"], 4), "calibration_sigma_c": r(stats["sigma_c"], 4),
        "calibration_flag": stats["calibration_flag"], "calibration_lead_days": stats["calibration_lead_days"],
        "family_probability_sum": r(family_sum_value), "family_status": family_status,
        "execution_quality": exec_quality, "signal": signal, "reason": "; ".join(reasons) or "no_trade_conditions",
    }


def main():
    print("=" * 72)
    print("POLYMARKET WEATHER EDGE ENGINE V1.6")
    print("CALIBRATED PAPER-TRADING SIGNALS ONLY")
    print("NO ORDERS SENT")
    print("=" * 72)
    run_at = now_iso()
    active = read_json(ACTIVE_FILE)
    markets = active.get("markets", [])
    forecasts = latest_forecasts(read_csv(FORECAST_FILE))
    calibration = load_calibration()
    print(f"UTC: {run_at}")
    print(f"Current markets: {len(markets)}")
    print(f"Latest forecast records: {len(forecasts)}")
    print(f"Calibration groups: {len(calibration)}")
    model_counts = {}
    for row in forecasts:
        model_counts[canonical_model(row.get("model"))] = model_counts.get(canonical_model(row.get("model")), 0) + 1
    print("Live models:", ", ".join(f"{k}={v}" for k, v in sorted(model_counts.items())))
    print("Calibration model aliases: ecmwf -> ecmwf_ifs025")

    families = {}
    for market in markets:
        key = market.get("event_key")
        if key:
            families.setdefault(key, []).append(market)

    stats_cache = {}
    signals = []
    diagnostics = {"families": len(families), "skipped_no_station": 0, "skipped_no_forecast": 0, "families_with_calibration": 0, "families_without_calibration": 0}

    for event_key, family in families.items():
        first = family[0]
        station = first.get("resolution_station")
        market_date = str(first.get("market_date") or "")[:10]
        market_type = first.get("market_type")
        if not station or not market_date:
            diagnostics["skipped_no_station"] += len(family)
            continue
        cache_key = (str(station).upper(), market_date, market_type)
        if cache_key not in stats_cache:
            stats_cache[cache_key] = forecast_stats(forecasts, station, market_date, market_type, calibration)
        stats = stats_cache[cache_key]
        if not stats:
            diagnostics["skipped_no_forecast"] += len(family)
            continue
        diagnostics["families_with_calibration"] += 1
        family_value, family_status = family_sum(family, stats)
        for market in family:
            signal = make_signal(market, stats, family_value, family_status)
            if signal:
                signals.append(signal)

    rank = {"PAPER_BUY": 3, "POSSIBLE_EDGE": 1, "NO_TRADE": 0}
    signals.sort(key=lambda x: (rank.get(x["signal"], 0), x["net_ev_per_share"] or -9, x["gross_edge"] or -9), reverse=True)
    retained = signals[:MAX_SIGNALS_PER_RUN]
    paper = [x for x in retained if x["signal"] == "PAPER_BUY"]

    print("\nTOP PAPER SIGNALS")
    for s in paper[:20]:
        print("-" * 72)
        print(f"{s['city']} | {s['station']} | {s['market_date']} | {s['bucket_type']} {s['bucket_value']}")
        print(f"  probability={s['model_probability']} ask={s['entry_price']} edge={s['gross_edge']} ev={s['net_ev_per_share']}")
        print(f"  calibration={s['calibration_source']} n={s['calibration_samples']} lead={s['calibration_lead_days']} flag={s['calibration_flag']}")
        print(f"  family={s['family_status']} execution={s['execution_quality']} SIGNAL={s['signal']}")

    payload = {
        "engine_version": ENGINE_VERSION,
        "run_at": run_at,
        "mode": "paper",
        "no_orders_sent": True,
        "model_aliases": MODEL_ALIASES,
        "calibration_groups": len(calibration),
        "signals_evaluated": len(signals),
        "signals_retained": len(retained),
        "paper_buy_candidates": len(paper),
        "diagnostics": diagnostics,
        "signals": retained,
    }
    write_json(LATEST_FILE, payload)
    report = [
        f"POLYMARKET WEATHER EDGE ENGINE V{ENGINE_VERSION}",
        f"UTC: {run_at}",
        f"Current markets: {len(markets)}",
        f"Forecast records: {len(forecasts)}",
        f"Calibration groups: {len(calibration)}",
        f"Signals evaluated: {len(signals)}",
        f"PAPER_BUY candidates: {len(paper)}",
        "",
    ]
    for s in paper[:50]:
        report.append(f"{s['city']} | {s['station']} | {s['market_date']} | market_id={s['market_id']} | ask={s['entry_price']} | edge={s['gross_edge']} | ev={s['net_ev_per_share']}")
    os.makedirs(EDGE_DIR, exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as h:
        h.write("\n".join(report) + "\n")
    append_csv(SIGNAL_FILE, SIGNAL_FIELDS, retained)
    print("\nSUMMARY")
    print(f"Signals evaluated: {len(signals)}")
    print(f"Signals retained: {len(retained)}")
    print(f"PAPER_BUY candidates: {len(paper)}")
    print(f"Families with calibration: {diagnostics['families_with_calibration']}")
    print(f"Skipped without station: {diagnostics['skipped_no_station']}")
    print(f"Skipped without forecast: {diagnostics['skipped_no_forecast']}")


if __name__ == "__main__":
    main()

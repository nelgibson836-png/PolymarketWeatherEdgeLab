import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from v18_probability_calibration import fit_platt, calibrate_probability, walkforward_diagnostics
from observation_lock_model import fit_lock_table, lookup_lock
from edge_engine_v17 import (
    f,
    i,
    r,
    now_iso,
    read_json,
    read_csv,
    write_json,
    latest_forecasts,
    load_calibration_groups,
    load_residuals,
    model_stats,
    normalize_market,
    empirical_probability,
    fee_per_share,
)

ENGINE_VERSION = "1.8"
DATA_DIR = "data"
ACTIVE_FILE = os.path.join(DATA_DIR, "active_temperature_markets.json")
FORECAST_FILE = os.path.join(DATA_DIR, "weather", "history", "forecasts.csv")
SUMMARY_FILE = os.path.join(DATA_DIR, "edge", "calibration", "station_calibration_summary.csv")
MATCHES_FILE = os.path.join(DATA_DIR, "edge", "calibration", "station_calibration_matches.csv")
OBS_FILE = os.path.join(DATA_DIR, "weather", "history", "observations.csv")
CLOB_FILE = os.path.join(DATA_DIR, "edge", "execution", "latest_clob_books.json")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
PAPER_TRADES_FILE = os.path.join(DATA_DIR, "edge", "paper_v18", "trades.csv")
V17_FILE = os.path.join(DATA_DIR, "edge", "latest_signals_v17.json")

EDGE_DIR = os.path.join(DATA_DIR, "edge")
LATEST_FILE = os.path.join(EDGE_DIR, "latest_signals_v18.json")
REPORT_FILE = os.path.join(EDGE_DIR, "latest_report_v18.txt")
HISTORY_FILE = os.path.join(EDGE_DIR, "history", "signals_v18.csv")
VALIDATION_DIR = os.path.join(EDGE_DIR, "validation")
COMPARISON_FILE = os.path.join(VALIDATION_DIR, "latest_v18_comparison.json")

DEFAULT_WEATHER_FEE_RATE = 0.05
MIN_CALIBRATION_SAMPLES = 15

MIN_ENTRY_PRICE = 0.02
MICROPRICE_MAX = 0.02
MAX_RAW_EDGE = 0.40
MAX_CALIBRATED_EDGE = 0.20
MIN_EDGE = 0.03
MIN_EV = 0.015
STRONG_MIN_EDGE = 0.06
STRONG_MIN_EV = 0.02

SHRINK_MIN_ALPHA = 0.25
SHRINK_MAX_ALPHA = 1.00
SHRINK_MIN_TRADES = 30

LOCK_ENABLED = True
LOCK_MIN_LOCAL_HOUR = 17
LOCK_MIN_MAX_AGE_HOURS = 2.0
LOCK_COOLING_GAP_C = 2.0
LOCK_LOOKBACK_OBS = 2
LOCK_PROBABILITY = 0.97
LOCK_MAX_ASK = 0.95

FIELDS = [
    "run_at","city","station","market_date","market_type","market_id","event_key",
    "bucket_type","bucket_value","bucket_low","bucket_high","probability_method",
    "raw_model_probability","shrunken_model_probability","market_probability",
    "entry_price","entry_source","fee_rate","fee_per_share","gross_edge",
    "net_ev_per_share","forecast_count","forecast_models","forecast_mean_c",
    "calibration_sigma_c","calibration_source","calibration_samples","calibration_bias_c",
    "calibration_lead_days","family_probability_sum","family_status","settlement_guard",
    "source_variants","source_changed","price_bucket","execution_source","clob_best_ask","clob_best_bid","clob_best_ask_depth","signal","reason"
]

STATION_TIMEZONES = {
    "CYYZ": "America/Toronto",
    "EDDM": "Europe/Berlin",
    "EFHK": "Europe/Helsinki",
    "EGLC": "Europe/London",
    "EHAM": "Europe/Amsterdam",
    "EPWA": "Europe/Warsaw",
    "FACT": "Africa/Johannesburg",
    "KATL": "America/New_York",
    "KAUS": "America/Chicago",
    "KBKF": "America/Denver",
    "KDAL": "America/Chicago",
    "KHOU": "America/Chicago",
    "KLAX": "America/Los_Angeles",
    "KLGA": "America/New_York",
    "KMIA": "America/New_York",
    "KORD": "America/Chicago",
    "KSEA": "America/Los_Angeles",
    "KSFO": "America/Los_Angeles",
    "LEMD": "Europe/Madrid",
    "LFPB": "Europe/Paris",
    "LIMC": "Europe/Rome",
    "LTAC": "Europe/Istanbul",
    "MMMX": "America/Mexico_City",
    "MPMG": "America/Panama",
    "NZWN": "Pacific/Auckland",
    "OEJN": "Asia/Riyadh",
    "OPKC": "Asia/Karachi",
    "RJTT": "Asia/Tokyo",
    "RKPK": "Asia/Seoul",
    "RKSI": "Asia/Seoul",
    "RPLL": "Asia/Manila",
    "SAEZ": "America/Argentina/Buenos_Aires",
    "SBGR": "America/Sao_Paulo",
    "VILK": "Asia/Kolkata",
    "WMKK": "Asia/Kuala_Lumpur",
    "WSSS": "Asia/Singapore",
    "ZBAA": "Asia/Shanghai",
    "ZGGG": "Asia/Shanghai",
    "ZGSZ": "Asia/Shanghai",
    "ZHCC": "Asia/Shanghai",
    "ZHHH": "Asia/Shanghai",
    "ZSPD": "Asia/Shanghai",
    "ZSQD": "Asia/Shanghai",
    "ZUCK": "Asia/Shanghai",
    "ZUUU": "Asia/Shanghai",
}


def sigmoid(x):
    if x >= 35:
        return 1.0
    if x <= -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def logit(p):
    p = max(1e-6, min(1.0 - 1e-6, float(p)))
    return math.log(p / (1.0 - p))


def apply_shrink(p, alpha):
    return sigmoid(alpha * logit(p))


def closed_paper_trades():
    rows = read_csv(PAPER_TRADES_FILE)
    out = []
    for row in rows:
        if str(row.get("status") or "").upper() != "CLOSED":
            continue
        p = f(row.get("raw_model_probability"))
        result = str(row.get("result") or "").upper()
        if p is None or result not in ("WIN", "LOSS") or not 0.0 < p < 1.0:
            continue
        out.append({
            "decision_at": str(row.get("opened_at") or ""),
            "resolved_at": str(row.get("resolved_at") or ""),
            "raw_model_probability": p,
            "outcome": 1.0 if result == "WIN" else 0.0,
            "market_type": str(row.get("market_type") or "unknown"),
            "event_key": str(row.get("event_key") or ""),
            "market_date": str(row.get("market_date") or ""),
            "calibration_eligible": "true",
        })
    return out


def calibration_params(trades, market_type=None):
    params = fit_platt(trades, cutoff=now_iso(), market_type=market_type)
    if not params["ready"] and market_type is not None:
        params = fit_platt(trades, cutoff=now_iso())
        params["scope"] = "global"
    return params


def calibrated_probability(raw_probability, params):
    if not params.get("ready"):
        return float(raw_probability)
    return calibrate_probability(
        raw_probability,
        params.get("intercept", 0.0),
        params.get("slope", 1.0),
    )


def walkforward_calibration_diagnostics(trades):
    return walkforward_diagnostics(trades)



def load_source_history():
    rows = []
    if not os.path.isdir(HISTORY_DIR):
        return rows
    for name in sorted(os.listdir(HISTORY_DIR)):
        if not name.endswith(".csv"):
            continue
        path = os.path.join(HISTORY_DIR, name)
        try:
            rows.extend(read_csv(path))
        except Exception:
            continue
    return rows


def source_guard(history_rows):
    variants = defaultdict(set)
    for row in history_rows:
        city = str(row.get("city") or "").strip().lower()
        date = str(row.get("market_date") or "")[:10]
        mtype = str(row.get("market_type") or "").strip()
        unit = str(row.get("temperature_unit") or "").strip().upper()
        station = str(row.get("resolution_station") or "").strip().upper()
        provider = str(row.get("resolution_provider") or "").strip().upper()
        if not city or not date or not mtype or not unit or not station:
            continue
        variants[(city, date, mtype, unit)].add((station, provider))
    return variants


def settlement_guard_for_market(market, variants):
    m = normalize_market(market)
    key = (
        str(m.get("city") or "").strip().lower(),
        str(m.get("market_date") or "")[:10],
        str(m.get("market_type") or "").strip(),
        str(m.get("temperature_unit") or "C").upper(),
    )
    current = (
        str(m.get("resolution_station") or "").strip().upper(),
        str(m.get("resolution_provider") or "").strip().upper(),
    )
    seen = variants.get(key, set())
    changed = len(seen) > 1
    if current[0] and seen and current not in seen:
        changed = True
    return {
        "changed": changed,
        "variants": sorted([f"{a}:{b}" for a, b in seen]),
        "status": "SOURCE_CHANGED" if changed else "PASS",
    }


def localize_observations(rows):
    localized = []
    for row in rows:
        station = str(row.get("station") or "").strip().upper()
        ts = str(row.get("observation_time") or "")
        temp = f(row.get("temperature_c"))
        if not station or not ts or temp is None:
            continue
        try:
            text = ts
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            zone = ZoneInfo(STATION_TIMEZONES.get(station, "UTC"))
            local_dt = dt.astimezone(zone)
        except Exception:
            continue
        localized.append({
            "station": station,
            "local_dt": local_dt,
            "local_date": local_dt.date().isoformat(),
            "local_hour": local_dt.hour,
            "temp_c": temp,
        })
    localized.sort(key=lambda x: x["local_dt"])
    return localized


def observation_lock_for_market(market, observations, lock_table):
    m = normalize_market(market)
    station = str(m.get("resolution_station") or "").upper().strip()
    date = str(m.get("market_date") or "")[:10]
    mtype = str(m.get("market_type") or "")
    if not LOCK_ENABLED or not station or not date:
        return None

    rows = [
        x for x in observations
        if x["station"] == station and x["local_date"] == date
    ]
    if len(rows) < LOCK_LOOKBACK_OBS:
        return None

    latest = rows[-1]
    local_hour = latest["local_dt"].hour
    if local_hour < LOCK_MIN_LOCAL_HOUR:
        return None

    if mtype not in ("highest_temperature", "lowest_temperature"):
        return None

    temps = [x["temp_c"] for x in rows]
    if mtype == "highest_temperature":
        running = max(temps)
        last = rows[-LOCK_LOOKBACK_OBS:]
        cooling = all(x["temp_c"] <= running - LOCK_COOLING_GAP_C for x in last)
        max_points = [x for x in rows if abs(x["temp_c"] - running) < 1e-9]
        extreme_dt = max_points[-1]["local_dt"]
        age_hours = (latest["local_dt"] - extreme_dt).total_seconds() / 3600.0
        if not cooling or age_hours < LOCK_MIN_MAX_AGE_HOURS:
            return None
    else:
        running = min(temps)
        last = rows[-LOCK_LOOKBACK_OBS:]
        cooling = all(x["temp_c"] >= running + LOCK_COOLING_GAP_C for x in last)
        min_points = [x for x in rows if abs(x["temp_c"] - running) < 1e-9]
        extreme_dt = min_points[-1]["local_dt"]
        age_hours = (latest["local_dt"] - extreme_dt).total_seconds() / 3600.0
        if not cooling or age_hours < LOCK_MIN_MAX_AGE_HOURS:
            return None

    value = f(m.get("bucket_value"))
    low = f(m.get("bucket_low"))
    high = f(m.get("bucket_high"))

    if mtype == "highest_temperature":
        locked_value = round(running)
    else:
        locked_value = round(running)

    belongs = False
    if m.get("bucket_type") == "exact" and value is not None:
        belongs = round(value) == locked_value
    elif m.get("bucket_type") == "range" and low is not None and high is not None:
        belongs = low <= locked_value <= high
    elif m.get("bucket_type") == "or_lower" and value is not None:
        belongs = locked_value <= value
    elif m.get("bucket_type") == "or_higher" and value is not None:
        belongs = locked_value >= value

    if not belongs:
        return None

    ask = f(m.get("best_ask"))
    if ask is None or not 0 < ask < 1:
        return None

    gap = (running - latest["temp_c"]) if mtype == "highest_temperature" else (latest["temp_c"] - running)
    empirical = lookup_lock(lock_table, station, local_hour, gap, "high" if mtype == "highest_temperature" else "low")
    if not empirical:
        return None
    lock_probability = empirical["probability_lower95"]
    if lock_probability <= ask:
        return None
    edge = lock_probability - ask
    fee = fee_per_share(ask, DEFAULT_WEATHER_FEE_RATE)
    return {
        "locked_value_c": locked_value,
        "observed_extreme_c": running,
        "local_hour": local_hour,
        "extreme_age_hours": round(age_hours, 2),
        "latest_observation_c": latest["temp_c"],
        "lock_probability": lock_probability,
        "lock_probability_mean": empirical["probability_mean"],
        "lock_probability_lower95": empirical["probability_lower95"],
        "lock_history_n": empirical["n"],
        "lock_history_days": empirical["days"],
        "ask": ask,
        "gross_edge": round(edge, 6),
        "net_ev_per_share": round(edge - fee, 6),
        "tradeable_price": ask >= MIN_ENTRY_PRICE and ask <= LOCK_MAX_ASK,
        "signal": "OBS_LOCK_CANDIDATE" if edge > 0 and ask >= MIN_ENTRY_PRICE and ask <= LOCK_MAX_ASK else "OBS_LOCK_WATCH",
        "reason": "empirical_observation_lock_lower95",
    }


def price_bucket(ask):
    if ask is None:
        return "unknown"
    if ask < 0.02:
        return "microprice_<2c"
    if ask < 0.05:
        return "low_price_2-5c"
    return "normal_price_>=5c"



def load_clob_books():
    if not os.path.exists(CLOB_FILE):
        return {}
    try:
        return read_json(CLOB_FILE).get("markets", {})
    except Exception:
        return {}

def build_v18_signals(markets, forecasts, groups, residuals, source_variants, observations, lock_table, clob_books, cal_global, cal_by_type):
    families = defaultdict(list)
    for market in markets:
        key = market.get("event_key")
        if key:
            families[key].append(market)

    signals = []
    counters = defaultdict(int)

    for _, family in families.items():
        first = family[0]
        station = first.get("resolution_station")
        date = str(first.get("market_date") or "")[:10]
        market_type = first.get("market_type")
        if not station or not date:
            counters["skipped_no_station"] += len(family)
            continue

        stats = model_stats(forecasts, station, date, market_type, groups, residuals)
        if not stats:
            counters["skipped_no_forecast"] += len(family)
            continue

        # Match v1.7 family normalization behavior using the first calibrated entry.
        family_value = None
        exact = []
        for market in family:
            m = normalize_market(market)
            if m.get("bucket_type") not in ("exact", "range"):
                continue
            p = empirical_probability(
                m.get("bucket_type"),
                f(m.get("bucket_value")),
                f(m.get("bucket_low")),
                f(m.get("bucket_high")),
                stats["entries"][0]["forecast"],
                stats["entries"][0]["errors"],
            )
            if p is not None:
                exact.append(p)
        if exact:
            family_value = sum(exact)

        if family_value is None:
            family_status = "NO_EXCLUSIVE_BUCKETS"
        elif family_value < 0.90:
            family_status = "INCOMPLETE"
        elif family_value > 1.05:
            family_status = "OVERLAP_OR_PARSE_ERROR"
        else:
            family_status = "PASS"

        for market in family:
            m = normalize_market(market)
            guard = settlement_guard_for_market(market, source_variants)
            lock = observation_lock_for_market(market, observations, lock_table)

            gamma_ask = f(m.get("best_ask"))
            book = clob_books.get(str(m.get("market_id") or ""), {})
            clob_ask = f(book.get("best_ask"))
            clob_bid = f(book.get("best_bid"))
            clob_depth = f(book.get("depth_ask_at_best")) or 0.0
            ask = clob_ask if clob_ask is not None else gamma_ask
            execution_source = "clob_book" if clob_ask is not None else "gamma_reference"
            if ask is None or not 0 < ask < 1:
                continue

            raw_p = empirical_probability(
                m.get("bucket_type"),
                f(m.get("bucket_value")),
                f(m.get("bucket_low")),
                f(m.get("bucket_high")),
                stats["entries"][0]["forecast"],
                stats["entries"][0]["errors"],
            )
            if raw_p is None:
                continue

            normalized_raw_p = raw_p
            if m.get("bucket_type") in ("exact", "range") and family_value and family_value > 0:
                normalized_raw_p = max(0.0, min(1.0, raw_p / family_value))

            params = cal_by_type.get(str(market_type), cal_global)
            cal_p = calibrated_probability(normalized_raw_p, params)
            alpha = params.get("slope", 1.0) if params.get("ready") else 1.0
            fee = fee_per_share(ask, DEFAULT_WEATHER_FEE_RATE)
            edge = cal_p - ask
            ev = edge - fee
            raw_edge = normalized_raw_p - ask
            pbucket = price_bucket(ask)

            reasons = []
            signal = "NO_TRADE"
            settlement_guard = guard["status"]

            if ask < MIN_ENTRY_PRICE:
                reasons.append("microprice_excluded")
                counters["excluded_microprice"] += 1
            if raw_edge > MAX_RAW_EDGE:
                reasons.append("raw_edge_too_large")
                counters["excluded_raw_edge"] += 1
            if edge > MAX_CALIBRATED_EDGE:
                reasons.append("calibrated_edge_too_large")
                counters["excluded_cal_edge"] += 1
            if guard["changed"]:
                reasons.append("settlement_source_changed")
                counters["excluded_source_change"] += 1
            if m.get("bucket_type") in ("exact", "range") and family_status != "PASS":
                reasons.append("family=" + family_status)
            if edge < MIN_EDGE:
                reasons.append("edge_below_min")
            if ev < MIN_EV:
                reasons.append("ev_below_min")

            strong = (
                ask >= MIN_ENTRY_PRICE and
                not guard["changed"] and
                execution_source == "clob_book" and
                clob_depth >= 10.0 / max(ask, 0.0001) and
                raw_edge <= MAX_RAW_EDGE and
                edge <= MAX_CALIBRATED_EDGE and
                edge >= STRONG_MIN_EDGE and
                ev >= STRONG_MIN_EV and
                stats["samples"] >= MIN_CALIBRATION_SAMPLES and
                (m.get("bucket_type") not in ("exact", "range") or family_status == "PASS")
            )

            if strong:
                signal = "PAPER_BUY_V18"
                counters["paper_buy_v18"] += 1
            elif edge >= MIN_EDGE and ev >= MIN_EV and ask >= MIN_ENTRY_PRICE and not guard["changed"]:
                signal = "POSSIBLE_EDGE_V18"

            row = {
                "run_at": now_iso(),
                "city": m.get("city"),
                "station": m.get("resolution_station"),
                "market_date": m.get("market_date"),
                "market_type": market_type,
                "market_id": m.get("market_id"),
                "event_key": m.get("event_key"),
                "bucket_type": m.get("bucket_type"),
                "bucket_value": m.get("bucket_value"),
                "bucket_low": m.get("bucket_low"),
                "bucket_high": m.get("bucket_high"),
                "probability_method": "empirical_station_lead + walkforward_shrink_shadow",
                "raw_model_probability": r(normalized_raw_p),
                "shrunken_model_probability": r(cal_p),
                "market_probability": r(f(m.get("yes_price"))),
                "entry_price": r(ask),
                "entry_source": execution_source,
                "execution_source": execution_source,
                "clob_best_ask": r(clob_ask),
                "clob_best_bid": r(clob_bid),
                "clob_best_ask_depth": r(clob_depth, 6),
                "fee_rate": DEFAULT_WEATHER_FEE_RATE,
                "fee_per_share": r(fee, 8),
                "gross_edge": r(edge),
                "net_ev_per_share": r(ev),
                "forecast_count": len(stats["entries"]),
                "forecast_models": ",".join(stats["models"]),
                "forecast_mean_c": r(stats["forecast"], 4),
                "calibration_sigma_c": r(stats["sigma"], 4),
                "calibration_source": "station_model_lead_empirical",
                "calibration_samples": stats["samples"],
                "calibration_bias_c": r(stats["bias"], 4),
                "calibration_lead_days": ",".join(str(x) for x in stats["lead_days"]),
                "family_probability_sum": r(family_value),
                "family_status": family_status,
                "settlement_guard": settlement_guard,
                "source_variants": "|".join(guard["variants"]),
                "source_changed": guard["changed"],
                "price_bucket": pbucket,
                "signal": signal,
                "reason": "; ".join(reasons) or "v18_thresholds_pass",
                "shrink_alpha": r(alpha, 4),
            }

            if lock:
                row["observation_lock"] = lock
                if not guard["changed"]:
                    signals.append({
                        **row,
                        "signal": lock["signal"],
                        "reason": lock["reason"],
                        "observation_lock": lock,
                    })
                    if lock["signal"] == "OBS_LOCK_CANDIDATE":
                        counters["observation_lock_candidates"] += 1

            if signal in ("PAPER_BUY_V18", "POSSIBLE_EDGE_V18"):
                signals.append(row)

    rank = {
        "OBS_LOCK_CANDIDATE": 4,
        "PAPER_BUY_V18": 3,
        "POSSIBLE_EDGE_V18": 1,
        "OBS_LOCK_WATCH": 1,
        "NO_TRADE": 0,
    }
    signals.sort(
        key=lambda x: (
            rank.get(x.get("signal"), 0),
            f(x.get("net_ev_per_share")) or -9,
            f(x.get("gross_edge")) or -9,
        ),
        reverse=True,
    )
    return signals, counters


def main():
    os.makedirs(VALIDATION_DIR, exist_ok=True)

    run_at = now_iso()
    markets = read_json(ACTIVE_FILE).get("markets", [])
    forecasts = latest_forecasts(read_csv(FORECAST_FILE))
    groups, usable_count, rejected_sanity = load_calibration_groups()
    residuals = load_residuals(groups)

    paper_trades = closed_paper_trades()
    cal_global = calibration_params(paper_trades)
    cal_by_type = {}
    for market_type in sorted({x.get("market_type") for x in paper_trades if x.get("market_type")}):
        params = calibration_params(paper_trades, market_type=market_type)
        if params.get("ready"):
            cal_by_type[market_type] = params
    walkforward = walkforward_calibration_diagnostics(paper_trades)

    history = load_source_history()
    variants = source_guard(history)
    raw_observations = read_csv(OBS_FILE)
    observations = localize_observations(raw_observations)
    lock_table = fit_lock_table(raw_observations, STATION_TIMEZONES)
    clob_books = load_clob_books()

    signals, counters = build_v18_signals(
        markets,
        forecasts,
        groups,
        residuals,
        variants,
        observations,
        lock_table,
        clob_books,
        cal_global,
        cal_by_type,
    )

    paper_v18 = [x for x in signals if x.get("signal") == "PAPER_BUY_V18"]
    locks = [x for x in signals if x.get("signal") == "OBS_LOCK_CANDIDATE"]

    v17_payload = read_json(V17_FILE) if os.path.exists(V17_FILE) else {}
    v17_paper = len(v17_payload.get("signals", [])) if v17_payload else 0
    v17_paper = sum(1 for x in v17_payload.get("signals", []) if x.get("signal") == "PAPER_BUY")

    comparison = {
        "engine_version": ENGINE_VERSION,
        "run_at": run_at,
        "no_orders_sent": True,
        "v17_paper_buy_candidates": v17_paper,
        "v18_paper_buy_candidates": len(paper_v18),
        "v18_observation_lock_candidates": len(locks),
        "calibration_groups_accepted": usable_count,
        "calibration_groups_rejected_sanity": rejected_sanity,
        "shrinkage": {
            "n_closed_paper_trades": len(paper_trades),
            "global_calibration": cal_global,
            "calibration_by_market_type": cal_by_type,
            "walkforward": walkforward,
        },
        "counters": dict(counters),
        "guards": {
            "min_entry_price": MIN_ENTRY_PRICE,
            "max_raw_edge": MAX_RAW_EDGE,
            "max_calibrated_edge": MAX_CALIBRATED_EDGE,
            "microprice_cutoff": MICROPRICE_MAX,
            "settlement_source_guard": True,
        },
        "observation_lock": {
            "enabled": LOCK_ENABLED,
            "empirical_cells": len(lock_table),
            "method": "empirical station/hour/gap lower95",
            "min_local_hour": LOCK_MIN_LOCAL_HOUR,
            "min_extreme_age_hours": LOCK_MIN_MAX_AGE_HOURS,
            "cooling_gap_c": LOCK_COOLING_GAP_C,
            "legacy_probability_parameter": LOCK_PROBABILITY,
        },
    }
    write_json(COMPARISON_FILE, comparison)

    payload = {
        "engine_version": ENGINE_VERSION,
        "run_at": run_at,
        "mode": "shadow_validation",
        "no_orders_sent": True,
        "probability_method": "empirical_station_lead + shrinkage",
        "calibration_groups_accepted": usable_count,
        "calibration_groups_rejected_sanity": rejected_sanity,
        "calibration_global": cal_global,
        "calibration_by_market_type": cal_by_type,
        "walkforward_calibration_diagnostics": walkforward,
        "signals_evaluated": len(signals),
        "paper_buy_v18": len(paper_v18),
        "observation_lock_candidates": len(locks),
        "signals": signals[:100],
    }
    write_json(LATEST_FILE, payload)

    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    exists = os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as h:
        writer = csv.DictWriter(h, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for row in signals[:100]:
            writer.writerow(row)

    with open(REPORT_FILE, "w", encoding="utf-8") as h:
        h.write(f"POLYMARKET WEATHER EDGE ENGINE V{ENGINE_VERSION}\n")
        h.write("SHADOW VALIDATION — NO ORDERS SENT\n")
        h.write(f"UTC: {run_at}\n\n")
        h.write(f"V1.7 PAPER_BUY snapshot: {v17_paper}\n")
        h.write(f"V1.8 PAPER_BUY_V18: {len(paper_v18)}\n")
        h.write(f"Observation-lock candidates: {len(locks)}\n")
        h.write(f"Global calibration: {json.dumps(cal_global, sort_keys=True)}\n")
        if cal_by_type:
            h.write(f"Calibration by type: {json.dumps(cal_by_type, sort_keys=True)}\n")
        h.write(f"Walk-forward calibration diagnostics: {json.dumps(walkforward, sort_keys=True)}\n\n")
        h.write("GUARDS\n")
        h.write(f"min_entry_price={MIN_ENTRY_PRICE:.3f}\n")
        h.write(f"max_raw_edge={MAX_RAW_EDGE:.2f}\n")
        h.write(f"max_calibrated_edge={MAX_CALIBRATED_EDGE:.2f}\n")
        h.write(f"microprice_cutoff<{MICROPRICE_MAX:.2f}\n")
        h.write("settlement_source_guard=enabled\n\n")
        h.write("TOP V1.8 SHADOW SIGNALS\n")
        for s in signals[:50]:
            h.write(
                f"{s.get('signal')}|{s.get('city')}|{s.get('market_date')}|"
                f"p_raw={s.get('raw_model_probability')}|p_cal={s.get('shrunken_model_probability')}|"
                f"ask={s.get('entry_price')}|edge={s.get('gross_edge')}|"
                f"alpha={s.get('shrink_alpha')}|reason={s.get('reason')}\n"
            )

    print("=" * 72)
    print(f"POLYMARKET WEATHER EDGE ENGINE V{ENGINE_VERSION}")
    print("SHADOW VALIDATION — NO ORDERS SENT")
    print("=" * 72)
    print(f"V1.7 PAPER_BUY candidates: {v17_paper}")
    print(f"V1.8 PAPER_BUY_V18 candidates: {len(paper_v18)}")
    print(f"Observation-lock candidates: {len(locks)}")
    print(f"Global calibration: {cal_global}")
    print(f"Closed paper trades used for shrink fit: {len(paper_trades)}")
    print(f"Walk-forward scored observations: {walkforward.get('scored_oos', 0)}")
    print(f"Accepted calibration groups: {usable_count}")
    print(f"Excluded microprice: {counters.get('excluded_microprice', 0)}")
    print(f"Excluded source-change: {counters.get('excluded_source_change', 0)}")
    print(f"Excluded oversized edge: {counters.get('excluded_cal_edge', 0) + counters.get('excluded_raw_edge', 0)}")

if __name__ == "__main__":
    main()

import math
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MIN_HISTORY_DAYS = 30
HOUR_BIN = 1
GAP_BINS = (0.0, 1.0, 2.0, 99.0)


def _dt(value):
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _gap_bin(gap):
    if gap <= 0.0:
        return "0"
    if gap < 1.0:
        return "0-1"
    if gap < 2.0:
        return "1-2"
    return "2+"


def build_history(observations, station_timezones):
    days = defaultdict(list)
    for row in observations:
        station = str(row.get("station") or "").strip().upper()
        ts = _dt(row.get("observation_time"))
        try:
            temp = float(row.get("temperature_c"))
        except (TypeError, ValueError):
            continue
        if not station or ts is None:
            continue
        zone = ZoneInfo(station_timezones.get(station, "UTC"))
        local = ts.astimezone(zone)
        days[(station, local.date().isoformat())].append((local, temp))

    records = []
    for (station, local_date), rows in days.items():
        rows.sort()
        final_max = max(x[1] for x in rows)
        final_min = min(x[1] for x in rows)
        running_max = -float("inf")
        running_min = float("inf")
        for local_dt, temp in rows:
            running_max = max(running_max, temp)
            running_min = min(running_min, temp)
            records.append({
                "station": station,
                "local_date": local_date,
                "local_hour": local_dt.hour,
                "temp": temp,
                "running_max": running_max,
                "running_min": running_min,
                "gap_high": max(0.0, final_max - running_max),
                "gap_low": max(0.0, running_min - final_min),
                "high_locked": final_max <= running_max + 1e-9,
                "low_locked": final_min >= running_min - 1e-9,
            })
    return records


def fit_lock_table(observations, station_timezones):
    history = build_history(observations, station_timezones)
    cells = defaultdict(lambda: {"locked": 0, "total": 0, "dates": set()})
    for r in history:
        # Fit using the actual state at that hour; outcome is whether the final
        # daily extreme was already fixed. No forecast or market data is used.
        for side, gap in (("high", r["gap_high"]), ("low", r["gap_low"])):
            key = (r["station"], r["local_hour"], _gap_bin(gap), side)
            cells[key]["locked"] += int(r[f"{side}_locked"])
            cells[key]["total"] += 1
            cells[key]["dates"].add(r["local_date"])

    table = {}
    for key, cell in cells.items():
        n = cell["total"]
        days = len(cell["dates"])
        if days < MIN_HISTORY_DAYS:
            continue
        hits = cell["locked"]
        # Jeffreys posterior mean; then a conservative normal lower bound.
        mean = (hits + 0.5) / (n + 1.0)
        variance = mean * (1.0 - mean) / (n + 2.0)
        lower = max(0.0, mean - 1.96 * math.sqrt(variance))
        table[key] = {
            "probability_mean": round(mean, 6),
            "probability_lower95": round(lower, 6),
            "n": n,
            "days": days,
        }
    return table


def lookup_lock(table, station, local_hour, gap, side):
    candidates = [
        (str(station).upper(), int(local_hour), _gap_bin(gap), side),
        (str(station).upper(), int(local_hour), "2+", side),
    ]
    for key in candidates:
        if key in table:
            return table[key]
    # Station-specific data can be sparse; use no fallback probability rather
    # than silently pooling unrelated stations.
    return None

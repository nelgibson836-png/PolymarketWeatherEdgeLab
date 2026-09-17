import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

DATA_DIR = "data"
PAPER_DIR = os.path.join(DATA_DIR, "edge", "paper")
VALIDATION_DIR = os.path.join(DATA_DIR, "edge", "validation")
TRADES_FILE = os.path.join(PAPER_DIR, "trades.csv")
SUMMARY_FILE = os.path.join(PAPER_DIR, "summary.json")
LATEST_V17_FILE = os.path.join(DATA_DIR, "edge", "latest_signals_v17.json")
ANALYSIS_JSON = os.path.join(VALIDATION_DIR, "latest_paper_analysis.json")
ANALYSIS_TXT = os.path.join(VALIDATION_DIR, "latest_paper_analysis.txt")
BINS_FILE = os.path.join(VALIDATION_DIR, "probability_bins.csv")
GROUPS_FILE = os.path.join(VALIDATION_DIR, "group_metrics.csv")

PROB_BINS = [
    (0.00, 0.05, "00-05%"),
    (0.05, 0.10, "05-10%"),
    (0.10, 0.20, "10-20%"),
    (0.20, 0.30, "20-30%"),
    (0.30, 0.50, "30-50%"),
    (0.50, 0.75, "50-75%"),
    (0.75, 1.01, "75-100%"),
]

PRICE_BINS = [
    (0.00, 0.01, "00-01c"),
    (0.01, 0.02, "01-02c"),
    (0.02, 0.05, "02-05c"),
    (0.05, 1.00, "05-100c"),
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def f(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def write_csv(path, fields, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def bin_for(value, bins):
    if value is None:
        return "unknown"
    for low, high, label in bins:
        if low <= value < high:
            return label
    return bins[-1][2]


def safe_log_loss(probability, outcome):
    p = min(1.0 - 1e-9, max(1e-9, probability))
    return -(outcome * math.log(p) + (1.0 - outcome) * math.log(1.0 - p))


def realized_drawdown(closed_trades):
    ordered = sorted(
        closed_trades,
        key=lambda row: str(row.get("resolved_at") or row.get("opened_at") or ""),
    )
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    for row in ordered:
        equity += f(row.get("pnl")) or 0.0
        peak = max(peak, equity)
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, drawdown / peak)
    return max_drawdown, max_drawdown_pct


def aggregate(rows, key_name):
    grouped = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key_name) or "unknown")].append(row)
    result = []
    for key, group in sorted(grouped.items()):
        closed = [r for r in group if r.get("status") == "CLOSED" and r.get("result") in ("WIN", "LOSS")]
        wins = sum(1 for r in closed if r.get("result") == "WIN")
        pnl = sum(f(r.get("pnl")) or 0.0 for r in closed)
        stake = sum(f(r.get("stake")) or 0.0 for r in closed)
        result.append({
            "group": key,
            "n": len(group),
            "closed": len(closed),
            "wins": wins,
            "win_rate": (wins / len(closed)) if closed else None,
            "pnl": pnl,
            "roi": (pnl / stake) if stake else None,
            "mean_probability": (sum(f(r.get("model_probability")) or 0.0 for r in closed) / len(closed)) if closed else None,
            "mean_entry_price": (sum(f(r.get("entry_price")) or 0.0 for r in closed) / len(closed)) if closed else None,
        })
    return result


def analyze_trade_ledger(trades):
    closed = [
        row for row in trades
        if row.get("status") == "CLOSED" and row.get("result") in ("WIN", "LOSS")
        and f(row.get("model_probability")) is not None
    ]
    open_trades = [row for row in trades if row.get("status") == "OPEN"]

    outcomes = [1.0 if row.get("result") == "WIN" else 0.0 for row in closed]
    probabilities = [f(row.get("model_probability")) for row in closed]
    total_p = sum(probabilities)
    wins = sum(outcomes)
    n = len(closed)
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / n if n else None
    log_loss = sum(safe_log_loss(p, y) for p, y in zip(probabilities, outcomes)) / n if n else None
    mean_p = total_p / n if n else None
    expected_gap = wins - total_p if n else None
    pnl = sum(f(row.get("pnl")) or 0.0 for row in closed)
    stake = sum(f(row.get("stake")) or 0.0 for row in closed)
    max_dd, max_dd_pct = realized_drawdown(closed)

    bins = []
    for low, high, label in PROB_BINS:
        group = [row for row in closed if low <= (f(row.get("model_probability")) or 0.0) < high]
        group_wins = sum(1 for row in group if row.get("result") == "WIN")
        group_p = [f(row.get("model_probability")) or 0.0 for row in group]
        group_pnl = sum(f(row.get("pnl")) or 0.0 for row in group)
        bins.append({
            "bin": label,
            "lower": low,
            "upper": high,
            "n": len(group),
            "mean_probability": (sum(group_p) / len(group_p)) if group_p else None,
            "actual_win_rate": (group_wins / len(group)) if group else None,
            "calibration_error": ((group_wins / len(group)) - (sum(group_p) / len(group))) if group else None,
            "pnl": group_pnl,
        })

    price_groups = []
    for low, high, label in PRICE_BINS:
        group = [row for row in closed if low <= (f(row.get("entry_price")) or 0.0) < high]
        group_wins = sum(1 for row in group if row.get("result") == "WIN")
        group_pnl = sum(f(row.get("pnl")) or 0.0 for row in group)
        price_groups.append({
            "bin": label,
            "lower": low,
            "upper": high,
            "n": len(group),
            "wins": group_wins,
            "win_rate": (group_wins / len(group)) if group else None,
            "pnl": group_pnl,
        })

    tiny = [row for row in closed if (f(row.get("entry_price")) or 0.0) <= 0.02]
    tiny_pnl = sum(f(row.get("pnl")) or 0.0 for row in tiny)
    tiny_positive_share = (tiny_pnl / pnl) if pnl > 0 else None

    cumulative = [row for row in closed if row.get("bucket_type") in ("or_lower", "or_higher")]
    exclusive = [row for row in closed if row.get("bucket_type") in ("exact", "range")]

    summary = {
        "updated_at": now_iso(),
        "closed_trades": n,
        "open_trades": len(open_trades),
        "wins": int(wins),
        "losses": n - int(wins),
        "observed_win_rate": (wins / n) if n else None,
        "mean_model_probability": mean_p,
        "expected_wins_from_model": total_p if n else None,
        "actual_minus_expected_wins": expected_gap,
        "brier_score": brier,
        "log_loss": log_loss,
        "closed_pnl": pnl,
        "closed_stake": stake,
        "closed_roi": (pnl / stake) if stake else None,
        "max_realized_drawdown": max_dd,
        "max_realized_drawdown_pct_of_prior_peak": max_dd_pct,
        "trades_entry_price_le_0_02": len(tiny),
        "pnl_entry_price_le_0_02": tiny_pnl,
        "share_of_positive_pnl_from_le_0_02": tiny_positive_share,
        "cumulative_bucket_closed": len(cumulative),
        "cumulative_bucket_win_rate": (sum(1 for r in cumulative if r.get("result") == "WIN") / len(cumulative)) if cumulative else None,
        "cumulative_bucket_pnl": sum(f(r.get("pnl")) or 0.0 for r in cumulative),
        "exclusive_bucket_closed": len(exclusive),
        "exclusive_bucket_win_rate": (sum(1 for r in exclusive if r.get("result") == "WIN") / len(exclusive)) if exclusive else None,
        "exclusive_bucket_pnl": sum(f(r.get("pnl")) or 0.0 for r in exclusive),
        "probability_bins": bins,
        "price_bins": price_groups,
    }

    return summary


def analyze_v17():
    payload = read_json(LATEST_V17_FILE, {})
    signals = payload.get("signals", []) if isinstance(payload, dict) else []
    paper = [s for s in signals if s.get("signal") == "PAPER_BUY"]
    cumulative = [s for s in paper if s.get("bucket_type") in ("or_lower", "or_higher")]
    incomplete = [s for s in paper if s.get("family_status") not in (None, "PASS") and s.get("bucket_type") in ("exact", "range")]
    thin = [s for s in paper if s.get("execution_quality") == "THIN_ASK"]
    high_p = [s for s in paper if (f(s.get("model_probability")) or 0.0) >= 0.50]
    return {
        "engine_version": payload.get("engine_version"),
        "run_at": payload.get("run_at"),
        "signals_evaluated": payload.get("signals_evaluated"),
        "signals_retained": payload.get("signals_retained"),
        "paper_buy_candidates": len(paper),
        "paper_buy_cumulative": len(cumulative),
        "paper_buy_exact_or_range": len(paper) - len(cumulative),
        "paper_buy_exact_or_range_not_pass": len(incomplete),
        "paper_buy_thin_ask": len(thin),
        "paper_buy_probability_ge_50pct": len(high_p),
        "max_paper_buy_probability": max((f(s.get("model_probability")) or 0.0 for s in paper), default=None),
        "mean_paper_buy_probability": (sum(f(s.get("model_probability")) or 0.0 for s in paper) / len(paper)) if paper else None,
    }


def fmt(value):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def main():
    print("=" * 72)
    print("POLYMARKET WEATHER PAPER TRIAL ANALYSIS")
    print("RETROSPECTIVE DIAGNOSTICS — NO ORDERS")
    print("=" * 72)

    trades = read_csv(TRADES_FILE)
    summary = read_json(SUMMARY_FILE, {})
    ledger = analyze_trade_ledger(trades)
    v17 = analyze_v17()

    payload = {
        "generated_at": now_iso(),
        "trial_state": summary,
        "ledger": ledger,
        "v17_shadow": v17,
        "interpretation": {
            "profitability_not_established": True,
            "reason": "Positive P&L is reported separately from calibration quality; rare low-price wins can dominate P&L.",
            "live_ready": False,
        },
    }
    write_json(ANALYSIS_JSON, payload)

    bin_rows = ledger.get("probability_bins", [])
    write_csv(
        BINS_FILE,
        ["bin", "lower", "upper", "n", "mean_probability", "actual_win_rate", "calibration_error", "pnl"],
        bin_rows,
    )

    group_rows = []
    group_rows.extend({"dimension": "bucket_type", **row} for row in aggregate([r for r in trades if r.get("status") == "CLOSED"], "bucket_type"))
    group_rows.extend({"dimension": "market_type", **row} for row in aggregate([r for r in trades if r.get("status") == "CLOSED"], "market_type"))
    group_rows.extend({"dimension": "city", **row} for row in aggregate([r for r in trades if r.get("status") == "CLOSED"], "city"))
    write_csv(
        GROUPS_FILE,
        ["dimension", "group", "n", "closed", "wins", "win_rate", "pnl", "roi", "mean_probability", "mean_entry_price"],
        group_rows,
    )

    lines = [
        "POLYMARKET WEATHER PAPER TRIAL ANALYSIS",
        f"Generated: {payload['generated_at']}",
        "",
        "CURRENT TRIAL",
        f"Trial start: {summary.get('trial_start', 'n/a')}",
        f"Trial end: {summary.get('trial_end', 'n/a')}",
        f"Closed trades: {fmt(ledger['closed_trades'])}",
        f"Open trades: {fmt(ledger['open_trades'])}",
        f"Observed win rate: {fmt(ledger['observed_win_rate'])}",
        f"Mean model probability: {fmt(ledger['mean_model_probability'])}",
        f"Expected wins from model: {fmt(ledger['expected_wins_from_model'])}",
        f"Actual minus expected wins: {fmt(ledger['actual_minus_expected_wins'])}",
        f"Brier score: {fmt(ledger['brier_score'])}",
        f"Log loss: {fmt(ledger['log_loss'])}",
        f"Closed P&L: {fmt(ledger['closed_pnl'])}",
        f"Closed ROI: {fmt(ledger['closed_roi'])}",
        f"Max realized drawdown: {fmt(ledger['max_realized_drawdown'])}",
        f"P&L from entry <= $0.02: {fmt(ledger['pnl_entry_price_le_0_02'])}",
        f"Share of positive P&L from entry <= $0.02: {fmt(ledger['share_of_positive_pnl_from_le_0_02'])}",
        "",
        "BUCKET TYPE",
        f"Cumulative buckets closed: {fmt(ledger['cumulative_bucket_closed'])}",
        f"Cumulative win rate: {fmt(ledger['cumulative_bucket_win_rate'])}",
        f"Cumulative P&L: {fmt(ledger['cumulative_bucket_pnl'])}",
        f"Exact/range closed: {fmt(ledger['exclusive_bucket_closed'])}",
        f"Exact/range win rate: {fmt(ledger['exclusive_bucket_win_rate'])}",
        f"Exact/range P&L: {fmt(ledger['exclusive_bucket_pnl'])}",
        "",
        "V1.7 SHADOW",
        f"Signals evaluated: {fmt(v17.get('signals_evaluated'))}",
        f"PAPER_BUY candidates: {fmt(v17.get('paper_buy_candidates'))}",
        f"PAPER_BUY cumulative: {fmt(v17.get('paper_buy_cumulative'))}",
        f"PAPER_BUY exact/range: {fmt(v17.get('paper_buy_exact_or_range'))}",
        f"Exact/range not PASS: {fmt(v17.get('paper_buy_exact_or_range_not_pass'))}",
        f"Thin asks: {fmt(v17.get('paper_buy_thin_ask'))}",
        f"PAPER_BUY p >= 50%: {fmt(v17.get('paper_buy_probability_ge_50pct'))}",
        f"Max PAPER_BUY probability: {fmt(v17.get('max_paper_buy_probability'))}",
        "",
        "DECISION NOTE",
        "Positive P&L alone is not treated as proof of predictive edge.",
        "The next strategy revision should require calibration evidence plus realistic execution assumptions.",
        "No live orders are enabled by this analysis script.",
        "",
        "PROBABILITY BINS",
    ]
    for row in ledger["probability_bins"]:
        lines.append(
            f"{row['bin']}: n={row['n']} mean_p={fmt(row['mean_probability'])} observed={fmt(row['actual_win_rate'])} error={fmt(row['calibration_error'])} pnl={fmt(row['pnl'])}"
        )

    os.makedirs(os.path.dirname(ANALYSIS_TXT), exist_ok=True)
    with open(ANALYSIS_TXT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    print(f"Closed trades: {ledger['closed_trades']}")
    print(f"Observed win rate: {fmt(ledger['observed_win_rate'])}")
    print(f"Mean model probability: {fmt(ledger['mean_model_probability'])}")
    print(f"Expected wins: {fmt(ledger['expected_wins_from_model'])}")
    print(f"Brier score: {fmt(ledger['brier_score'])}")
    print(f"Log loss: {fmt(ledger['log_loss'])}")
    print(f"Closed P&L: {fmt(ledger['closed_pnl'])}")
    print(f"V1.7 PAPER_BUY: {fmt(v17.get('paper_buy_candidates'))}")
    print(f"V1.7 cumulative PAPER_BUY: {fmt(v17.get('paper_buy_cumulative'))}")
    print("No live orders enabled.")


if __name__ == "__main__":
    main()

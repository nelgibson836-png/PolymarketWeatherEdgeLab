import csv
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests

DATA_DIR = "data"
EDGE_DIR = os.path.join(DATA_DIR, "edge")
PAPER_DIR = os.path.join(EDGE_DIR, "paper_v18")
SIGNAL_FILE = os.path.join(EDGE_DIR, "latest_signals_v18.json")
TRADES_FILE = os.path.join(PAPER_DIR, "trades.csv")
SUMMARY_FILE = os.path.join(PAPER_DIR, "summary.json")
REPORT_FILE = os.path.join(PAPER_DIR, "latest_report.txt")
STATE_FILE = os.path.join(PAPER_DIR, "trial_state.json")

POLYMARKET_API = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = 15

VIRTUAL_BANKROLL = 1000.0
STAKE_PER_TRADE = 10.0
MAX_OPEN_TRADES = 30
MAX_OPEN_PER_FAMILY = 1
MAX_OPEN_PER_CITY = 3
MAX_OPEN_PER_MARKET_DATE = 8
TRIAL_DAYS = 15

# Conservative sensitivity assumption only. Actual historical CLOB execution is not reconstructed.
EXTRA_SLIPPAGE = 0.005
SIGNAL_MAX_AGE_MINUTES = 20

FIELDS = [
    "trade_id","decision_key","engine_version","opened_at","market_id","event_key","city","station","market_date",
    "market_type","bucket_type","bucket_value","bucket_low","bucket_high","entry_price","execution_price",
    "shares","stake","fee_total","fee_per_share","slippage_per_share","model_probability","raw_model_probability",
    "gross_edge","net_ev_per_share","signal","status","resolved_at","result","payout_gross","payout_net",
    "pnl","roi","resolution_source","resolution_note"
]

def now():
    return datetime.now(timezone.utc)

def now_iso():
    return now().isoformat()

def parse_dt(value):
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

def f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None

def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))

def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)

def get_market(mid):
    r = requests.get(
        f"{POLYMARKET_API}/markets/{mid}",
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "PolymarketWeatherEdgeLab/1.8-paper"},
    )
    r.raise_for_status()
    return r.json()

def resolve_market(mid):
    try:
        market = get_market(mid)
    except Exception as exc:
        return None, None, f"api_error:{exc}"

    if not bool(market.get("closed")) and not bool(market.get("resolved")):
        return None, None, "still_open"

    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            prices = None

    if not isinstance(prices, list) or len(prices) < 2:
        return None, None, "closed_without_final_prices"

    values = [f(x) for x in prices]
    if any(x is None for x in values):
        return None, None, f"invalid_final_prices:{prices}"

    # Normal binary settlement only. Exceptional/disputed resolutions remain unresolved
    # until a future resolver can identify outcome tokens by identity.
    if values[0] == 1.0 and values[1] == 0.0:
        return "WIN", 1.0, "binary_yes_resolved"
    if values[0] == 0.0 and values[1] == 1.0:
        return "LOSS", 0.0, "binary_no_resolved"
    return None, None, f"non_binary_or_disputed:{prices}"

def load_state():
    os.makedirs(PAPER_DIR, exist_ok=True)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as h:
            state = json.load(h)
    else:
        start = now()
        state = {
            "start_at": start.isoformat(),
            "trial_days": TRIAL_DAYS,
            "engine_version": "1.8",
            "virtual_bankroll": VIRTUAL_BANKROLL,
        }
        with open(STATE_FILE, "w", encoding="utf-8") as h:
            json.dump(state, h, indent=2)
    start = parse_dt(state["start_at"])
    return start, start + timedelta(days=TRIAL_DAYS)

def decision_key(signal):
    raw = "|".join(str(signal.get(k) or "") for k in (
        "market_id", "market_date", "bucket_type", "bucket_value", "bucket_low",
        "bucket_high", "run_at", "signal"
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

def signal_is_fresh(signal):
    dt = parse_dt(signal.get("run_at"))
    if dt is None:
        return False
    age = (now() - dt).total_seconds() / 60.0
    return -5.0 <= age <= SIGNAL_MAX_AGE_MINUTES

def build_trade(signal, seq, free_cash):
    ask = f(signal.get("entry_price"))
    raw_p = f(signal.get("raw_model_probability"))
    calibrated_p = f(signal.get("shrunken_model_probability"))
    fee_per_share = f(signal.get("fee_per_share")) or 0.0

    if ask is None or not 0.0 < ask < 1.0:
        return None
    if calibrated_p is None or raw_p is None:
        return None
    if signal.get("execution_source") != "clob_book":
        return None

    execution = min(0.999999, ask + EXTRA_SLIPPAGE)
    budget = min(STAKE_PER_TRADE, free_cash)
    if budget <= 0.0:
        return None

    # Stake is the total cash budget, including estimated fee.
    denom = execution + max(0.0, fee_per_share)
    if denom <= 0:
        return None
    shares = budget / denom
    fee_total = shares * max(0.0, fee_per_share)
    gross_cost = shares * execution
    total_cash = gross_cost + fee_total

    if total_cash > free_cash + 1e-9:
        return None

    return {
        "trade_id": f"paper18-{seq:06d}",
        "decision_key": decision_key(signal),
        "engine_version": "1.8",
        "opened_at": signal.get("run_at") or now_iso(),
        "market_id": str(signal.get("market_id") or ""),
        "event_key": signal.get("event_key") or "",
        "city": signal.get("city") or "",
        "station": signal.get("station") or "",
        "market_date": signal.get("market_date") or "",
        "market_type": signal.get("market_type") or "",
        "bucket_type": signal.get("bucket_type") or "",
        "bucket_value": signal.get("bucket_value") or "",
        "bucket_low": signal.get("bucket_low") or "",
        "bucket_high": signal.get("bucket_high") or "",
        "entry_price": ask,
        "execution_price": execution,
        "shares": shares,
        "stake": total_cash,
        "fee_total": fee_total,
        "fee_per_share": fee_per_share,
        "slippage_per_share": EXTRA_SLIPPAGE,
        "model_probability": calibrated_p,
        "raw_model_probability": raw_p,
        "gross_edge": f(signal.get("gross_edge")) or "",
        "net_ev_per_share": f(signal.get("net_ev_per_share")) or "",
        "signal": signal.get("signal") or "PAPER_BUY_V18",
        "status": "OPEN",
        "resolved_at": "",
        "result": "",
        "payout_gross": "",
        "payout_net": "",
        "pnl": "",
        "roi": "",
        "resolution_source": "",
        "resolution_note": "",
    }

def metrics(trades, start, end, active):
    closed = [x for x in trades if x.get("status") == "CLOSED"]
    open_trades = [x for x in trades if x.get("status") == "OPEN"]
    wins = [x for x in closed if x.get("result") == "WIN"]
    realized_pnl = sum(f(x.get("pnl")) or 0.0 for x in closed)
    committed = sum(f(x.get("stake")) or 0.0 for x in open_trades)
    closed_stake = sum(f(x.get("stake")) or 0.0 for x in closed)
    free_cash = VIRTUAL_BANKROLL - committed

    return {
        "updated_at": now_iso(),
        "engine_version": "1.8",
        "trial_start": start.isoformat(),
        "trial_end": end.isoformat(),
        "trial_active": active,
        "virtual_bankroll_initial": VIRTUAL_BANKROLL,
        "stake_target": STAKE_PER_TRADE,
        "max_open_trades": MAX_OPEN_TRADES,
        "closed_trades": len(closed),
        "open_trades": len(open_trades),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate": len(wins) / len(closed) if closed else None,
        "closed_pnl_net": realized_pnl,
        "closed_roi_net": realized_pnl / closed_stake if closed_stake else None,
        "open_committed_cash": committed,
        "free_cash_before_new_orders": free_cash,
        "mark_to_unsettled": VIRTUAL_BANKROLL + realized_pnl - committed,
    }

def main():
    start, end = load_state()
    active = now() < end

    payload = {}
    if os.path.exists(SIGNAL_FILE):
        with open(SIGNAL_FILE, encoding="utf-8") as h:
            payload = json.load(h)

    signals = [
        s for s in payload.get("signals", [])
        if s.get("signal") == "PAPER_BUY_V18" and signal_is_fresh(s)
    ]

    trades = read_csv(TRADES_FILE)
    existing_keys = {str(x.get("decision_key") or "") for x in trades if x.get("decision_key")}
    processed_market_ids = {
        str(x.get("market_id") or "")
        for x in trades
        if x.get("status") in ("OPEN", "CLOSED")
    }

    # First resolve existing positions.
    for trade in trades:
        if trade.get("status") != "OPEN":
            continue
        result, payout, note = resolve_market(trade.get("market_id"))
        if result is None:
            continue

        shares = f(trade.get("shares")) or 0.0
        fee_total = f(trade.get("fee_total")) or 0.0
        gross_payout = shares * payout
        net_payout = max(0.0, gross_payout - fee_total)
        stake = f(trade.get("stake")) or 0.0
        net_pnl = net_payout - stake

        trade.update({
            "status": "CLOSED",
            "resolved_at": now_iso(),
            "result": result,
            "payout_gross": round(gross_payout, 8),
            "payout_net": round(net_payout, 8),
            "pnl": round(net_pnl, 8),
            "roi": round(net_pnl / stake, 8) if stake else "",
            "resolution_source": "Polymarket Gamma market endpoint",
            "resolution_note": note,
        })

    open_trades = [x for x in trades if x.get("status") == "OPEN"]
    committed = sum(f(x.get("stake")) or 0.0 for x in open_trades)
    free_cash = VIRTUAL_BANKROLL - committed

    fam = defaultdict(int)
    city = defaultdict(int)
    date = defaultdict(int)
    for trade in open_trades:
        fam[trade.get("event_key", "")] += 1
        city[trade.get("city", "")] += 1
        date[trade.get("market_date", "")] += 1

    candidates = sorted(
        signals,
        key=lambda x: f(x.get("net_ev_per_share")) or -9.0,
        reverse=True,
    )

    seq = len(trades) + 1
    added = 0

    if active:
        for signal in candidates:
            # Capacity is checked BEFORE constructing/adding a position.
            current_open = sum(1 for x in trades if x.get("status") == "OPEN")
            if current_open >= MAX_OPEN_TRADES:
                break

            key = decision_key(signal)
            mid = str(signal.get("market_id") or "")
            ek = signal.get("event_key", "")
            c = signal.get("city", "")
            d = signal.get("market_date", "")

            if not mid or key in existing_keys:
                continue

            # One paper entry per market. This avoids stale re-entry after a prior close.
            if mid in processed_market_ids:
                continue

            if fam[ek] >= MAX_OPEN_PER_FAMILY or city[c] >= MAX_OPEN_PER_CITY or date[d] >= MAX_OPEN_PER_MARKET_DATE:
                continue

            if free_cash < 0.01:
                break

            trade = build_trade(signal, seq, free_cash)
            if not trade:
                continue

            trades.append(trade)
            existing_keys.add(key)
            processed_market_ids.add(mid)
            fam[ek] += 1
            city[c] += 1
            date[d] += 1
            free_cash -= f(trade.get("stake")) or 0.0
            seq += 1
            added += 1

    write_csv(TRADES_FILE, trades)
    summary = metrics(trades, start, end, active)
    summary.update({
        "max_open_per_family": MAX_OPEN_PER_FAMILY,
        "max_open_per_city": MAX_OPEN_PER_CITY,
        "max_open_per_market_date": MAX_OPEN_PER_MARKET_DATE,
        "extra_slippage_assumption": EXTRA_SLIPPAGE,
        "signal_max_age_minutes": SIGNAL_MAX_AGE_MINUTES,
        "new_paper_trades_this_run": added,
        "execution_model": "whole-budget-fill-at-ask-plus-slippage; no historical CLOB depth",
        "live_ready": False,
    })

    with open(SUMMARY_FILE, "w", encoding="utf-8") as h:
        json.dump(summary, h, indent=2)

    with open(REPORT_FILE, "w", encoding="utf-8") as h:
        h.write("POLYMARKET WEATHER PAPER TRADER V1.8\n")
        h.write("VIRTUAL MONEY ONLY — NO ORDERS SENT\n")
        h.write("NET P&L INCLUDES RECORDED EXECUTION FEE AND SLIPPAGE ASSUMPTION\n\n")
        for key, value in summary.items():
            h.write(f"{key}: {value}\n")

    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()

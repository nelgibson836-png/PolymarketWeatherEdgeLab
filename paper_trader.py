import csv
import json
import os
from datetime import datetime, timezone

import requests

DATA_DIR = "data"
EDGE_DIR = os.path.join(DATA_DIR, "edge")
PAPER_DIR = os.path.join(EDGE_DIR, "paper")
SIGNAL_FILE = os.path.join(EDGE_DIR, "latest_signals_v16.json")
TRADES_FILE = os.path.join(PAPER_DIR, "trades.csv")
SUMMARY_FILE = os.path.join(PAPER_DIR, "summary.json")
REPORT_FILE = os.path.join(PAPER_DIR, "latest_report.txt")
POLYMARKET_API = "https://gamma-api.polymarket.com"
REQUEST_TIMEOUT = 15
VIRTUAL_BANKROLL = 1000.0
STAKE_PER_TRADE = 10.0
MAX_OPEN_TRADES = 50

FIELDS = [
    "trade_id", "opened_at", "market_id", "event_key", "city", "station", "market_date",
    "market_type", "bucket_type", "bucket_value", "bucket_low", "bucket_high",
    "entry_price", "shares", "stake", "model_probability", "gross_edge", "net_ev_per_share",
    "fee_rate", "fee_per_share", "signal", "status", "resolved_at", "result",
    "payout", "pnl", "roi", "resolution_source", "resolution_note"
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


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as h:
        return list(csv.DictReader(h))


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as h:
        writer = csv.DictWriter(h, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def append_csv(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as h:
        writer = csv.DictWriter(h, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def get_market(market_id):
    response = requests.get(
        f"{POLYMARKET_API}/markets/{market_id}",
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "PolymarketWeatherEdgeLab/1.0"},
    )
    response.raise_for_status()
    return response.json()


def parse_outcome_prices(market):
    value = market.get("outcomePrices")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if isinstance(value, list) and len(value) >= 2:
        return [f(value[0]), f(value[1])]
    return None


def resolve_market(market_id):
    try:
        market = get_market(market_id)
    except Exception as exc:
        return None, None, None, f"api_error: {exc}"

    prices = parse_outcome_prices(market)
    closed = bool(market.get("closed"))
    resolved = bool(market.get("resolved"))

    if not closed and not resolved:
        return None, None, market, "still_open"

    if not prices:
        return None, None, market, "closed_without_outcome_prices"

    yes_price, no_price = prices

    if yes_price == 1.0 and no_price == 0.0:
        return "WIN", 1.0, market, "yes_resolved"

    if yes_price == 0.0 and no_price == 1.0:
        return "LOSS", 0.0, market, "no_resolved"

    return None, None, market, f"unresolved_prices:{prices}"


def build_trade(signal, bankroll, sequence):
    price = f(signal.get("entry_price"))
    if price is None or price <= 0 or price >= 1:
        return None
    shares = STAKE_PER_TRADE / price
    return {
        "trade_id": f"paper-{sequence:06d}",
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
        "entry_price": price,
        "shares": shares,
        "stake": STAKE_PER_TRADE,
        "model_probability": signal.get("model_probability") or "",
        "gross_edge": signal.get("gross_edge") or "",
        "net_ev_per_share": signal.get("net_ev_per_share") or "",
        "fee_rate": signal.get("fee_rate") or "",
        "fee_per_share": signal.get("fee_per_share") or "",
        "signal": "PAPER_BUY",
        "status": "OPEN",
        "resolved_at": "",
        "result": "",
        "payout": "",
        "pnl": "",
        "roi": "",
        "resolution_source": "",
        "resolution_note": "",
    }


def calculate_metrics(trades):
    closed = [x for x in trades if x.get("status") == "CLOSED"]
    wins = [x for x in closed if x.get("result") == "WIN"]
    losses = [x for x in closed if x.get("result") == "LOSS"]
    pnl = sum(f(x.get("pnl")) or 0.0 for x in closed)
    staked = sum(f(x.get("stake")) or 0.0 for x in closed)
    open_stake = sum(f(x.get("stake")) or 0.0 for x in trades if x.get("status") == "OPEN")
    return {
        "updated_at": now_iso(),
        "virtual_bankroll_initial": VIRTUAL_BANKROLL,
        "stake_per_trade": STAKE_PER_TRADE,
        "max_open_trades": MAX_OPEN_TRADES,
        "total_trades": len(trades),
        "open_trades": sum(1 for x in trades if x.get("status") == "OPEN"),
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(closed)) if closed else None,
        "closed_pnl": pnl,
        "closed_roi": (pnl / staked) if staked else None,
        "open_stake": open_stake,
        "virtual_equity_after_closed": VIRTUAL_BANKROLL + pnl,
        "capacity_remaining": max(0, MAX_OPEN_TRADES - sum(1 for x in trades if x.get("status") == "OPEN")),
    }


def main():
    os.makedirs(PAPER_DIR, exist_ok=True)
    signals_payload = {}
    if os.path.exists(SIGNAL_FILE):
        with open(SIGNAL_FILE, "r", encoding="utf-8") as h:
            signals_payload = json.load(h)
    signals = signals_payload.get("signals", [])
    trades = read_csv(TRADES_FILE)

    print("=" * 72)
    print("POLYMARKET WEATHER PAPER TRADER V1.0")
    print("VIRTUAL MONEY ONLY — NO ORDERS SENT")
    print("=" * 72)

    # Resolve existing positions first.
    for trade in trades:
        if trade.get("status") != "OPEN":
            continue
        result, payout_per_share, market, note = resolve_market(trade.get("market_id"))
        if result is None:
            continue
        shares = f(trade.get("shares")) or 0.0
        stake = f(trade.get("stake")) or 0.0
        payout = shares * payout_per_share
        pnl = payout - stake
        trade["status"] = "CLOSED"
        trade["resolved_at"] = now_iso()
        trade["result"] = result
        trade["payout"] = round(payout, 8)
        trade["pnl"] = round(pnl, 8)
        trade["roi"] = round(pnl / stake, 8) if stake else ""
        trade["resolution_source"] = "Polymarket Gamma market endpoint"
        trade["resolution_note"] = note
        print(f"RESOLVED {trade['trade_id']}: {result} P&L={pnl:.4f}")

    open_ids = {str(x.get("market_id")) for x in trades if x.get("status") == "OPEN"}
    sequence = len(trades) + 1
    capacity = max(0, MAX_OPEN_TRADES - len(open_ids))

    candidates = [x for x in signals if x.get("signal") == "PAPER_BUY"]
    candidates.sort(key=lambda x: f(x.get("net_ev_per_share")) or -9.0, reverse=True)

    added = 0
    for signal in candidates:
        market_id = str(signal.get("market_id") or "")
        if not market_id or market_id in open_ids:
            continue
        if capacity <= 0:
            break
        trade = build_trade(signal, VIRTUAL_BANKROLL, sequence)
        if not trade:
            continue
        trades.append(trade)
        open_ids.add(market_id)
        sequence += 1
        capacity -= 1
        added += 1
        print(f"OPENED {trade['trade_id']}: {trade['city']} {trade['market_date']} ask={trade['entry_price']} stake=${STAKE_PER_TRADE:.2f}")

    write_csv(TRADES_FILE, trades)
    summary = calculate_metrics(trades)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as h:
        json.dump(summary, h, ensure_ascii=False, indent=2)
        h.write("\n")

    report = [
        "POLYMARKET WEATHER PAPER TRADER V1.0",
        "VIRTUAL MONEY ONLY — NO ORDERS SENT",
        f"Updated: {summary['updated_at']}",
        "",
        f"Total trades: {summary['total_trades']}",
        f"Open trades: {summary['open_trades']}",
        f"Closed trades: {summary['closed_trades']}",
        f"Wins: {summary['wins']}",
        f"Losses: {summary['losses']}",
        f"Win rate: {summary['win_rate']}",
        f"Closed P&L: {summary['closed_pnl']}",
        f"Closed ROI: {summary['closed_roi']}",
        f"Open stake: {summary['open_stake']}",
        f"New paper trades this run: {added}",
    ]
    with open(REPORT_FILE, "w", encoding="utf-8") as h:
        h.write("\n".join(report) + "\n")

    print("\nSUMMARY")
    for key in ("total_trades", "open_trades", "closed_trades", "wins", "losses", "win_rate", "closed_pnl", "closed_roi", "open_stake"):
        print(f"{key}: {summary[key]}")
    print(f"new paper trades: {added}")


if __name__ == "__main__":
    main()

import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests

DATA_DIR="data"
EDGE_DIR=os.path.join(DATA_DIR,"edge")
PAPER_DIR=os.path.join(EDGE_DIR,"paper_v18")
SIGNAL_FILE=os.path.join(EDGE_DIR,"latest_signals_v18.json")
TRADES_FILE=os.path.join(PAPER_DIR,"trades.csv")
SUMMARY_FILE=os.path.join(PAPER_DIR,"summary.json")
REPORT_FILE=os.path.join(PAPER_DIR,"latest_report.txt")
STATE_FILE=os.path.join(PAPER_DIR,"trial_state.json")
POLYMARKET_API="https://gamma-api.polymarket.com"
REQUEST_TIMEOUT=15

VIRTUAL_BANKROLL=1000.0
STAKE_PER_TRADE=10.0
MAX_OPEN_TRADES=30
MAX_OPEN_PER_FAMILY=1
MAX_OPEN_PER_CITY=3
MAX_OPEN_PER_MARKET_DATE=8
TRIAL_DAYS=15

# Conservative paper-only execution assumptions. These are diagnostics, not claims
# about current Polymarket fees.
EXTRA_SLIPPAGE=0.005

FIELDS=[
"trade_id","engine_version","opened_at","market_id","event_key","city","station","market_date",
"market_type","bucket_type","bucket_value","bucket_low","bucket_high","entry_price","execution_price",
"shares","stake","model_probability","raw_model_probability","gross_edge","net_ev_per_share",
"fee_per_share","slippage_per_share","signal","status","resolved_at","result","payout","pnl","roi",
"resolution_source","resolution_note"
]

def now(): return datetime.now(timezone.utc)
def now_iso(): return now().isoformat()
def f(v):
    try: return None if v in (None,"") else float(v)
    except (TypeError,ValueError): return None

def read_csv(path):
    if not os.path.exists(path): return []
    with open(path,"r",encoding="utf-8",newline="") as h: return list(csv.DictReader(h))

def write_csv(path,rows):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    tmp=path+".tmp"
    with open(tmp,"w",encoding="utf-8",newline="") as h:
        w=csv.DictWriter(h,fieldnames=FIELDS,extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    os.replace(tmp,path)

def get_market(mid):
    r=requests.get(f"{POLYMARKET_API}/markets/{mid}",timeout=REQUEST_TIMEOUT,
                   headers={"User-Agent":"PolymarketWeatherEdgeLab/1.8-paper"})
    r.raise_for_status(); return r.json()

def resolve_market(mid):
    try: m=get_market(mid)
    except Exception as e: return None,None,f"api_error:{e}"
    p=m.get("outcomePrices")
    if isinstance(p,str):
        try: p=json.loads(p)
        except Exception: p=None
    if not (isinstance(p,list) and len(p)>=2): return None,None,"closed_without_outcome_prices"
    if not bool(m.get("closed")) and not bool(m.get("resolved")): return None,None,"still_open"
    y,n=f(p[0]),f(p[1])
    if y==1.0 and n==0.0: return "WIN",1.0,"yes_resolved"
    if y==0.0 and n==1.0: return "LOSS",0.0,"no_resolved"
    return None,None,f"unresolved_prices:{p}"

def load_state():
    os.makedirs(PAPER_DIR,exist_ok=True)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE,encoding="utf-8") as h: s=json.load(h)
    else:
        start=now()
        s={"start_at":start.isoformat(),"trial_days":TRIAL_DAYS,"engine_version":"1.8"}
        with open(STATE_FILE,"w",encoding="utf-8") as h: json.dump(s,h,indent=2)
    start=datetime.fromisoformat(s["start_at"])
    return start,start+timedelta(days=TRIAL_DAYS)

def build_trade(s,seq):
    ask=f(s.get("entry_price"))
    p=f(s.get("shrunken_model_probability"))
    raw=f(s.get("raw_model_probability"))
    if ask is None or p is None or not 0<ask<1: return None
    execution=min(0.999,ask+EXTRA_SLIPPAGE)
    shares=STAKE_PER_TRADE/execution
    fee=f(s.get("fee_per_share")) or 0.0
    return {
      "trade_id":f"paper18-{seq:06d}","engine_version":"1.8","opened_at":s.get("run_at") or now_iso(),
      "market_id":str(s.get("market_id") or ""),"event_key":s.get("event_key") or "",
      "city":s.get("city") or "","station":s.get("station") or "","market_date":s.get("market_date") or "",
      "market_type":s.get("market_type") or "","bucket_type":s.get("bucket_type") or "",
      "bucket_value":s.get("bucket_value") or "","bucket_low":s.get("bucket_low") or "","bucket_high":s.get("bucket_high") or "",
      "entry_price":ask,"execution_price":execution,"shares":shares,"stake":STAKE_PER_TRADE,
      "model_probability":p,"raw_model_probability":raw,"gross_edge":f(s.get("gross_edge")) or "",
      "net_ev_per_share":f(s.get("net_ev_per_share")) or "","fee_per_share":fee,
      "slippage_per_share":EXTRA_SLIPPAGE,"signal":s.get("signal") or "PAPER_BUY_V18",
      "status":"OPEN","resolved_at":"","result":"","payout":"","pnl":"","roi":"",
      "resolution_source":"","resolution_note":""
    }

def metrics(trades,start,end,active):
    closed=[x for x in trades if x.get("status")=="CLOSED"]
    wins=[x for x in closed if x.get("result")=="WIN"]
    pnl=sum(f(x.get("pnl")) or 0 for x in closed)
    stake=sum(f(x.get("stake")) or 0 for x in closed)
    return {"updated_at":now_iso(),"engine_version":"1.8","trial_start":start.isoformat(),
      "trial_end":end.isoformat(),"trial_active":active,"virtual_bankroll_initial":VIRTUAL_BANKROLL,
      "stake_per_trade":STAKE_PER_TRADE,"max_open_trades":MAX_OPEN_TRADES,
      "closed_trades":len(closed),"open_trades":sum(x.get("status")=="OPEN" for x in trades),
      "wins":len(wins),"losses":len(closed)-len(wins),"win_rate":len(wins)/len(closed) if closed else None,
      "closed_pnl":pnl,"closed_roi":pnl/stake if stake else None,
      "open_stake":sum(f(x.get("stake")) or 0 for x in trades if x.get("status")=="OPEN"),
      "virtual_equity_after_closed":VIRTUAL_BANKROLL+pnl}

def main():
    start,end=load_state(); active=now()<end
    payload={}
    if os.path.exists(SIGNAL_FILE):
        with open(SIGNAL_FILE,encoding="utf-8") as h: payload=json.load(h)
    signals=[s for s in payload.get("signals",[]) if s.get("signal")=="PAPER_BUY_V18"]
    trades=read_csv(TRADES_FILE)

    for t in trades:
        if t.get("status")!="OPEN": continue
        result,payout,note=resolve_market(t.get("market_id"))
        if result is None: continue
        shares=f(t.get("shares")) or 0; stake=f(t.get("stake")) or 0
        t.update({"status":"CLOSED","resolved_at":now_iso(),"result":result,
                  "payout":round(shares*payout,8),"pnl":round(shares*payout-stake,8),
                  "roi":round((shares*payout-stake)/stake,8) if stake else "",
                  "resolution_source":"Polymarket Gamma market endpoint","resolution_note":note})

    open_ids={str(x.get("market_id")) for x in trades if x.get("status")=="OPEN"}
    fam=defaultdict(int); city=defaultdict(int); date=defaultdict(int)
    for x in trades:
        if x.get("status")!="OPEN": continue
        fam[x.get("event_key","")]+=1; city[x.get("city","")]+=1; date[x.get("market_date","")]+=1

    candidates=sorted(signals,key=lambda x:f(x.get("net_ev_per_share")) or -9,reverse=True)
    seq=len(trades)+1; added=0
    if active:
        for s in candidates:
            mid=str(s.get("market_id") or ""); ek=s.get("event_key",""); c=s.get("city",""); d=s.get("market_date","")
            if not mid or mid in open_ids or fam[ek]>=MAX_OPEN_PER_FAMILY or city[c]>=MAX_OPEN_PER_CITY or date[d]>=MAX_OPEN_PER_MARKET_DATE: continue
            t=build_trade(s,seq)
            if not t: continue
            trades.append(t); open_ids.add(mid); fam[ek]+=1; city[c]+=1; date[d]+=1; seq+=1; added+=1
            if sum(1 for x in trades if x.get("status")=="OPEN")>=MAX_OPEN_TRADES: break

    write_csv(TRADES_FILE,trades)
    summary=metrics(trades,start,end,active)
    summary.update({"max_open_per_family":MAX_OPEN_PER_FAMILY,"max_open_per_city":MAX_OPEN_PER_CITY,
                    "max_open_per_market_date":MAX_OPEN_PER_MARKET_DATE,"extra_slippage_assumption":EXTRA_SLIPPAGE,
                    "new_paper_trades_this_run":added})
    with open(SUMMARY_FILE,"w",encoding="utf-8") as h: json.dump(summary,h,indent=2)
    with open(REPORT_FILE,"w",encoding="utf-8") as h:
        h.write("POLYMARKET WEATHER PAPER TRADER V1.8\nVIRTUAL MONEY ONLY — NO ORDERS SENT\n")
        for k,v in summary.items(): h.write(f"{k}: {v}\n")
    print(json.dumps(summary,indent=2))

if __name__=="__main__": main()

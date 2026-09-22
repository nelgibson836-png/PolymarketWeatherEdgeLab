import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

DATA="data"
PAPER=os.path.join(DATA,"edge","paper_v18")
TRADES=os.path.join(PAPER,"trades.csv")
SUMMARY=os.path.join(PAPER,"summary.json")
OUT=os.path.join(DATA,"edge","validation")
JSON_OUT=os.path.join(OUT,"latest_paper_analysis_v18.json")
TXT_OUT=os.path.join(OUT,"latest_paper_analysis_v18.txt")

def f(v):
    try:return None if v in (None,"") else float(v)
    except:return None
def read_csv(p):
    if not os.path.exists(p):return []
    with open(p,encoding="utf-8",newline="") as h:return list(csv.DictReader(h))
def read_json(p):
    if not os.path.exists(p):return {}
    with open(p,encoding="utf-8") as h:return json.load(h)
def logloss(p,y):
    p=max(1e-9,min(1-1e-9,p))
    return -(y*math.log(p)+(1-y)*math.log(1-p))
def main():
    os.makedirs(OUT,exist_ok=True)
    rows=read_csv(TRADES)
    closed=[r for r in rows if r.get("status")=="CLOSED" and r.get("result") in ("WIN","LOSS") and f(r.get("model_probability")) is not None]
    n=len(closed)
    wins=sum(r.get("result")=="WIN" for r in closed)
    ps=[f(r["model_probability"]) for r in closed]
    ys=[1 if r["result"]=="WIN" else 0 for r in closed]
    pnl=sum(f(r.get("pnl")) or 0 for r in closed)
    stake=sum(f(r.get("stake")) or 0 for r in closed)
    brier=sum((p-y)**2 for p,y in zip(ps,ys))/n if n else None
    ll=sum(logloss(p,y) for p,y in zip(ps,ys))/n if n else None
    market_ps=[f(r.get("entry_price")) for r in closed]
    market_ps=[p for p in market_ps if p is not None and 0 < p < 1]
    market_pairs=[(f(r.get("entry_price")), y) for r,y in zip(closed,ys) if f(r.get("entry_price")) is not None and 0 < f(r.get("entry_price")) < 1]
    market_brier=sum((p-y)**2 for p,y in market_pairs)/len(market_pairs) if market_pairs else None
    market_ll=sum(logloss(p,y) for p,y in market_pairs)/len(market_pairs) if market_pairs else None
    micro=[r for r in closed if (f(r.get("entry_price")) or 0)<=.02]
    nonmicro=[r for r in closed if (f(r.get("entry_price")) or 0)>.02]
    result={"generated_at":datetime.now(timezone.utc).isoformat(),"engine_version":"1.8","paper_trial":read_json(SUMMARY),
      "closed_trades":n,"wins":wins,"losses":n-wins,"observed_win_rate":wins/n if n else None,
      "mean_calibrated_probability":sum(ps)/n if n else None,"expected_wins":sum(ps) if n else None,
      "actual_minus_expected_wins":wins-sum(ps) if n else None,"brier_score":brier,"log_loss":ll,"market_brier_score":market_brier,"market_log_loss":market_ll,
      "closed_pnl":pnl,"closed_roi":pnl/stake if stake else None,
      "microprice_le_2c":{"n":len(micro),"pnl":sum(f(r.get("pnl")) or 0 for r in micro),"wins":sum(r.get("result")=="WIN" for r in micro)},
      "non_microprice_gt_2c":{"n":len(nonmicro),"pnl":sum(f(r.get("pnl")) or 0 for r in nonmicro),"wins":sum(r.get("result")=="WIN" for r in nonmicro)},
      "market_comparison": {"model_minus_market_brier": (brier-market_brier) if brier is not None and market_brier is not None else None, "model_minus_market_log_loss": (ll-market_ll) if ll is not None and market_ll is not None else None},
      "live_ready":False,
      "promotion_rule":"Require positive OOS edge after execution costs, not dependent on <=2c tickets, with settlement integrity and sufficient multi-city sample."
    }
    with open(JSON_OUT,"w",encoding="utf-8") as h:json.dump(result,h,indent=2)
    with open(TXT_OUT,"w",encoding="utf-8") as h:
        for k,v in result.items():h.write(f"{k}: {v}\n")
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()

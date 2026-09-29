from __future__ import annotations

from collections import defaultdict
from typing import Any


def _num(v: Any) -> float | None:
    try:
        if v in (None,"","-"):
            return None
        return float(v)
    except (TypeError,ValueError):
        return None


def clv_percent(signal_odd: float, closing_odd: float) -> float:
    """Positive when the taken price is better than closing price."""
    if signal_odd<=1.0 or closing_odd<=1.0:
        return 0.0
    return (signal_odd/closing_odd-1.0)*100.0


def summarize_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled=[]
    for r in rows:
        result=str(r.get("result") or "").lower()
        if result not in {"won","lost"}:
            continue
        p=_num(r.get("probability"))
        odd=_num(r.get("odd"))
        close=_num(r.get("closing_odd"))
        if p is None:
            continue
        y=1 if result=="won" else 0
        profit=(odd-1.0) if y and odd and odd>1.0 else (-1.0 if not y else 0.0)
        settled.append({
            "p":max(0.0,min(1.0,p/100.0 if p>1 else p)),
            "y":y,"profit":profit,
            "clv":None if odd is None or close is None else clv_percent(odd,close),
            "league":str(r.get("league") or "unknown"),
            "ts":str(r.get("created_at") or r.get("timestamp") or ""),
        })
    if not settled:
        return {"sample":0}

    def block(items):
        n=len(items)
        brier=sum((x["p"]-x["y"])**2 for x in items)/n
        hit=sum(x["y"] for x in items)/n
        avgp=sum(x["p"] for x in items)/n
        roi=sum(x["profit"] for x in items)/n*100.0
        clvs=[x["clv"] for x in items if x["clv"] is not None]
        return {
            "sample":n,"hit_rate":round(hit,4),"avg_probability":round(avgp,4),
            "calibration_error_pp":round((hit-avgp)*100,2),
            "brier":round(brier,5),"roi_pct":round(roi,2),
            "avg_clv_pct":None if not clvs else round(sum(clvs)/len(clvs),3),
            "beat_close_rate":None if not clvs else round(sum(1 for x in clvs if x>0)/len(clvs),4),
        }

    settled.sort(key=lambda x:x["ts"])
    recent=settled[-max(10,len(settled)//4):]
    baseline=settled[:-len(recent)] if len(settled)>len(recent) else []
    out={"sample":len(settled),"overall":block(settled),"recent":block(recent)}
    if baseline:
        base=block(baseline)
        out["baseline"]=base
        out["drift"]={
            "brier_delta":round(out["recent"]["brier"]-base["brier"],5),
            "calibration_error_abs_delta_pp":round(abs(out["recent"]["calibration_error_pp"])-abs(base["calibration_error_pp"]),2),
            "roi_delta_pp":round(out["recent"]["roi_pct"]-base["roi_pct"],2),
            "clv_delta_pp":None if out["recent"]["avg_clv_pct"] is None or base["avg_clv_pct"] is None else round(out["recent"]["avg_clv_pct"]-base["avg_clv_pct"],3),
        }

    leagues=defaultdict(list)
    for x in settled: leagues[x["league"]].append(x)
    out["leave_one_league_out"]=[]
    if len(leagues)>=2:
        for league in sorted(leagues):
            rest=[x for x in settled if x["league"]!=league]
            if rest:
                out["leave_one_league_out"].append({"left_out":league,**block(rest)})
    return out


__all__=["clv_percent","summarize_quality"]

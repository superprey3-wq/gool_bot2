from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("GOOL_FOOTBALL_AUTOINSTALL", "0")
from typing import Any

from gool_bot2.xbet_multisport_steam import MultiSportSteamWorker, SPORTS, map_xbet_to_flashscore


def flatten_groups(game: dict[str, Any]) -> list[dict[str, Any]]:
    out=[]
    for group in game.get("AE") or []:
        if not isinstance(group,dict): continue
        g=group.get("G")
        for sel in group.get("ME") or []:
            if isinstance(sel,dict) and sel.get("T") is not None and (sel.get("C") is not None or sel.get("CV") is not None):
                out.append({"layout":"AE","G":g,"GS":sel.get("GS") or group.get("GS"),"T":sel.get("T"),"P":sel.get("P"),"C":sel.get("C") or sel.get("CV")})
    for group in game.get("GE") or []:
        if not isinstance(group,dict): continue
        g=group.get("G"); gs=group.get("GS")
        for row in group.get("E") or []:
            sels=[row] if isinstance(row,dict) else row if isinstance(row,list) else []
            for sel in sels:
                if isinstance(sel,dict) and sel.get("T") is not None and (sel.get("C") is not None or sel.get("CV") is not None):
                    out.append({"layout":"GE","G":g,"GS":sel.get("GS") or gs,"T":sel.get("T"),"P":sel.get("P"),"C":sel.get("C") or sel.get("CV")})
    for sel in game.get("E") or []:
        if isinstance(sel,dict) and sel.get("T") is not None and (sel.get("C") is not None or sel.get("CV") is not None):
            out.append({"layout":"E","G":sel.get("G"),"GS":sel.get("GS"),"T":sel.get("T"),"P":sel.get("P"),"C":sel.get("C") or sel.get("CV")})
    uniq={}
    for row in out:
        key=(row["layout"],row["G"],row["GS"],row["T"],row["P"])
        uniq.setdefault(key,row)
    return list(uniq.values())


def compact(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets={}
    for row in groups:
        key=(row.get("G"),row.get("GS"),row.get("T"))
        b=buckets.setdefault(key,{"G":row.get("G"),"GS":row.get("GS"),"T":row.get("T"),"layouts":set(),"lines":[]})
        b["layouts"].add(str(row.get("layout")))
        if row.get("P") is not None and row.get("P") not in b["lines"]:
            b["lines"].append(row.get("P"))
    out=[]
    for b in buckets.values():
        b["layouts"]=sorted(b["layouts"]); b["lines"]=sorted(b["lines"],key=lambda x:float(x))
        out.append(b)
    return sorted(out,key=lambda x:(int(x["G"] or -1),int(x["GS"] or -1),int(x["T"] or -1)))


def relevant_sg(row: dict[str,Any]) -> bool:
    p=str(row.get("PN") or "").strip().casefold()
    return any(k in p for k in ("quarter","half","period"))


def audit_phase(worker: MultiSportSteamWorker, cfg, phase: str) -> dict[str,Any]:
    fs=worker._flashscore_today(cfg)
    if phase=="PREMATCH":
        fs=[x for x in fs if str(x.get("coarse_status") or "")=="1"][:200]
        xb=worker._xbet_prematch_index(cfg)
    else:
        fs=[x for x in fs if str(x.get("coarse_status") or "")=="2"][:200]
        xb=worker._xbet_index(cfg)
    mapped=map_xbet_to_flashscore(xb,fs)
    result={"flashscore":len(fs),"xbet":len(xb),"mapped":len(mapped),"events":[]}
    for event, frow, rev, score in mapped[:2]:
        eid=str(event.get("I") or "")
        game=(worker._prematch_game(eid,cfg) if phase=="PREMATCH" else worker._game(eid,cfg)) or event
        ev={
            "event_id":eid,"home":frow.get("home"),"away":frow.get("away"),"league":frow.get("league"),
            "root_keys":sorted(game.keys()),"root_markets":compact(flatten_groups(game)),
            "subgames_meta":[{k:sg.get(k) for k in ("I","PN","TG","P","EC","SI","MG")} for sg in game.get("SG") or [] if isinstance(sg,dict)],
            "subgames":[]
        }
        for sg in [x for x in game.get("SG") or [] if isinstance(x,dict) and relevant_sg(x)][:12]:
            sid=str(sg.get("I") or "")
            if not sid: continue
            sub=(worker._prematch_game(sid,cfg) if phase=="PREMATCH" else worker._game(sid,cfg)) or {}
            ev["subgames"].append({
                "id":sid,"PN":sg.get("PN"),"TG":sg.get("TG"),"P":sg.get("P"),
                "keys":sorted(sub.keys()),"markets":compact(flatten_groups(sub))
            })
        result["events"].append(ev)
    return result


def main():
    runtime=Path("artifacts/multisport_market_tree/runtime"); runtime.mkdir(parents=True,exist_ok=True)
    os.environ["RUNTIME_DATA_DIR"]=str(runtime)
    os.environ["GOOL_MULTISPORT_MODE"]="shadow"
    os.environ.setdefault("GOOL_MULTISPORT_EXACT_GAME_TIMEOUT","3.0")
    os.environ.setdefault("GOOL_MULTISPORT_GAME_HTTP_TIMEOUT","2.5")
    os.environ.setdefault("GOOL_MULTISPORT_GAME_ROOT_ATTEMPTS","1")
    os.environ.setdefault("GOOL_MULTISPORT_V3_GAME_FALLBACK","0")
    os.environ.setdefault("GOOL_MULTISPORT_CURRENT_LINEFEED_FALLBACK","0")
    os.environ.setdefault("GOOL_MULTISPORT_SUBGAME_ROOT_ATTEMPTS","1")
    os.environ.setdefault("GOOL_MULTISPORT_SUBGAME_HTTP_TIMEOUT","2.5")
    os.environ.setdefault("GOOL_MULTISPORT_HTTP_ATTEMPTS","1")
    worker=MultiSportSteamWorker(runtime)
    report={}
    for key,cfg in SPORTS.items():
        report[key]={}
        for phase in ("PREMATCH","LIVE"):
            try:
                report[key][phase]=audit_phase(worker,cfg,phase)
            except Exception as exc:
                report[key][phase]={"error":f"{type(exc).__name__}:{exc}"}
    out=Path("artifacts/multisport_market_tree"); out.mkdir(parents=True,exist_ok=True)
    (out/"market_tree.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),"utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()

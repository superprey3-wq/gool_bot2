from __future__ import annotations
import json, os, time, urllib.request

OLLAMA=os.getenv("OLLAMA_URL","http://127.0.0.1:11434")
MODEL=os.environ["LOCAL_LLM_MODEL"]

MATCHES=[
{
 "id":"araba_hapoel",
 "home":"Araba","away":"Hapoel Migdal HaEmek","competition":"Israel",
 "raw":{
  "home_recent":["1-4 L","0-4 L","0-4 L","0-1 L"],
  "away_recent":["2-3 L","4-2 W","2-2 D","2-0 W"],
  "h2h":["Araba 1-2 Hapoel","Araba 1-2 Hapoel","Hapoel 2-0 Araba"]
 },
 "facts":{
  "home_last4":{"wins":0,"draws":0,"losses":4,"goals_for":1,"goals_against":13,"scored_in":1},
  "away_last4":{"wins":2,"draws":1,"losses":1,"goals_for":10,"goals_against":7,"scored_2plus_in":4},
  "h2h":{"matches":3,"home_team_wins":0,"draws":0,"away_team_wins":3,"scores":["1-2","1-2","0-2"],"away_team_exactly_2_goals_in":3}
 },
 "markets":["FT O/U 1.5","FT O/U 2.5","FT O/U 3.5","home team O/U 0.5","home team O/U 1.5","away team O/U 0.5","away team O/U 1.5","BTTS","1X2","double chance"]
},
{
 "id":"bryne_kolbotn",
 "home":"Bryne W","away":"Kolbotn W","competition":"Norway Division 1 Women",
 "raw":{
  "home_recent":["6-2 W","0-4 L","5-0 W","6-1 W","0-1 L"],
  "away_recent":["2-1 W","4-1 W","2-2 D","1-3 L","1-3 L"],
  "h2h":["Bryne 1-0 Kolbotn","Bryne 2-0 Kolbotn"]
 },
 "facts":{
  "home_last5":{"wins":3,"draws":0,"losses":2,"goals_for":17,"goals_against":8,"scored_in":3},
  "away_last5":{"wins":2,"draws":1,"losses":2,"goals_for":10,"goals_against":10,"scored_in":5},
  "h2h":{"matches":2,"home_team_wins":2,"draws":0,"away_team_wins":0,"scores":["1-0","2-0"],"total_under_2_5_in":2}
 },
 "markets":["FT O/U 1.5","FT O/U 2.5","FT O/U 3.5","home team O/U 0.5","home team O/U 1.5","away team O/U 0.5","away team O/U 1.5","BTTS","1X2","double chance"]
}
]

SYSTEM="""You are a cautious football prematch second-opinion reviewer.
The final scores are hidden. Never infer or invent them.
You receive deterministic_facts calculated by code. Treat those as authoritative and do not contradict them.
First describe the football scenario. Then compare ALL supplied market families.
Do not force a bet. If no direction is clearly supported, choose SKIP.
Do not equate a goals trend with Over 2.5 automatically.
There are NO bookmaker prices in this diagnostic, so do not claim value/EV.
Return ONLY JSON:
{
 "decision":"BET|SKIP",
 "best_market":"specific market or SKIP",
 "confidence":0-100,
 "scenario":"...",
 "why_best":"...",
 "alternatives":["..."],
 "avoid":["..."],
 "fact_check":{
   "home_form_summary":"...",
   "away_form_summary":"...",
   "h2h_summary":"..."
 }
}
If BET, best_market must be one of the supplied market families and use a concrete side/line such as
"away team over 0.5", "under 3.5", "double chance X2", "home win", "BTTS no".
Confidence is confidence in the football direction, not a guarantee and not expected value."""

def call(match):
    user=json.dumps({
      "match":f"{match['home']} vs {match['away']}",
      "competition":match["competition"],
      "raw_evidence":match["raw"],
      "deterministic_facts":match["facts"],
      "available_markets":match["markets"],
      "instruction":"Analyze prematch only. Final result intentionally hidden."
    },ensure_ascii=False,indent=2)
    payload={"model":MODEL,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":user}],
             "stream":False,"format":"json","options":{"temperature":0.1,"num_ctx":8192}}
    req=urllib.request.Request(OLLAMA+"/api/chat",data=json.dumps(payload).encode(),
                               headers={"Content-Type":"application/json"},method="POST")
    t=time.time()
    with urllib.request.urlopen(req,timeout=1200) as r:
        raw=json.loads(r.read().decode())
    content=raw.get("message",{}).get("content","")
    try: ans=json.loads(content)
    except Exception: ans={"parse_error":True,"raw":content}
    return {"match_id":match["id"],"elapsed_s":round(time.time()-t,2),"answer":ans}

out={"model":MODEL,"results":[]}
for m in MATCHES:
    print(f"MODEL={MODEL} MATCH={m['id']}",flush=True)
    try: row=call(m)
    except Exception as e: row={"match_id":m["id"],"error":f"{type(e).__name__}: {e}"}
    out["results"].append(row)
    print(json.dumps(row,ensure_ascii=False,indent=2),flush=True)

safe=MODEL.replace(":","_").replace("/","_")
path=f"local_llm_benchmark_{safe}.json"
with open(path,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=2)
print(f"WROTE {path}",flush=True)

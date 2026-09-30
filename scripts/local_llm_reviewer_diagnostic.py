from __future__ import annotations
import json, os, time, urllib.request

OLLAMA=os.getenv("OLLAMA_URL","http://127.0.0.1:11434")

MATCH={
  "home":"Araba",
  "away":"Hapoel Migdal HaEmek",
  "competition":"Israel",
  "home_recent":[
    {"vs":"Maccabi Nujeidat Ahmad","score":"1-4","result":"L"},
    {"vs":"M. Kiryat Ata-Bialik","score":"0-4","result":"L"},
    {"vs":"MC Tira","score":"0-4","result":"L"},
    {"vs":"Nahariya","score":"0-1","result":"L"}
  ],
  "away_recent":[
    {"vs":"Nof HaGalil","score":"2-3","result":"L"},
    {"vs":"Maccabi Nujeidat Ahmad","score":"4-2","result":"W"},
    {"vs":"M. Kiryat Ata-Bialik","score":"2-2","result":"D"},
    {"vs":"Tzeirey Tamra","score":"2-0","result":"W"}
  ],
  "h2h":[
    {"score":"Araba 1-2 Hapoel Migdal HaEmek"},
    {"score":"Araba 1-2 Hapoel Migdal HaEmek"},
    {"score":"Hapoel Migdal HaEmek 2-0 Araba"}
  ],
  "available_market_families":[
    "match total over/under 1.5,2.5,3.5",
    "home team total over/under 0.5,1.5",
    "away team total over/under 0.5,1.5",
    "BTTS yes/no",
    "1X2",
    "double chance"
  ],
  "note":"Final score is intentionally hidden. Analyze only prematch evidence."
}

SYSTEM="""You are a football prematch reviewer. You do not need to force a bet.
Use ONLY the supplied match evidence. Do not invent injuries, standings, odds or news.
First understand the likely match scenario, then compare market families.
Do NOT automatically choose Over 2.5 just because one team concedes often.
You may choose SKIP if the evidence does not support a specific market.
Return ONLY valid JSON with keys:
decision (BET or SKIP),
best_market,
confidence (0-100),
scenario,
reasoning,
alternatives (array),
avoid (array).
The market can be match total, team total, BTTS, 1X2/double chance, or SKIP.
Without prices, confidence means confidence in the football scenario/market direction, NOT expected value."""
USER=json.dumps(MATCH,ensure_ascii=False,indent=2)

def call(model:str)->dict:
    payload={
      "model":model,
      "messages":[{"role":"system","content":SYSTEM},{"role":"user","content":USER}],
      "stream":False,
      "format":"json",
      "options":{"temperature":0.15,"num_ctx":8192}
    }
    req=urllib.request.Request(
      OLLAMA+"/api/chat",
      data=json.dumps(payload).encode(),
      headers={"Content-Type":"application/json"},
      method="POST"
    )
    started=time.time()
    with urllib.request.urlopen(req,timeout=900) as r:
        raw=json.loads(r.read().decode())
    content=raw.get("message",{}).get("content","")
    try:
        parsed=json.loads(content)
    except Exception:
        parsed={"raw":content,"parse_error":True}
    return {"model":model,"elapsed_s":round(time.time()-started,2),"answer":parsed}

models=[m.strip() for m in os.getenv("LOCAL_LLM_MODELS","qwen2.5:7b,llama3.2:3b").split(",") if m.strip()]
out={"match":MATCH,"results":[]}
for model in models:
    print(f"RUNNING {model}",flush=True)
    try:
        row=call(model)
    except Exception as exc:
        row={"model":model,"error":f"{type(exc).__name__}: {exc}"}
    out["results"].append(row)
    print(json.dumps(row,ensure_ascii=False,indent=2),flush=True)

with open("local_llm_reviewer_result.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
print("WROTE local_llm_reviewer_result.json",flush=True)

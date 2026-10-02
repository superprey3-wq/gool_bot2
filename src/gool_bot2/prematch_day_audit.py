from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from .journal import load_signal_journal
from .multi_menu import _parse_dt, _tz


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bucket_odd(v: Any) -> str:
    odd=_f(v)
    if odd is None or odd <= 1: return "без кэфа"
    if odd < 1.50: return "<1.50"
    if odd < 1.70: return "1.50–1.69"
    if odd < 1.90: return "1.70–1.89"
    if odd < 2.20: return "1.90–2.19"
    return "2.20+"


def _bucket_prob(v: Any) -> str:
    p=_f(v)
    if p is None: return "без p"
    if p > 1: p/=100.0
    if p < .60: return "<60%"
    if p < .65: return "60–64%"
    if p < .70: return "65–69%"
    if p < .75: return "70–74%"
    return "75%+"


def _bucket_edge(row: dict[str, Any]) -> str:
    e=_f(row.get("edge"))
    if e is None:
        e=_f(row.get("value_edge"))
    if e is None:
        e=_f(row.get("value_edge_pp"))
        if e is not None and abs(e)>1: e/=100.0
    if e is None: return "без edge"
    if e < .03: return "<3пп"
    if e < .06: return "3–5.9пп"
    if e < .10: return "6–9.9пп"
    return "10пп+"


def _market_group(row: dict[str, Any]) -> str:
    fam=str(row.get("market_family") or "").strip()
    if fam: return fam
    m=str(row.get("market") or row.get("selection") or "").upper()
    if "BTTS" in m or "ОБЕ" in m: return "btts"
    if "1H" in m or "1-Й ТАЙМ" in m: return "first_half_total"
    if "2H" in m or "2-Й ТАЙМ" in m: return "second_half_total"
    return "match_total"


def _rate(w:int,l:int)->str:
    n=w+l
    return "—" if not n else f"{100*w/n:.1f}%"


def _line(title:str, rows:list[dict[str,Any]])->str:
    c=Counter(str(r.get("result") or "").lower() for r in rows)
    settled=c["won"]+c["lost"]
    odds=[x for r in rows if (x:=_f(r.get("odd"))) is not None and x>1]
    probs=[]
    for r in rows:
        p=_f(r.get("probability") or r.get("model_probability"))
        if p is not None:
            probs.append(p/100.0 if p>1 else p)
    return (
        f"{title}: {settled} · ✅ {c['won']} · ❌ {c['lost']} · "
        f"проход {_rate(c['won'],c['lost'])} · "
        f"ср.кэф {mean(odds):.2f}" if odds else
        f"{title}: {settled} · ✅ {c['won']} · ❌ {c['lost']} · проход {_rate(c['won'],c['lost'])} · ср.кэф —"
    ) + (f" · ср.p {mean(probs)*100:.1f}%" if probs else "")


def _group(rows:list[dict[str,Any]], keyfn):
    d=defaultdict(list)
    for r in rows: d[keyfn(r)].append(r)
    return d


def prematch_day_audit_text(journal_path: Path) -> str:
    rows=load_signal_journal(journal_path)
    tz=_tz()
    today=datetime.now(tz).date()
    pm=[
        r for r in rows
        if str(r.get("origin") or "").lower()=="prematch"
        and (dt:=_parse_dt(r.get("created_at"))) is not None
        and dt.astimezone(tz).date()==today
        and str(r.get("result") or "").lower() in {"won","lost"}
        and bool(r.get("telegram_sent"))
        and not bool(r.get("public_duplicate"))
    ]
    if not pm:
        return "🧠 PREMATCH AUDIT\nСегодня нет закрытых реально отправленных prematch-ординаров."

    wins=[r for r in pm if str(r.get("result")).lower()=="won"]
    losses=[r for r in pm if str(r.get("result")).lower()=="lost"]

    out=[
        "🧠 <b>PREMATCH · РАЗБОР ДНЯ</b>",
        _line("Все",pm),
        _line("Плюсы",wins),
        _line("Минусы",losses),
        "",
        "<b>По рынкам</b>",
    ]

    for k,v in sorted(_group(pm,_market_group).items(), key=lambda x:(-len(x[1]),x[0])):
        out.append(_line(k,v))

    out += ["","<b>По коэффициентам</b>"]
    for k,v in _group(pm,lambda r:_bucket_odd(r.get("odd"))).items():
        out.append(_line(k,v))

    out += ["","<b>По вероятности модели</b>"]
    for k,v in _group(pm,lambda r:_bucket_prob(r.get("probability") or r.get("model_probability"))).items():
        out.append(_line(k,v))

    out += ["","<b>По edge</b>"]
    for k,v in _group(pm,_bucket_edge).items():
        out.append(_line(k,v))

    # Leagues with at least 2 settled selections are more informative than one-offs.
    out += ["","<b>Проблемные турниры/лиги (от 2 ставок)</b>"]
    leagues=_group(pm,lambda r:str(r.get("league") or "UNKNOWN"))
    ranked=[]
    for league,items in leagues.items():
        c=Counter(str(r.get("result") or "").lower() for r in items)
        n=c["won"]+c["lost"]
        if n>=2:
            ranked.append((c["won"]/n,n,league,items))
    for _,_,league,items in sorted(ranked,key=lambda x:(x[0],-x[1],x[2]))[:10]:
        out.append(_line(league,items))

    # Individual losses for post-mortem.
    out += ["","<b>14/текущие минусы — что видел мозг</b>"]
    for i,r in enumerate(sorted(losses,key=lambda x:(_f(x.get("probability") or x.get("model_probability")) or 0),reverse=True),1):
        p=_f(r.get("probability") or r.get("model_probability"))
        ptxt="—" if p is None else f"{(p/100 if p>1 else p)*100:.1f}%"
        edge=_f(r.get("edge"))
        if edge is None: edge=_f(r.get("value_edge"))
        etxt="—" if edge is None else f"{(edge/100 if abs(edge)>1 else edge)*100:+.1f}пп"
        quality=_f(r.get("data_quality"))
        qtxt="—" if quality is None else f"{quality*100:.0f}%"
        out.append(
            f"{i}. {r.get('home','?')} — {r.get('away','?')} · {r.get('league','?')}\n"
            f"   {r.get('selection') or r.get('market')} @ {_f(r.get('odd')) or 0:.2f} · p {ptxt} · edge {etxt} · quality {qtxt}"
        )
        if len("\n".join(out))>3600:
            out.append("… список сокращён из-за лимита Telegram")
            break

    return "\n".join(out)


__all__=["prematch_day_audit_text"]

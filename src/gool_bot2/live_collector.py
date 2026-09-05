from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .prefilter import football_prefilter
from .providers.flashscore import FlashscoreProvider
from .providers.fotmob import FotMobProvider
from .providers.scores365 import Scores365Provider
from .team_history import fotmob_team_history


class LiveSnapshotCollector:
    """Append-only football snapshot collector for GOOL Bot 2."""

    def __init__(self, data_dir: str | Path = "data/raw/live", prefilter_threshold: float = 50.0, secondary_interval_minutes: int = 3) -> None:
        self.data_dir = Path(data_dir)
        self.prefilter_threshold = float(prefilter_threshold)
        self.secondary_interval_minutes = max(1, int(secondary_interval_minutes))
        self.flashscore = FlashscoreProvider(); self.fotmob = FotMobProvider(); self.scores365 = Scores365Provider()
        self._last_secondary_minute: dict[str, int] = {}
        self._secondary_cache: dict[str, dict[str, dict[str, Any]]] = {}
        self._tracked_matches: dict[str, dict[str, Any]] = {}
        self._prematch_context: dict[str, dict[str, Any]] = {}
        self._prematch_last_attempt: dict[str, float] = {}
        self._stop = False

    @staticmethod
    def _eligible_for_detail(minute: int, is_halftime: bool) -> bool:
        return bool(is_halftime or 1 <= minute <= 90)

    def _secondary_due(self, match_id: str, minute: int) -> bool:
        previous = self._last_secondary_minute.get(match_id)
        if previous is None or minute - previous >= self.secondary_interval_minutes:
            self._last_secondary_minute[match_id] = minute
            return True
        return False

    def _path_for_now(self, now: datetime) -> Path:
        return self.data_dir / f"{now.date().isoformat()}.jsonl"

    def _append(self, record: dict[str, Any], now: datetime) -> None:
        path = self._path_for_now(now); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _attach_flashscore_assets(self, record: dict[str, Any], match_meta: dict[str, Any]) -> None:
        fs = ((record.get("providers") or {}).get("flashscore") or {}); meta = fs.get("meta") or {}
        home_logo = self.flashscore.team_logo_url(str(match_meta.get("home_team_slug") or ""), str(match_meta.get("home_team_id") or ""))
        away_logo = self.flashscore.team_logo_url(str(match_meta.get("away_team_slug") or ""), str(match_meta.get("away_team_id") or ""))
        if home_logo: meta["home_logo_url"] = home_logo
        if away_logo: meta["away_logo_url"] = away_logo
        fs["meta"] = meta; record["providers"]["flashscore"] = fs

    @staticmethod
    def _row_timestamp(row: dict[str, Any]) -> float:
        value = row.get("timestamp")
        try:
            raw = float(value)
            return raw / 1000.0 if raw > 10_000_000_000 else raw
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    @staticmethod
    def _row_identity(row: dict[str, Any]) -> tuple[Any, ...]:
        event_id = str(row.get("event_id") or "").strip()
        if event_id:
            return ("id", event_id)
        return (
            "row",
            str(row.get("home") or "").casefold().strip(),
            str(row.get("away") or "").casefold().strip(),
            str(row.get("timestamp") or ""),
        )

    @staticmethod
    def _merge_history_contexts(contexts: list[dict[str, Any]], limit: int) -> dict[str, Any]:
        """Merge provider histories first, then choose the best/latest rows.

        The old code sliced each list to `limit` while processing providers. If the
        first provider returned ten malformed rows (for example ten apparent 0:0s),
        valid FotMob/365 rows never had a chance to enter the prematch profile.
        Half-score rows are now ranked above final-score-only duplicates because
        the two-system GOOL needs independent 1H/2H history.
        """
        keys = ("home_recent", "away_recent", "home_at_home", "away_away", "h2h")
        pools: dict[str, list[dict[str, Any]]] = {key: [] for key in keys}
        sources: list[str] = []
        raw_counts: dict[str, int] = {}
        source_quality: dict[str, dict[str, Any]] = {}
        has_trends = False
        has_top_trends = False
        has_previous_meetings = False
        half_score_matches = 0

        for ctx in contexts:
            if not isinstance(ctx, dict):
                continue
            source = str(ctx.get("source") or "unknown")
            if source != "none":
                sources.append(source)
            raw_counts[source] = max(raw_counts.get(source, 0), int(ctx.get("raw_matches") or 0))
            has_trends = has_trends or bool(ctx.get("has_trends"))
            has_top_trends = has_top_trends or bool(ctx.get("has_top_trends"))
            has_previous_meetings = has_previous_meetings or bool(ctx.get("has_previous_meetings"))
            half_score_matches = max(half_score_matches, int(ctx.get("half_score_matches") or 0))
            for key in keys:
                rows = [dict(r) for r in (ctx.get(key) or []) if isinstance(r, dict)]
                if not rows:
                    continue
                for row in rows:
                    row.setdefault("source", source)
                    pools[key].append(row)
                totals = [int(r.get("home_score") or 0) + int(r.get("away_score") or 0) for r in rows]
                quality = source_quality.setdefault(source, {"rows": 0, "nonzero": 0, "zero_zero": 0})
                quality["rows"] += len(rows)
                quality["nonzero"] += sum(1 for x in totals if x > 0)
                quality["zero_zero"] += sum(1 for x in totals if x == 0)

        merged: dict[str, Any] = {}
        source_rank = {
            "365scores_recent_halves": 5,
            "fotmob_team_history": 4,
            "flashscore_h2h": 3,
            "fotmob_embedded": 2,
            "365scores_embedded": 1,
        }
        suspicious_sources = {
            source for source, q in source_quality.items()
            if int(q.get("rows") or 0) >= 5 and int(q.get("nonzero") or 0) == 0
        }

        # Only suppress an all-0:0 source when some other provider has actual goals.
        any_nonzero_source = any(int(q.get("nonzero") or 0) > 0 for q in source_quality.values())
        if not any_nonzero_source:
            suspicious_sources = set()

        for key in keys:
            selected: dict[tuple[Any, ...], dict[str, Any]] = {}
            candidates = pools[key]
            if suspicious_sources:
                cleaned = [r for r in candidates if str(r.get("source") or "") not in suspicious_sources]
                if cleaned:
                    candidates = cleaned
            for row in candidates:
                ident = LiveSnapshotCollector._row_identity(row)
                existing = selected.get(ident)
                if existing is None:
                    selected[ident] = row
                    continue
                old_total = int(existing.get("home_score") or 0) + int(existing.get("away_score") or 0)
                new_total = int(row.get("home_score") or 0) + int(row.get("away_score") or 0)
                old_rank = source_rank.get(str(existing.get("source") or ""), 0)
                new_rank = source_rank.get(str(row.get("source") or ""), 0)
                old_half = existing.get("halftime_home_score") is not None and existing.get("halftime_away_score") is not None
                new_half = row.get("halftime_home_score") is not None and row.get("halftime_away_score") is not None
                if (
                    (new_half and not old_half)
                    or (new_total > 0 and old_total == 0)
                    or (new_total == old_total and new_half == old_half and new_rank > old_rank)
                ):
                    selected[ident] = row
            rows = list(selected.values())
            rows.sort(
                key=lambda r: (
                    LiveSnapshotCollector._row_timestamp(r),
                    int(r.get("halftime_home_score") is not None and r.get("halftime_away_score") is not None),
                    source_rank.get(str(r.get("source") or ""), 0),
                ),
                reverse=True,
            )
            merged[key] = rows[:limit]

        merged["source"] = "+".join(dict.fromkeys(sources)) if sources else "none"
        merged["sources"] = list(dict.fromkeys(sources))
        merged["source_raw_matches"] = raw_counts
        merged["source_quality"] = source_quality
        merged["suppressed_sources"] = sorted(suspicious_sources)
        merged["raw_matches"] = sum(raw_counts.values())
        merged["half_score_matches"] = half_score_matches
        merged["has_trends"] = has_trends
        merged["has_top_trends"] = has_top_trends
        merged["has_previous_meetings"] = has_previous_meetings
        return merged

    @staticmethod
    def _history_ready(ctx: dict[str, Any], minimum: int) -> bool:
        return len(ctx.get("home_recent") or []) >= minimum and len(ctx.get("away_recent") or []) >= minimum

    def _history_for(self, match: Any) -> dict[str, Any]:
        mid = str(match.provider_match_id)
        limit = int(os.getenv("PREMATCH_HISTORY_MATCHES", "10"))
        minimum = int(os.getenv("ANOTHER_GOAL_PREMATCH_MIN_TEAM_MATCHES", "5"))
        retry_seconds = max(60, int(os.getenv("PREMATCH_HISTORY_RETRY_SECONDS", "180")))
        cached = self._prematch_context.get(mid) or {}
        if cached and self._history_ready(cached, minimum):
            return cached
        now = time.time()
        previous_attempt = self._prematch_last_attempt.get(mid, 0.0)
        if cached and now - previous_attempt < retry_seconds:
            return cached
        self._prematch_last_attempt[mid] = now

        contexts: list[dict[str, Any]] = [cached] if cached else []
        # Team-level FotMob history is the strongest fallback because it directly
        # contains each side's recent fixtures. Flashscore remains a primary source,
        # but no provider can monopolize the first `limit` rows anymore.
        try:
            contexts.append(fotmob_team_history(self.fotmob, str(match.home), str(match.away), limit=limit))
        except Exception as exc:
            print(f"prematch_fotmob_team_error match={mid} error={type(exc).__name__}:{exc}", flush=True)
        try:
            contexts.append(self.flashscore.fetch_match_history(mid, str(match.home), str(match.away), limit=limit))
        except Exception as exc:
            print(f"prematch_flashscore_error match={mid} error={type(exc).__name__}:{exc}", flush=True)
        try:
            contexts.append(self.fotmob.prematch_context(str(match.home), str(match.away), limit=limit))
        except Exception as exc:
            print(f"prematch_fotmob_embedded_error match={mid} error={type(exc).__name__}:{exc}", flush=True)
        try:
            contexts.append(self.scores365.prematch_context(str(match.home), str(match.away), limit=limit))
        except Exception as exc:
            print(f"prematch_365scores_error match={mid} error={type(exc).__name__}:{exc}", flush=True)

        self._prematch_context[mid] = self._merge_history_contexts(contexts, limit)
        ctx = self._prematch_context[mid]
        state = "READY" if self._history_ready(ctx, minimum) else "RETRY"
        print(
            f"PREMATCH_DATA match={match.home} - {match.away} state={state} sources={','.join(ctx.get('sources') or []) or 'none'} "
            f"home={len(ctx.get('home_recent') or [])} away={len(ctx.get('away_recent') or [])} "
            f"homeVenue={len(ctx.get('home_at_home') or [])} awayVenue={len(ctx.get('away_away') or [])} h2h={len(ctx.get('h2h') or [])} "
            f"halfRows={ctx.get('half_score_matches') or 0} trends={int(bool(ctx.get('has_trends')))} "
            f"suppressed={','.join(ctx.get('suppressed_sources') or []) or '-'} raw={ctx.get('source_raw_matches') or {}}",
            flush=True,
        )
        return ctx

    @staticmethod
    def _provider_payload(provider_match: Any) -> dict[str, Any]:
        return {"id": provider_match.provider_match_id, "stats": dict(provider_match.stats or {}), "meta": dict(provider_match.meta or {})}

    def _refresh_secondary(self, match: Any, minute: int) -> int:
        mid = str(match.provider_match_id); cache = self._secondary_cache.setdefault(mid, {}); refreshed = 0
        if not self._secondary_due(mid, minute): return refreshed
        try:
            fm = self.fotmob.enrich(match.home, match.away)
            if fm: cache[fm.provider] = self._provider_payload(fm); refreshed += 1
        except Exception as exc: print(f"fotmob_enrich_error match={mid} error={type(exc).__name__}:{exc}", flush=True)
        try:
            sc = self.scores365.enrich(match.home, match.away)
            if sc: cache[sc.provider] = self._provider_payload(sc); refreshed += 1
        except Exception as exc: print(f"scores365_enrich_error match={mid} error={type(exc).__name__}:{exc}", flush=True)
        return refreshed

    def _attach_secondary_cache(self, record: dict[str, Any], match_id: str) -> None:
        for name, payload in (self._secondary_cache.get(str(match_id)) or {}).items():
            record.setdefault("providers", {})[name] = {"id": payload.get("id"), "stats": dict(payload.get("stats") or {}), "meta": dict(payload.get("meta") or {})}

    def _live_record(self, match: Any, now: datetime) -> dict[str, Any]:
        minute = int(match.minute or 0)
        record: dict[str, Any] = {"schema_version":1,"captured_at":now.isoformat(),"source_observed_at":now.isoformat(),"ingested_at":datetime.now(timezone.utc).isoformat(),"match":{"flashscore_event_id":match.provider_match_id,"home":match.home,"away":match.away,"league":match.league,"minute":minute,"home_score":match.home_score,"away_score":match.away_score,"is_halftime":match.is_halftime,"status_code":match.meta.get("status_code",""),"is_finished":False},"providers":{"flashscore":{"id":match.provider_match_id,"stats":{},"meta":match.meta}},"prefilter":{"score":0.0,"candidate":False,"reasons":[]},"prematch_context":self._history_for(match)}
        if self._eligible_for_detail(minute, match.is_halftime):
            fs_stats=self.flashscore.fetch_stats(match.provider_match_id);goals=self.flashscore.fetch_goal_timeline(match.provider_match_id)
            record["providers"]["flashscore"]={"id":match.provider_match_id,"stats":fs_stats,"meta":{**match.meta,"goal_timeline":goals}}
            pref=football_prefilter(fs_stats,minute,threshold=self.prefilter_threshold);record["prefilter"]={"score":pref.score,"candidate":pref.candidate,"reasons":list(pref.reasons)}
        self._attach_secondary_cache(record,str(match.provider_match_id));return record

    def _final_record(self, match_id: str, info: dict[str, Any], state: dict[str, Any], now: datetime) -> dict[str, Any]:
        goals=self.flashscore.fetch_goal_timeline(match_id);meta=dict(info.get("meta") or {});meta.update({"status_code":state.get("status_code",""),"coarse_status":state.get("coarse_status","3"),"goal_timeline":goals,"is_finished":True})
        record={"schema_version":1,"captured_at":now.isoformat(),"source_observed_at":now.isoformat(),"ingested_at":datetime.now(timezone.utc).isoformat(),"match":{"flashscore_event_id":match_id,"home":info.get("home","?"),"away":info.get("away","?"),"league":info.get("league",""),"minute":90,"home_score":int(state.get("home_score") or 0),"away_score":int(state.get("away_score") or 0),"is_halftime":False,"status_code":state.get("status_code",""),"is_finished":True},"providers":{"flashscore":{"id":match_id,"stats":{},"meta":meta}},"prefilter":{"score":0.0,"candidate":False,"reasons":["match_finished"]},"prematch_context":self._prematch_context.get(match_id,{})}
        self._attach_secondary_cache(record,match_id);return record

    def collect_once(self) -> dict[str, int]:
        now=datetime.now(timezone.utc);matches=self.flashscore.live_matches();current_ids={str(m.provider_match_id) for m in matches};counters={"live":len(matches),"detail":0,"candidate":0,"secondary":0,"final":0,"errors":0}
        for match in matches:
            try:
                mid=str(match.provider_match_id);minute=int(match.minute or 0);self._tracked_matches[mid]={"home":match.home,"away":match.away,"league":match.league,"meta":dict(match.meta or {})}
                if minute>=90:continue
                counters["secondary"]+=self._refresh_secondary(match,minute);record=self._live_record(match,now)
                if self._eligible_for_detail(minute,match.is_halftime):counters["detail"]+=1
                pref=record.get("prefilter") or {}
                if pref.get("candidate") or match.is_halftime:self._attach_flashscore_assets(record,match.meta)
                if pref.get("candidate"):counters["candidate"]+=1
                self._append(record,now)
            except Exception as exc:
                counters["errors"]+=1;self._append({"schema_version":1,"captured_at":now.isoformat(),"match":{"flashscore_event_id":match.provider_match_id,"home":match.home,"away":match.away},"collector_error":f"{type(exc).__name__}: {exc}"},now)
        missing=set(self._tracked_matches)-current_ids
        if missing:
            try:
                states=self.flashscore.event_states(missing)
                for mid in list(missing):
                    state=states.get(mid)
                    if not state or not bool(state.get("is_finished")):continue
                    final_record=self._final_record(mid,self._tracked_matches[mid],state,now);self._append(final_record,now);self._append({**final_record,"captured_at":datetime.now(timezone.utc).isoformat()},now);counters["final"]+=1
                    self._tracked_matches.pop(mid,None);self._last_secondary_minute.pop(mid,None);self._secondary_cache.pop(mid,None);self._prematch_context.pop(mid,None);self._prematch_last_attempt.pop(mid,None)
            except Exception as exc:counters["errors"]+=1;print(f"final_state_error={type(exc).__name__}:{exc}",flush=True)
        return counters

    def stop(self,*_:object)->None:self._stop=True
    def run_forever(self,interval_seconds:int=60)->None:
        interval_seconds=max(30,int(interval_seconds))
        while not self._stop:
            started=time.monotonic()
            try:counters=self.collect_once();print(json.dumps({"collector":counters,"at":datetime.now(timezone.utc).isoformat()}),flush=True)
            except Exception as exc:print(json.dumps({"collector_error":f"{type(exc).__name__}: {exc}"}),flush=True)
            elapsed=time.monotonic()-started;end=time.monotonic()+max(1.0,interval_seconds-elapsed)
            while not self._stop and time.monotonic()<end:time.sleep(min(1.0,end-time.monotonic()))


def main()->None:
    parser=argparse.ArgumentParser(description="Collect GOOL live football snapshots");parser.add_argument("--data-dir",default=os.getenv("RUNTIME_DATA_DIR","data")+"/raw/live");parser.add_argument("--interval",type=int,default=int(os.getenv("LIVE_INTERVAL_SECONDS","60")));parser.add_argument("--prefilter",type=float,default=float(os.getenv("CORE_ANALYSIS_PREFILTER","50")));parser.add_argument("--secondary-interval",type=int,default=int(os.getenv("SECONDARY_PROVIDER_INTERVAL_MINUTES","3")));args=parser.parse_args();collector=LiveSnapshotCollector(data_dir=args.data_dir,prefilter_threshold=args.prefilter,secondary_interval_minutes=args.secondary_interval);signal.signal(signal.SIGINT,collector.stop);signal.signal(signal.SIGTERM,collector.stop);collector.run_forever(interval_seconds=args.interval)


if __name__=="__main__":main()
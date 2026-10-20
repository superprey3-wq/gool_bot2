"""One-shot real-source diagnostic for original GOOL Football V5/V4 bridge.

No Telegram, no betting alerts and no credentials. Intended for a CI smoke step
only; network availability in GitHub Actions does NOT guarantee host availability.
"""
from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gool_bot2.providers.flashscore import FlashscoreProvider
from gool_bot2.flashscore_odds import fetch_event_odds
from gool_bot2.football_v5_shadow import forecast_from_history, _observations, _name, _team_view
from gool_bot2.lite_football_v5_adapter import normalized_full_market, history_identity_name, original_v5_picks


def main() -> None:
    start = time.monotonic()
    provider = FlashscoreProvider()
    result: dict[str, object] = {}
    try:
        pre = provider.scheduled_matches_for_day(0)
        result["scheduled"] = len(pre)
    except Exception as e:
        pre = []
        result["scheduled_error"] = type(e).__name__
    try:
        live = provider.live_matches()
        result["live"] = len(live)
        if live:
            snapshot = provider.fetch_stats(str(live[0].provider_match_id))
            result["first_live_stat_fields"] = sorted(snapshot or {})
    except Exception as e:
        result["live_error"] = type(e).__name__
    if pre:
        future = [m for m in pre if float((m.meta or {}).get("scheduled_start_ts") or 0) > time.time()+300]
        future.sort(key=lambda m: float((m.meta or {}).get("scheduled_start_ts") or 0))
        result["upcoming_unstarted"] = len(future)
        m = future[0] if future else pre[0]
        history = {}
        forecast = {}
        try:
            history = provider.fetch_match_history(m.provider_match_id, m.home, m.away, limit=10)
            result["history_rows"] = {
                key:len(history.get(key) or [])
                for key in ("home_recent","away_recent","h2h")
            }
            forecast = forecast_from_history(
                home=history_identity_name(m.home, history),
                away=history_identity_name(m.away, history),
                kickoff=float((m.meta or {}).get("scheduled_start_ts") or 0),
                context=history,
            )
            result["v5_status"] = forecast.get("status")
            result["v5_home_games"] = forecast.get("home_games")
            result["v5_away_games"] = forecast.get("away_games")
            kickoff = float((m.meta or {}).get("scheduled_start_ts") or 0)
            observations = _observations(history, kickoff)
            result["valid_dated_rows"] = len(observations)
            result["valid_home_id_matches"] = sum(1 for row in observations if _team_view(row, _name(history_identity_name(m.home, history))))
            result["valid_away_id_matches"] = sum(1 for row in observations if _team_view(row, _name(history_identity_name(m.away, history))))
            result["fixture_names"] = [m.home, m.away]
            result["v5_history_identities"] = [history_identity_name(m.home, history), history_identity_name(m.away, history)]
            result["history_home_names_sample"] = [[v.get("home"),v.get("away"),v.get("timestamp")] for v in (history.get("home_recent") or [])[:2]]
            result["history_away_names_sample"] = [[v.get("home"),v.get("away"),v.get("timestamp")] for v in (history.get("away_recent") or [])[:2]]
            result["kickoff_ts"] = kickoff
        except Exception as e:
            result["history_error"] = type(e).__name__
        try:
            quotes = fetch_event_odds(m.provider_match_id, timeout=8) or {}
            result["paired_full_market_rows"] = len(normalized_full_market(quotes)["candidates"])
            if forecast.get("status") == "READY":
                wagers, _ = original_v5_picks(
                    history=history, raw_odds=quotes,
                    event_id=m.provider_match_id, home=m.home, away=m.away,
                    league=m.league or "", kickoff=float((m.meta or {}).get("scheduled_start_ts") or 0),
                    forecast=forecast,
                )
                result["v5_real_priced_count"] = len(wagers)
        except Exception as e:
            result["odds_error"] = type(e).__name__
    result["rss_peak_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    result["cpu_seconds"] = round(
        resource.getrusage(resource.RUSAGE_SELF).ru_utime +
        resource.getrusage(resource.RUSAGE_SELF).ru_stime, 2,
    )
    result["elapsed_sec"] = round(time.monotonic()-start,2)
    print("GOOL_ORIGINAL_REAL_SOURCE_PROBE "+json.dumps(result, ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit


BETFAIR_ROOT = "https://www.betfair.com"
BETFAIR_FOOTBALL_URL = f"{BETFAIR_ROOT}/exchange/plus/en/football-betting-1"
BETFAIR_INPLAY_URL = f"{BETFAIR_FOOTBALL_URL}/inplay"


def _state_path() -> Path:
    runtime = Path(os.getenv("RUNTIME_DATA_DIR", "data"))
    return Path(os.getenv("BETFAIR_PUBLIC_STATE", str(runtime / "live" / "betfair_public_state.json")))


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _money_number(value: str) -> float:
    try:
        return float(str(value or "").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _canonical_href(href: str) -> str:
    url = urljoin(BETFAIR_ROOT, str(href or "").strip())
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._depth = 0
        self._parts: list[str] = []
        self.rows: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "a" and self._href is None:
            href = dict(attrs).get("href")
            if href:
                self._href = str(href)
                self._depth = 1
                self._parts = []
                return
        if self._href is not None:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._href is None:
            return
        self._depth -= 1
        if self._depth <= 0:
            text = " ".join(" ".join(self._parts).split())
            if text:
                self.rows.append((self._href, text))
            self._href = None
            self._parts = []
            self._depth = 0

    def handle_data(self, data: str) -> None:
        if self._href is not None and data:
            self._parts.append(data)


_MATCHED_RE = re.compile(r"Matched\s+bets\s*£\s*([0-9][0-9,]*(?:\.\d+)?)", re.I)
_PRICE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*£\s*([0-9][0-9,]*(?:\.\d+)?)")
_UNMATCHED_TAIL_RE = re.compile(
    r"\s+\d[\d,]*(?:\.\d+)?\s+Unmatched\s+bets\s+\d[\d,]*(?:\.\d+)?\s*$",
    re.I,
)


def _clean_name(prefix: str) -> tuple[str, bool, str]:
    text = _UNMATCHED_TAIL_RE.sub("", " ".join(str(prefix or "").split())).strip()
    live = bool(re.match(r"^\d{1,3}'", text)) or text.casefold().startswith("in-play")
    start_label = "LIVE" if live else ""

    if live:
        text = re.sub(r"^\d{1,3}'\s*\d{0,4}\s*", "", text).strip()
    else:
        m = re.match(r"^(Today|Tomorrow)\s+(\d{1,2}:\d{2})\s+", text, re.I)
        if m:
            start_label = f"{m.group(1).title()} {m.group(2)}"
            text = text[m.end():].strip()
        else:
            m = re.match(r"^([A-Z][a-z]{2}(?:\s+\d{1,2})?|\d{1,2}\s+[A-Z][a-z]{2})\s+(\d{1,2}:\d{2})\s+", text)
            if m:
                start_label = f"{m.group(1)} {m.group(2)}"
                text = text[m.end():].strip()
    return text or "?", live, start_label


def _parse_anchor(href: str, text: str, *, force_live: bool = False) -> dict[str, Any] | None:
    if "Matched" not in text or "bets" not in text:
        return None
    matched = _MATCHED_RE.search(text)
    if matched is None:
        return None
    before = text[: matched.start()].strip()
    name, live, start_label = _clean_name(before)
    live = bool(live or force_live)
    if force_live:
        start_label = "LIVE"

    after = text[matched.end():]
    pairs = [
        {"odd": _number(odd), "available_gbp": _money_number(amount)}
        for odd, amount in _PRICE_RE.findall(after)
    ]
    runners: list[dict[str, Any]] = []
    labels = ("П1", "X", "П2")
    for index, label in enumerate(labels):
        base = index * 2
        back = pairs[base] if len(pairs) > base else None
        lay = pairs[base + 1] if len(pairs) > base + 1 else None
        if back is None and lay is None:
            continue
        runners.append({"label": label, "best_back": back, "best_lay": lay})

    canonical = _canonical_href(href)
    if not canonical or "/football/" not in canonical:
        return None
    return {
        "event_key": canonical,
        "url": canonical,
        "name": name,
        "in_running": live,
        "start_label": start_label,
        "matched_gbp": _money_number(matched.group(1)),
        "runners": runners,
    }


def parse_betfair_html(html_text: str, *, force_live: bool = False) -> list[dict[str, Any]]:
    parser = _AnchorCollector()
    try:
        parser.feed(str(html_text or ""))
    except Exception:
        return []
    latest: dict[str, dict[str, Any]] = {}
    for href, text in parser.rows:
        row = _parse_anchor(href, text, force_live=force_live)
        if row is None:
            continue
        key = str(row.get("event_key") or "")
        old = latest.get(key)
        if old is None or _number(row.get("matched_gbp")) >= _number(old.get("matched_gbp")):
            latest[key] = row
    return list(latest.values())


def _mid_odd(runner: dict[str, Any]) -> float | None:
    odds = []
    for key in ("best_back", "best_lay"):
        odd = _number((runner.get(key) or {}).get("odd"))
        if odd > 1.0:
            odds.append(odd)
    return sum(odds) / len(odds) if odds else None


def _runners_by_label(event: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("label") or ""): row
        for row in (event.get("runners") or [])
        if isinstance(row, dict) and row.get("label")
    }


def attach_price_flow(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    out = dict(current)
    if not previous:
        out["flow"] = {"ready": False, "level": "WARMING"}
        return out
    old_matched = _number(previous.get("matched_gbp"))
    new_matched = _number(current.get("matched_gbp"))
    matched_delta = max(0.0, new_matched - old_matched)
    old_runners = _runners_by_label(previous)
    new_runners = _runners_by_label(current)
    moves: list[dict[str, Any]] = []
    for label, new_runner in new_runners.items():
        old_runner = old_runners.get(label)
        if not old_runner:
            continue
        old_odd = _mid_odd(old_runner)
        new_odd = _mid_odd(new_runner)
        if old_odd is None or new_odd is None:
            continue
        old_p = 1.0 / old_odd
        new_p = 1.0 / new_odd
        moves.append(
            {
                "outcome": label,
                "old_odd": round(old_odd, 4),
                "new_odd": round(new_odd, 4),
                "implied_delta_pp": round((new_p - old_p) * 100.0, 3),
            }
        )
    strongest = max(moves, key=lambda row: float(row.get("implied_delta_pp") or -999.0), default=None)
    delta_pp = _number((strongest or {}).get("implied_delta_pp"), -999.0)
    min_delta = max(0.0, _number(os.getenv("BETFAIR_FLOW_MIN_MATCHED_DELTA_GBP", "100"), 100.0))
    strong_delta = max(min_delta, _number(os.getenv("BETFAIR_FLOW_STRONG_MATCHED_DELTA_GBP", "500"), 500.0))
    min_pp = max(0.1, _number(os.getenv("BETFAIR_FLOW_MIN_PRICE_PP", "0.6"), 0.6))
    strong_pp = max(min_pp, _number(os.getenv("BETFAIR_FLOW_STRONG_PRICE_PP", "1.5"), 1.5))
    if strongest is not None and matched_delta >= strong_delta and delta_pp >= strong_pp:
        level = "STRONG_FLOW"
    elif strongest is not None and matched_delta >= min_delta and delta_pp >= min_pp:
        level = "FLOW"
    else:
        level = "NEUTRAL"
    out["flow"] = {
        "ready": bool(moves),
        "level": level,
        "outcome": (strongest or {}).get("outcome"),
        "matched_delta_gbp": round(matched_delta, 2),
        "implied_delta_pp": None if strongest is None else round(delta_pp, 3),
        "old_odd": (strongest or {}).get("old_odd"),
        "new_odd": (strongest or {}).get("new_odd"),
        "moves": moves,
    }
    return out


def load_betfair_state(path: Path | None = None) -> dict[str, Any]:
    state_path = path or _state_path()
    try:
        payload = json.loads(state_path.read_text("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_betfair_state(payload: dict[str, Any], path: Path | None = None) -> None:
    state_path = path or _state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(state_path)


async def _request_html(context: Any, url: str) -> tuple[int, str]:
    response = await context.request.get(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": BETFAIR_ROOT + "/",
        },
        timeout=20_000,
    )
    return int(response.status), await response.text()


async def _page_html(page: Any, url: str) -> tuple[int | None, str]:
    response = await page.goto(url, wait_until="domcontentloaded", timeout=35_000)
    await page.wait_for_timeout(2500)
    return (None if response is None else int(response.status)), await page.content()


async def collect_betfair_public(context: Any, page: Any, previous_state: dict[str, Any] | None = None) -> dict[str, Any]:
    previous_rows = {
        str(row.get("event_key") or ""): row
        for row in ((previous_state or {}).get("events") or [])
        if isinstance(row, dict) and row.get("event_key")
    }
    merged: dict[str, dict[str, Any]] = {}
    statuses: list[str] = []
    errors: list[str] = []
    for url, force_live in ((BETFAIR_FOOTBALL_URL, False), (BETFAIR_INPLAY_URL, True)):
        html_text = ""
        try:
            status, html_text = await _request_html(context, url)
            statuses.append(f"http:{status}")
        except Exception as exc:
            errors.append(f"http:{type(exc).__name__}:{exc}")
        rows = parse_betfair_html(html_text, force_live=force_live)
        if not rows:
            try:
                status, html_text = await _page_html(page, url)
                statuses.append(f"browser:{status}")
                rows = parse_betfair_html(html_text, force_live=force_live)
            except Exception as exc:
                errors.append(f"browser:{type(exc).__name__}:{exc}")
        for row in rows:
            key = str(row.get("event_key") or "")
            old = merged.get(key)
            if old is None or _number(row.get("matched_gbp")) >= _number(old.get("matched_gbp")):
                merged[key] = row
            elif row.get("in_running"):
                old["in_running"] = True
                old["start_label"] = "LIVE"

    events = [attach_price_flow(previous_rows.get(key), row) for key, row in merged.items()]
    events.sort(key=lambda row: _number(row.get("matched_gbp")), reverse=True)
    return {
        "captured_epoch": time.time(),
        "captured_at": _now_iso(),
        "source": "betfair_public_exchange",
        "available": bool(events),
        "events": events,
        "statuses": statuses,
        "error": "; ".join(errors)[:1000] if errors and not events else "",
    }


__all__ = [
    "BETFAIR_FOOTBALL_URL",
    "BETFAIR_INPLAY_URL",
    "attach_price_flow",
    "collect_betfair_public",
    "load_betfair_state",
    "parse_betfair_html",
    "save_betfair_state",
]

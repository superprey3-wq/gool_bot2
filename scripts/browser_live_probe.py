from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Page, Response, WebSocket, async_playwright


STAT_TERMS = (
    "xg",
    "expected goal",
    "shots on target",
    "on target",
    "shots",
    "big chance",
    "dangerous attack",
    "attack",
    "possession",
    "corner",
    "statistics",
    "stats",
    "trend",
    "insight",
    "scored first",
    "both teams scored",
    "over 2.5",
    "under 2.5",
    "win or draw",
    "won or drew",
    "last matches",
    "last games",
)

TARGETS = [
    {
        "name": "aiscore",
        "url": "https://m.aiscore.com/live",
        "detail_url": "https://m.aiscore.com/live/football-deportivo-cuenca-vs-tecnico-universitario",
    },
    {
        "name": "gooolll",
        "url": "https://gooolll.com/",
        "detail_url": "https://gooolll.com/match/match-apf-1607431",
    },
    {
        "name": "365scores",
        "url": "https://www.365scores.com/football/team/everton-107",
        "detail_url": "https://www.365scores.com/football/match/premier-league-7/everton-manchester-united-105-107-7",
    },
]


@dataclass
class NetworkHit:
    provider: str
    page_url: str
    resource_type: str
    status: int | None
    content_type: str | None
    url: str
    matched_terms: list[str]
    json_keys: list[str]
    body_preview: str | None


@dataclass
class SocketHit:
    provider: str
    page_url: str
    url: str
    frame_preview: str | None
    matched_terms: list[str]


def _terms(text: str) -> list[str]:
    folded = text.casefold()
    return [term for term in STAT_TERMS if term in folded]


def _keys(value: Any, *, max_depth: int = 4, max_keys: int = 80) -> list[str]:
    out: list[str] = []

    def walk(obj: Any, prefix: str, depth: int) -> None:
        if depth > max_depth or len(out) >= max_keys:
            return
        if isinstance(obj, dict):
            for key, child in obj.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                out.append(path)
                if len(out) >= max_keys:
                    return
                walk(child, path, depth + 1)
        elif isinstance(obj, list):
            for child in obj[:3]:
                walk(child, prefix + "[]", depth + 1)

    walk(value, "", 0)
    return out


def _stat_context(text: str) -> list[str]:
    lines = [re.sub(r"\s+", " ", row).strip() for row in text.splitlines()]
    lines = [row for row in lines if row]
    wanted: list[str] = []
    for idx, row in enumerate(lines):
        if not _terms(row):
            continue
        lo = max(0, idx - 2)
        hi = min(len(lines), idx + 4)
        context = " | ".join(lines[lo:hi])
        if context not in wanted:
            wanted.append(context)
        if len(wanted) >= 30:
            break
    return wanted


async def probe_provider(browser: Browser, name: str, url: str, detail_url: str) -> dict[str, Any]:
    context = await browser.new_context(
        locale="en-US",
        viewport={"width": 1440, "height": 1000},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    network: list[NetworkHit] = []
    sockets: list[SocketHit] = []
    seen_urls: set[str] = set()
    seen_socket_frames: set[tuple[str, str]] = set()

    async def inspect_response(response: Response) -> None:
        req = response.request
        if req.resource_type not in {"xhr", "fetch", "document"}:
            return
        if response.url in seen_urls and req.resource_type != "document":
            return
        seen_urls.add(response.url)
        try:
            headers = await response.all_headers()
        except Exception:
            headers = {}
        content_type = headers.get("content-type", "")
        interesting_url = bool(re.search(r"api|graphql|match|live|stat|score|event|supabase|trend|insight", response.url, re.I))
        if req.resource_type == "document" and not interesting_url:
            return

        text = ""
        keys: list[str] = []
        matched = _terms(response.url)
        if req.resource_type in {"xhr", "fetch"}:
            try:
                body = await response.body()
                if len(body) > 1_500_000:
                    body = body[:1_500_000]
                text = body.decode("utf-8", errors="ignore")
                matched = sorted(set(matched + _terms(text)))
                if "json" in content_type.casefold() or text.lstrip().startswith(("{", "[")):
                    try:
                        parsed = json.loads(text)
                        keys = _keys(parsed)
                        matched = sorted(set(matched + _terms(" ".join(keys))))
                    except Exception:
                        pass
            except Exception:
                pass

        if interesting_url or matched or req.resource_type in {"xhr", "fetch"}:
            network.append(
                NetworkHit(
                    provider=name,
                    page_url=page.url,
                    resource_type=req.resource_type,
                    status=response.status,
                    content_type=content_type or None,
                    url=response.url,
                    matched_terms=matched,
                    json_keys=keys[:80],
                    body_preview=re.sub(r"\s+", " ", text[:700]).strip() or None,
                )
            )

    def on_response(response: Response) -> None:
        asyncio.create_task(inspect_response(response))

    def on_websocket(ws: WebSocket) -> None:
        def on_frame(payload: str | bytes) -> None:
            try:
                if isinstance(payload, bytes):
                    text = payload[:1000].decode("utf-8", errors="ignore")
                else:
                    text = str(payload)[:1000]
                key = (ws.url, text[:140])
                if key in seen_socket_frames or len(sockets) >= 60:
                    return
                seen_socket_frames.add(key)
                sockets.append(
                    SocketHit(
                        provider=name,
                        page_url=page.url,
                        url=ws.url,
                        frame_preview=re.sub(r"\s+", " ", text).strip() or None,
                        matched_terms=_terms(text + " " + ws.url),
                    )
                )
            except Exception:
                return

        ws.on("framereceived", on_frame)

    page.on("response", on_response)
    page.on("websocket", on_websocket)

    pages_visited: list[dict[str, Any]] = []
    for step, target in enumerate([url, detail_url]):
        print(f"PROBE_NAV provider={name} step={step} url={target}")
        try:
            response = await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(10_000)
            title = await page.title()
            text = await page.locator("body").inner_text(timeout=8_000)
            body_terms = _terms(text[:300_000])
            contexts = _stat_context(text[:300_000])
            pages_visited.append(
                {
                    "url": page.url,
                    "status": None if response is None else response.status,
                    "title": title,
                    "body_terms": body_terms,
                    "stat_context": contexts,
                    "body_preview": re.sub(r"\s+", " ", text[:1600]).strip(),
                }
            )
            print(
                "PROBE_PAGE "
                f"provider={name} status={None if response is None else response.status} "
                f"url={page.url} terms={','.join(body_terms) or '-'} title={title[:120]}"
            )
            for line in contexts[:16]:
                print(f"PROBE_VISIBLE_STATS provider={name} {line[:600]}")
        except Exception as exc:
            pages_visited.append({"url": target, "error": f"{type(exc).__name__}:{exc}"})
            print(f"PROBE_NAV_ERROR provider={name} url={target} type={type(exc).__name__} error={exc}")

    await page.wait_for_timeout(2_000)
    await context.close()

    strong_network = [row for row in network if row.matched_terms]
    strong_sockets = [row for row in sockets if row.matched_terms]
    print(
        f"PROBE_SUMMARY provider={name} pages={len(pages_visited)} "
        f"network={len(network)} stat_network={len(strong_network)} "
        f"sockets={len(sockets)} stat_sockets={len(strong_sockets)}"
    )

    api_rows = []
    for hit in network:
        if re.search(r"api|graphql|supabase|match|stat|event|trend|insight", hit.url, re.I):
            if hit.url not in [row.url for row in api_rows]:
                api_rows.append(hit)
        if len(api_rows) >= 50:
            break
    for hit in api_rows:
        print(
            "PROBE_API "
            f"provider={name} status={hit.status} type={hit.resource_type} "
            f"terms={','.join(hit.matched_terms) or '-'} content_type={hit.content_type or '-'} url={hit.url}"
        )
        if hit.json_keys:
            print(f"PROBE_KEYS provider={name} keys={' | '.join(hit.json_keys[:35])}")
        if hit.body_preview and hit.matched_terms:
            print(f"PROBE_BODY provider={name} preview={hit.body_preview[:700]}")

    for hit in strong_sockets[:12]:
        print(
            "PROBE_SOCKET "
            f"provider={name} terms={','.join(hit.matched_terms) or '-'} url={hit.url} "
            f"frame={(hit.frame_preview or '')[:350]}"
        )

    return {
        "provider": name,
        "start_url": url,
        "detail_url": detail_url,
        "pages": pages_visited,
        "network": [asdict(row) for row in network[:200]],
        "sockets": [asdict(row) for row in sockets[:80]],
        "stat_network_count": len(strong_network),
        "stat_socket_count": len(strong_sockets),
    }


async def main() -> int:
    report: dict[str, Any] = {"providers": []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            for target in TARGETS:
                report["providers"].append(
                    await probe_provider(
                        browser,
                        target["name"],
                        target["url"],
                        target["detail_url"],
                    )
                )
        finally:
            await browser.close()

    out = Path("browser_probe_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    usable = sum(
        int(provider.get("stat_network_count") or 0) + int(provider.get("stat_socket_count") or 0)
        for provider in report["providers"]
    )
    print(f"PROBE_DONE usable_stat_channels={usable} report={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

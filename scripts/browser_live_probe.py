from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, Page, Response, WebSocket, async_playwright


STAT_TERMS = (
    "xg",
    "expected goal",
    "shots on target",
    "shots",
    "big chance",
    "dangerous attack",
    "attack",
    "possession",
    "corner",
    "statistics",
    "stats",
)

TARGETS = [
    {"name": "aiscore", "url": "https://www.aiscore.com/live"},
    {"name": "gooolll", "url": "https://gooolll.com/"},
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


async def _candidate_match_url(page: Page) -> str | None:
    anchors = await page.locator("a[href]").evaluate_all(
        "els => els.map(a => ({href:a.href, text:(a.innerText||'').trim()})).slice(0, 1500)"
    )
    host = urlparse(page.url).netloc
    scored: list[tuple[int, str]] = []
    for row in anchors:
        href = str((row or {}).get("href") or "")
        text = str((row or {}).get("text") or "")
        if not href or urlparse(href).netloc != host:
            continue
        folded = (href + " " + text).casefold()
        score = 0
        if "/match/" in href or "match-" in href:
            score += 6
        if "live" in folded:
            score += 3
        if "stats" in folded or "statistics" in folded:
            score += 2
        if score:
            scored.append((score, href))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


async def probe_provider(browser: Browser, name: str, url: str) -> dict[str, Any]:
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
        try:
            req = response.request
            if req.resource_type not in {"xhr", "fetch", "document"}:
                return
            if response.url in seen_urls and req.resource_type != "document":
                return
            seen_urls.add(response.url)
            content_type = (await response.all_headers()).get("content-type", "")
            interesting_url = bool(re.search(r"api|graphql|match|live|stat|score|event", response.url, re.I))
            if req.resource_type == "document" and not interesting_url:
                return
            if not ("json" in content_type.casefold() or interesting_url):
                return
            body = await response.body()
            if len(body) > 1_500_000:
                body = body[:1_500_000]
            text = body.decode("utf-8", errors="ignore")
            matched = _terms(text + " " + response.url)
            parsed: Any = None
            keys: list[str] = []
            if "json" in content_type.casefold() or text.lstrip().startswith(("{", "[")):
                try:
                    parsed = json.loads(text)
                    keys = _keys(parsed)
                    matched = sorted(set(matched + _terms(" ".join(keys))))
                except Exception:
                    pass
            if matched or req.resource_type == "xhr" or req.resource_type == "fetch":
                preview = re.sub(r"\s+", " ", text[:700]).strip() or None
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
                        body_preview=preview,
                    )
                )
        except Exception as exc:
            print(f"PROBE_RESPONSE_ERROR provider={name} type={type(exc).__name__} error={exc}")

    def on_response(response: Response) -> None:
        asyncio.create_task(inspect_response(response))

    def on_websocket(ws: WebSocket) -> None:
        def on_frame(payload: str | bytes) -> None:
            try:
                if isinstance(payload, bytes):
                    text = payload[:800].decode("utf-8", errors="ignore")
                else:
                    text = str(payload)[:800]
                key = (ws.url, text[:120])
                if key in seen_socket_frames or len(sockets) >= 30:
                    return
                seen_socket_frames.add(key)
                matched = _terms(text + " " + ws.url)
                sockets.append(
                    SocketHit(
                        provider=name,
                        page_url=page.url,
                        url=ws.url,
                        frame_preview=re.sub(r"\s+", " ", text).strip() or None,
                        matched_terms=matched,
                    )
                )
            except Exception:
                return

        ws.on("framereceived", on_frame)

    page.on("response", on_response)
    page.on("websocket", on_websocket)

    pages_visited: list[dict[str, Any]] = []
    for step, target in enumerate([url, None]):
        if target is None:
            target = await _candidate_match_url(page)
            if not target:
                break
        print(f"PROBE_NAV provider={name} step={step} url={target}")
        try:
            response = await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(8_000)
            title = await page.title()
            text = await page.locator("body").inner_text(timeout=8_000)
            body_terms = _terms(text[:250_000])
            pages_visited.append(
                {
                    "url": page.url,
                    "status": None if response is None else response.status,
                    "title": title,
                    "body_terms": body_terms,
                    "body_preview": re.sub(r"\s+", " ", text[:1200]).strip(),
                }
            )
            print(
                "PROBE_PAGE "
                f"provider={name} status={None if response is None else response.status} "
                f"url={page.url} terms={','.join(body_terms) or '-'} title={title[:120]}"
            )
        except Exception as exc:
            pages_visited.append({"url": target, "error": f"{type(exc).__name__}:{exc}"})
            print(f"PROBE_NAV_ERROR provider={name} url={target} type={type(exc).__name__} error={exc}")
            break

    await page.wait_for_timeout(2_000)
    await context.close()

    strong_network = [row for row in network if row.matched_terms]
    strong_sockets = [row for row in sockets if row.matched_terms]
    print(
        f"PROBE_SUMMARY provider={name} pages={len(pages_visited)} "
        f"network={len(network)} stat_network={len(strong_network)} "
        f"sockets={len(sockets)} stat_sockets={len(strong_sockets)}"
    )
    for hit in strong_network[:20]:
        print(
            "PROBE_ENDPOINT "
            f"provider={name} status={hit.status} type={hit.resource_type} "
            f"terms={','.join(hit.matched_terms)} url={hit.url}"
        )
        if hit.json_keys:
            print(f"PROBE_KEYS provider={name} keys={' | '.join(hit.json_keys[:35])}")
    for hit in strong_sockets[:10]:
        print(
            "PROBE_SOCKET "
            f"provider={name} terms={','.join(hit.matched_terms) or '-'} url={hit.url} "
            f"frame={(hit.frame_preview or '')[:300]}"
        )

    return {
        "provider": name,
        "start_url": url,
        "pages": pages_visited,
        "network": [asdict(row) for row in network[:120]],
        "sockets": [asdict(row) for row in sockets[:60]],
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
                    await probe_provider(browser, target["name"], target["url"])
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

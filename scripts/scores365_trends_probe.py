from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from playwright.async_api import Response, async_playwright

TARGET = "https://www.365scores.com/football/match/premier-league-7/everton-manchester-united-105-107-7"


def _interesting(url: str, text: str = "") -> bool:
    hay = f"{url} {text[:5000]}".casefold()
    return any(token in hay for token in (
        "trend", "insight", "last matches", "last games", "over 2.5",
        "both teams", "won or drew", "scored first", "bett", "lineTypeId".casefold(),
    ))


async def main() -> int:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1440, "height": 1100},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        async def inspect(response: Response) -> None:
            if response.request.resource_type not in {"xhr", "fetch", "document"}:
                return
            if response.url in seen and response.request.resource_type != "document":
                return
            seen.add(response.url)
            text = ""
            ctype = ""
            try:
                headers = await response.all_headers()
                ctype = headers.get("content-type", "")
            except Exception:
                pass
            if response.request.resource_type in {"xhr", "fetch"}:
                try:
                    body = await response.body()
                    text = body[:1_000_000].decode("utf-8", errors="ignore")
                except Exception:
                    pass
            if _interesting(response.url, text):
                preview = re.sub(r"\s+", " ", text[:1600]).strip()
                row = {
                    "status": response.status,
                    "type": response.request.resource_type,
                    "content_type": ctype,
                    "url": response.url,
                    "preview": preview,
                }
                rows.append(row)
                print(f"S365_TREND_API status={response.status} type={response.request.resource_type} url={response.url}")
                if preview:
                    print(f"S365_TREND_BODY {preview[:1200]}")

        def on_response(response: Response) -> None:
            asyncio.create_task(inspect(response))

        page.on("response", on_response)
        response = await page.goto(TARGET, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(8_000)
        print(f"S365_PAGE status={None if response is None else response.status} url={page.url}")

        body = await page.locator("body").inner_text(timeout=10_000)
        print("S365_BODY_TERMS", [term for term in ("Stats", "Trends", "Insights", "Over 2.5", "Both Teams") if term.casefold() in body.casefold()])

        # Try the visible tabs/buttons. 365Scores changes markup frequently, so
        # use text/role fallbacks and keep failures non-fatal.
        for label in ("Trends", "Insights"):
            clicked = False
            for locator in (
                page.get_by_role("tab", name=re.compile(label, re.I)),
                page.get_by_role("button", name=re.compile(label, re.I)),
                page.get_by_text(re.compile(rf"^{label}$", re.I)),
            ):
                try:
                    if await locator.count():
                        await locator.first.click(timeout=5_000)
                        clicked = True
                        print(f"S365_CLICK label={label} ok=1")
                        await page.wait_for_timeout(6_000)
                        break
                except Exception as exc:
                    print(f"S365_CLICK label={label} error={type(exc).__name__}:{exc}")
            if not clicked:
                print(f"S365_CLICK label={label} ok=0")

        body2 = await page.locator("body").inner_text(timeout=10_000)
        trend_lines = []
        for line in body2.splitlines():
            cleaned = re.sub(r"\s+", " ", line).strip()
            if not cleaned:
                continue
            low = cleaned.casefold()
            if any(t in low for t in ("last matches", "last games", "over 2.5", "both teams", "won or drew", "scored first")):
                trend_lines.append(cleaned)
        for line in trend_lines[:50]:
            print(f"S365_VISIBLE_TREND {line[:500]}")

        await page.wait_for_timeout(2_000)
        await context.close()
        await browser.close()

    Path("scores365_trends_probe.json").write_text(
        json.dumps({"target": TARGET, "network": rows, "visible_trends": trend_lines[:100]}, ensure_ascii=False, indent=2),
        "utf-8",
    )
    print(f"S365_TREND_DONE network={len(rows)} visible={len(trend_lines)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

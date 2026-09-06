from __future__ import annotations

import asyncio
import json

from playwright.async_api import async_playwright

from gool_bot2.browser_context_worker import _stats_from_game, _trim_trends


PAGE = "https://www.365scores.com/football/match/premier-league-7/everton-manchester-united-105-107-7"
GAME = "https://webws.365scores.com/web/game/?appTypeId=5&langId=1&timezoneName=UTC&userCountryId=321&gameId=4742078&topBookmaker=103"
TRENDS = "https://webws.365scores.com/web/trends/?appTypeId=5&langId=1&timezoneName=UTC&userCountryId=321&games=4742078&topBookmaker=103"


async def _json(page, url: str):
    result = await page.evaluate(
        """async (url) => {
            const r = await fetch(url, {credentials: 'include', cache: 'no-store'});
            return {status: r.status, text: await r.text()};
        }""",
        url,
    )
    return int(result["status"]), json.loads(result["text"])


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(locale="en-US")

        async def route_handler(route):
            if route.request.resource_type in {"image", "media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", route_handler)
        page = await context.new_page()
        response = await page.goto(PAGE, wait_until="domcontentloaded", timeout=60_000)
        if response is None or response.status >= 400:
            raise RuntimeError(f"page_status={None if response is None else response.status}")
        await page.wait_for_timeout(1500)

        game_status, game_payload = await _json(page, GAME)
        trend_status, trend_payload = await _json(page, TRENDS)
        if game_status != 200 or trend_status != 200:
            raise RuntimeError(f"api_status game={game_status} trends={trend_status}")
        game = game_payload.get("game") or {}
        trends = _trim_trends(trend_payload)
        stats = _stats_from_game(game)
        if not isinstance(game, dict) or not game.get("id"):
            raise RuntimeError("game_payload_missing")
        if not isinstance(trend_payload.get("trends"), list):
            raise RuntimeError("trends_payload_missing")
        if "shots" not in stats:
            raise RuntimeError("shot_stats_missing")

        print(
            "GOOL_BROWSER_PRODUCTION_SMOKE "
            f"page={response.status} game={game_status} trends={trend_status} "
            f"stats={sorted(stats)} trend_rows={len(trends)}"
        )
        for row in trends[:5]:
            print(
                f"GOOL_BROWSER_PRODUCTION_TREND pct={row.get('percentage')} "
                f"text={str(row.get('text') or row.get('cause') or '')[:180]}"
            )
        await context.close()
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

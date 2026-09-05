from __future__ import annotations

import re
import urllib.parse
import urllib.request

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/javascript,*/*"}


def fetch(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print("FETCH_ERROR", url, type(exc).__name__, exc)
        return ""


def main() -> None:
    root = "https://www.365scores.com/"
    html = fetch(root)
    print("HTML_LEN", len(html))
    sources = []
    for src in re.findall(r'<script[^>]+src=["\']([^"\']+)', html, flags=re.I):
        url = urllib.parse.urljoin(root, src)
        if url not in sources:
            sources.append(url)
    print("SCRIPTS", len(sources))
    patterns = (
        re.compile(r".{0,180}hasTopTrends.{0,320}", re.I),
        re.compile(r".{0,180}hasTrends.{0,320}", re.I),
        re.compile(r".{0,180}(?:top.?trends|game.?trends|trends/|/trends).{0,320}", re.I),
        re.compile(r".{0,180}(?:previousMeetings|recentMatches).{0,320}", re.I),
    )
    hits = 0
    for url in sources[:60]:
        body = fetch(url)
        if not body:
            continue
        low = body.casefold()
        if "trend" not in low and "previousmeetings" not in low and "recentmatches" not in low:
            continue
        print("BUNDLE", url, "LEN", len(body))
        for pattern in patterns:
            for match in pattern.finditer(body):
                snippet = match.group(0).replace("\n", " ")
                print("JS_HIT", snippet[:700])
                hits += 1
                if hits >= 80:
                    return
    print("TOTAL_HITS", hits)


if __name__ == "__main__":
    main()

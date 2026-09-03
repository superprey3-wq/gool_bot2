from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc

W, H = 1080, 985
BG = sc.BG
PANEL = sc.PANEL
PANEL2 = sc.PANEL2
TEXT = sc.TEXT
MUTED = sc.MUTED
LINE = sc.LINE
GREEN = sc.THEMES["another_goal"][0]
RED = sc.RED
THEMES = {
    "both_teams_to_score": ((190, 83, 255), "ОБЕ ЗАБЬЮТ — ДА"),
    "team_to_score": ((52, 190, 255), "КОМАНДА ЗАБЬЁТ"),
}


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font, fill) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    draw.text(((W - (box[2] - box[0])) / 2, y), str(text), font=font, fill=fill)


def _save(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _num(value: Any, digits: int = 0) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "—"


def _provider_pair(record: dict[str, Any], key: str, digits: int = 0, suffix: str = "") -> str:
    try:
        home, away = sc.provider_pair(record, key)
    except Exception:
        return "—"
    if home is None or away is None:
        return "—"
    if digits:
        return f"{float(home):.{digits}f}{suffix} : {float(away):.{digits}f}{suffix}"
    return f"{int(round(float(home)))}{suffix} : {int(round(float(away)))}{suffix}"


def _momentum_pair(momentum: dict[str, Any], key: str, digits: int = 0) -> str:
    home = momentum.get(f"home_{key}")
    away = momentum.get(f"away_{key}")
    if home is None or away is None:
        return "—"
    return f"{_num(home, digits)} : {_num(away, digits)}"


def _team_target(head: str, selected_side: str | None, hs: int, aws: int, team: Any) -> tuple[str, str]:
    if head != "team_to_score":
        return THEMES.get(head, THEMES["team_to_score"])[1], str(team or "обе команды забьют")
    side = "home" if selected_side == "home" else "away"
    goals = hs if side == "home" else aws
    line = goals + 0.5
    prefix = "ИТБ1" if side == "home" else "ИТБ2"
    label = "КОМАНДА ЗАБЬЁТ" if goals == 0 else "КОМАНДА ЗАБЬЁТ ЕЩЁ 1"
    detail = f"{team or 'Команда'} • {prefix} {line:g}"
    return label, detail


def _scoreless_team(head: str, selected_side: str | None, hs: int, aws: int, home: str, away: str) -> str:
    if head != "both_teams_to_score":
        return ""
    if hs == 0 and aws > 0:
        return home
    if aws == 0 and hs > 0:
        return away
    if selected_side == "home":
        return home
    if selected_side == "away":
        return away
    return "обе команды"


def _master_meta(event_id: str) -> dict[str, str]:
    """Recover Flashscore team identity for legacy rows that lack cached assets.

    This mirrors the first GOOL bot: locate AA÷<event_id> in the Flashscore master
    feed and read OA/OB logo files plus team ids/slugs. It also works for many
    recently finished matches still present in the day's master feed.
    """
    event_id = str(event_id or "").strip()
    if not event_id:
        return {}
    try:
        provider = sc.FlashscoreProvider()
        for path in ("f_1_0_3_en_1", "f_1_0_0_en_1"):
            body = provider._feed(path)
            if not body:
                continue
            prefix = f"AA÷{event_id}¬"
            for chunk in body.split("~"):
                if not chunk.startswith(prefix):
                    continue
                fields: dict[str, str] = {}
                for token in chunk.split("¬")[1:]:
                    if "÷" in token:
                        key, value = token.split("÷", 1)
                        if key and key not in fields:
                            fields[key] = value
                return {
                    "home_team_id": str(fields.get("JA") or "").strip(),
                    "away_team_id": str(fields.get("JB") or "").strip(),
                    "home_team_slug": str(fields.get("WU") or "").strip(),
                    "away_team_slug": str(fields.get("WV") or "").strip(),
                    "home_logo_file": str(fields.get("OA") or "").strip(),
                    "away_logo_file": str(fields.get("OB") or "").strip(),
                }
    except Exception:
        return {}
    return {}


def _stat_box(draw: ImageDraw.ImageDraw, x1: int, y1: int, x2: int, y2: int, title: str, value: str, accent) -> None:
    draw.rounded_rectangle((x1, y1, x2, y2), 16, fill=PANEL, outline=LINE, width=2)
    draw.text((x1 + 14, y1 + 12), title, font=sc._font(12, True), fill=MUTED)
    draw.text((x1 + 14, y1 + 42), value, font=sc._fit(draw, value, x2 - x1 - 28, 21, True), fill=TEXT)
    draw.rounded_rectangle((x1 + 14, y2 - 13, x2 - 14, y2 - 8), 3, fill=(27, 43, 63))
    draw.rounded_rectangle((x1 + 14, y2 - 13, x1 + 62, y2 - 8), 3, fill=accent)


def _draw_stats(draw: ImageDraw.ImageDraw, record: dict[str, Any], stats: dict[str, Any], momentum: dict[str, Any], accent) -> None:
    draw.rounded_rectangle((42, 655, 1038, 905), 22, fill=(10, 18, 30), outline=LINE, width=2)
    draw.text((64, 674), "СТАТИСТИКА МАТЧА", font=sc._font(15, True), fill=accent)
    possession = _provider_pair(record, "possession", 0, "%")
    rows = [
        ("xG", str(stats.get("xg") or "—")),
        ("УДАРЫ", str(stats.get("shots") or "—")),
        ("В СТВОР", str(stats.get("sot") or "—")),
        ("ОПАСНЫЕ АТАКИ", str(stats.get("dangerous_attacks") or "—")),
        ("ВЛАДЕНИЕ", possession),
        ("BIG CHANCES", str(stats.get("big_chances") or "—")),
        ("УДАРЫ 5М", _momentum_pair(momentum, "shots_last_5m")),
        ("УДАРЫ 10М", _momentum_pair(momentum, "shots_last_10m")),
    ]
    gap = 12
    box_w = 232
    x0 = 60
    y_top = 712
    for i, (title, value) in enumerate(rows):
        row, col = divmod(i, 4)
        x1 = x0 + col * (box_w + gap)
        y1 = y_top + row * 91
        _stat_box(draw, x1, y1, x1 + box_w, y1 + 80, title, value, accent)


def render_shadow_market_card(record: dict[str, Any], analysis: dict[str, Any]) -> bytes:
    match = record.get("match") or {}
    head = str(analysis.get("head") or "team_to_score")
    accent, _ = THEMES.get(head, THEMES["team_to_score"])
    home = str(match.get("home") or "?")
    away = str(match.get("away") or "?")
    league = str(match.get("league") or "LIVE FOOTBALL")
    minute = int(match.get("minute") or 0)
    hs = int(match.get("home_score") or 0)
    aws = int(match.get("away_score") or 0)
    confidence = analysis.get("confidence_score")
    team = analysis.get("team")
    pressure = analysis.get("pressure_score")
    selected_side = analysis.get("selected_side") or analysis.get("target_side")
    label, team_detail = _team_target(head, selected_side, hs, aws, team)
    scoreless = _scoreless_team(head, selected_side, hs, aws, home, away)
    meta = sc.flashscore_meta(record)
    stats = sc.stats_snapshot(record)
    momentum = record.get("live_momentum") or {}
    match_id = str(match.get("flashscore_event_id") or "")
    sc._remember(match_id, meta, stats)

    image = Image.new("RGBA", (W, H), BG + (255,))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((24, 20, 1056, 92), 22, fill=PANEL, outline=accent, width=2)
    draw.text((48, 38), league, font=sc._fit(draw, league, 790, 24, True), fill=TEXT)
    draw.rounded_rectangle((885, 31, 1028, 80), 14, fill=(8, 24, 34), outline=accent, width=2)
    draw.text((927, 44), "LIVE", font=sc._font(19, True), fill=accent)

    sc._badge(image, draw, 178, 205, sc._logo(meta, "home"), home, accent)
    sc._badge(image, draw, 902, 205, sc._logo(meta, "away"), away, accent)
    _center(draw, f"{hs} : {aws}", 145, sc._font(58, True), TEXT)
    period = "2-Й ТАЙМ" if minute >= 46 else "1-Й ТАЙМ"
    _center(draw, f"{period} • {minute}'", 215, sc._font(20, True), accent)
    for x, name in ((178, home), (902, away)):
        font = sc._fit(draw, name, 330, 24, True)
        box = draw.textbbox((0, 0), name, font=font)
        draw.text((x - (box[2] - box[0]) / 2, 285), name, font=font, fill=TEXT)

    draw.rounded_rectangle((44, 335, 1036, 505), 24, fill=PANEL2, outline=accent, width=3)
    _center(draw, label, 360, sc._fit(draw, label, 890, 32, True), accent)
    if head == "team_to_score":
        _center(draw, team_detail, 411, sc._fit(draw, team_detail, 850, 26, True), TEXT)
    else:
        detail = f"ДОЛЖЕН ЗАБИТЬ: {scoreless}" if scoreless and scoreless != "обе команды" else "обе команды забьют"
        _center(draw, detail, 411, sc._fit(draw, detail, 850, 24, True), TEXT)
    strength = "—" if confidence is None else f"{float(confidence) * 100:.0f}/100"
    _center(draw, f"СИЛА СИГНАЛА: {strength}", 460, sc._font(19, True), MUTED)

    draw.rounded_rectangle((44, 525, 1036, 635), 22, fill=(10, 18, 30), outline=LINE, width=2)
    draw.text((66, 545), "GOOL АНАЛИТИКА", font=sc._font(14, True), fill=accent)
    pressure_text = "—" if pressure is None else f"{float(pressure):.2f}"
    draw.text((66, 578), f"LIVE PRESSURE  {pressure_text}", font=sc._font(22, True), fill=TEXT)
    draw.text((375, 578), "GOOL LIVE  ✓", font=sc._font(17, True), fill=GREEN)
    draw.text((590, 578), "PREMATCH  ✓", font=sc._font(17, True), fill=GREEN)
    draw.text((800, 578), "LIVE  ✓", font=sc._font(17, True), fill=GREEN)

    _draw_stats(draw, record, stats, momentum, accent)
    _center(draw, "GOOL Bot 2", 936, sc._font(17, True), MUTED)
    return _save(image)


def render_shadow_market_result_card(row: dict[str, Any]) -> bytes:
    head = str(row.get("head") or "team_to_score")
    base_accent, _ = THEMES.get(head, THEMES["team_to_score"])
    won = str(row.get("result") or "lost").lower() == "won"
    accent = GREEN if won else RED
    home = str(row.get("home") or "?")
    away = str(row.get("away") or "?")
    league = str(row.get("league") or "LIVE FOOTBALL")
    entry = row.get("score") or [0, 0]
    settled = row.get("settled_score") or entry
    minute = int(row.get("settled_minute") or row.get("minute") or 0)
    entry_minute = int(row.get("minute") or 0)
    team = row.get("team")
    label, team_detail = _team_target(head, row.get("selected_side"), int(entry[0]), int(entry[1]), team)

    match_id = str(row.get("match_id") or "")
    cached = sc._read_assets().get(match_id, {}) or {}
    meta = dict(row.get("flashscore_meta") or cached.get("flashscore_meta") or {})
    stats = dict(row.get("stats_snapshot") or cached.get("stats_snapshot") or {})
    if not (meta.get("home_logo_file") or meta.get("home_logo_url")) or not (meta.get("away_logo_file") or meta.get("away_logo_url")):
        recovered = _master_meta(match_id)
        if recovered:
            meta = {**recovered, **{k: v for k, v in meta.items() if v}}

    image = Image.new("RGBA", (W, H), BG + (255,))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 20, 1056, 92), 22, fill=PANEL, outline=base_accent, width=2)
    draw.text((48, 38), league, font=sc._fit(draw, league, 790, 24, True), fill=TEXT)
    draw.rounded_rectangle((885, 31, 1028, 80), 14, fill=(8, 24, 34), outline=base_accent, width=2)
    draw.text((918, 44), "RESULT", font=sc._font(17, True), fill=base_accent)

    sc._badge(image, draw, 178, 205, sc._logo(meta, "home"), home, base_accent)
    sc._badge(image, draw, 902, 205, sc._logo(meta, "away"), away, base_accent)
    _center(draw, f"{int(settled[0])} : {int(settled[1])}", 145, sc._font(58, True), TEXT)
    _center(draw, f"{minute}'", 215, sc._font(20, True), base_accent)
    for x, name in ((178, home), (902, away)):
        font = sc._fit(draw, name, 330, 24, True)
        box = draw.textbbox((0, 0), name, font=font)
        draw.text((x - (box[2] - box[0]) / 2, 285), name, font=font, fill=TEXT)

    draw.rounded_rectangle((44, 345, 1036, 535), 26, fill=PANEL2, outline=accent, width=3)
    _center(draw, "✓ СИГНАЛ ЗАШЁЛ" if won else "✕ СИГНАЛ НЕ ЗАШЁЛ", 375, sc._font(36, True), accent)
    _center(draw, label, 430, sc._fit(draw, label, 880, 26, True), base_accent)
    if head == "team_to_score" and team:
        _center(draw, team_detail, 475, sc._fit(draw, team_detail, 820, 21, True), TEXT)

    draw.rounded_rectangle((44, 555, 1036, 655), 20, fill=(10, 18, 30), outline=LINE, width=2)
    draw.text((70, 578), f"ВХОД  {entry_minute}' • {int(entry[0])}:{int(entry[1])}", font=sc._font(20, True), fill=TEXT)
    draw.text((570, 578), f"ИТОГ  {minute}' • {int(settled[0])}:{int(settled[1])}", font=sc._font(20, True), fill=accent)

    draw.rounded_rectangle((44, 680, 1036, 875), 22, fill=(10, 18, 30), outline=LINE, width=2)
    draw.text((66, 700), "СТАТИСТИКА ПРИ ВХОДЕ", font=sc._font(14, True), fill=base_accent)
    items = [
        ("xG", str(stats.get("xg") or "—")),
        ("УДАРЫ", str(stats.get("shots") or "—")),
        ("В СТВОР", str(stats.get("sot") or "—")),
        ("BIG CHANCES", str(stats.get("big_chances") or "—")),
    ]
    for i, (title, value) in enumerate(items):
        x1 = 66 + i * 242
        _stat_box(draw, x1, 738, x1 + 220, 842, title, value, base_accent)
    _center(draw, "GOOL Bot 2", 925, sc._font(17, True), MUTED)
    return _save(image)
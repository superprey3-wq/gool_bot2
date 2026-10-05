from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc

BG = (5, 10, 18)
PANEL = (13, 22, 36)
PANEL2 = (19, 31, 49)
TEXT = (247, 249, 252)
MUTED = (151, 166, 188)
LINE = (45, 63, 88)
GOLD = (255, 184, 48)
ICE = (104, 224, 242)
ORANGE = (255, 142, 43)


def _center(d: ImageDraw.ImageDraw, text: str, cx: int, y: int, width: int, size: int, fill) -> None:
    font = sc._fit(d, text, width, size, True)
    box = d.textbbox((0, 0), text, font=font)
    d.text((cx - (box[2] - box[0]) / 2, y), text, font=font, fill=fill)


def _leg_logo_meta(leg: dict[str, Any], side: str) -> dict[str, Any]:
    return {
        f"{side}_logo_file": leg.get(f"{side}_logo_file"),
        f"{side}_team_id": leg.get(f"{side}_team_id"),
        f"{side}_team_slug": leg.get(f"{side}_team_slug"),
    }


def render_multisport_parlay_card(parlay: dict[str, Any], sport: str) -> bytes:
    legs = [dict(x) for x in (parlay.get("legs") or [])][:4]
    icon = "🏒" if sport == "hockey" else "🏀"
    title = "ХОККЕЙ" if sport == "hockey" else "БАСКЕТБОЛ"
    accent = ICE if sport == "hockey" else ORANGE
    height = 280 + max(2, len(legs)) * 210
    im = Image.new("RGBA", (1080, height), BG + (255,))
    d = ImageDraw.Draw(im)

    d.rounded_rectangle((28, 24, 1052, 112), 24, fill=PANEL, outline=accent, width=3)
    d.text((54, 46), f"{icon} GOOL {title} • PREMATCH", font=sc._font(29, True), fill=TEXT)
    d.text((760, 48), "ЭКСПРЕСС", font=sc._font(22, True), fill=accent)

    combined = float(parlay.get("combined_odd") or 0.0)
    avg_strength = float(parlay.get("average_strength") or 0.0)
    probability = float(parlay.get("combined_probability") or 0.0)

    d.rounded_rectangle((42, 138, 1038, 246), 22, fill=PANEL2, outline=GOLD, width=3)
    d.text((70, 158), "ОБЩИЙ КЭФ", font=sc._font(15, True), fill=MUTED)
    d.text((70, 188), f"{combined:.2f}", font=sc._font(39, True), fill=GOLD)
    d.text((380, 158), "СР. СИЛА", font=sc._font(15, True), fill=MUTED)
    d.text((380, 190), f"R{avg_strength:.0f}", font=sc._font(31, True), fill=accent)
    d.text((690, 158), "РАСЧЁТНАЯ ВЕРОЯТНОСТЬ", font=sc._font(15, True), fill=MUTED)
    d.text((690, 190), f"{probability * 100:.1f}%", font=sc._font(31, True), fill=TEXT)

    y = 275
    for idx, leg in enumerate(legs, 1):
        d.rounded_rectangle((42, y, 1038, y + 180), 22, fill=PANEL, outline=LINE, width=2)
        d.rounded_rectangle((58, y + 18, 104, y + 64), 12, fill=PANEL2, outline=accent, width=2)
        _center(d, str(idx), 81, y + 27, 30, 20, accent)

        home = str(leg.get("home") or "?")
        away = str(leg.get("away") or "?")
        league = str(leg.get("league") or "")
        selection = str(leg.get("selection") or "?")
        odd = float(leg.get("odd") or 0.0)
        strength = float(leg.get("strength") or 0.0)

        home_meta = _leg_logo_meta(leg, "home")
        away_meta = _leg_logo_meta(leg, "away")
        sc._badge(im, d, 142, y + 92, sc._logo(home_meta, "home"), home, accent)
        sc._badge(im, d, 938, y + 92, sc._logo(away_meta, "away"), away, accent)

        match = f"{home} — {away}"
        d.text((230, y + 24), league, font=sc._fit(d, league, 610, 16, False), fill=MUTED)
        d.text((230, y + 55), match, font=sc._fit(d, match, 610, 26, True), fill=TEXT)
        d.text((230, y + 102), selection, font=sc._fit(d, selection, 500, 27, True), fill=GOLD)
        d.text((760, y + 100), f"@ {odd:.2f}", font=sc._font(28, True), fill=TEXT)
        d.text((760, y + 136), f"R{strength:.0f}", font=sc._font(20, True), fill=accent)
        y += 210

    d.rounded_rectangle((320, height - 72, 760, height - 22), 16, fill=accent)
    _center(d, f"{title} • ЭКСПРЕСС", 540, height - 61, 390, 21, BG)
    return sc._save(im)

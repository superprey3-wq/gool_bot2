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

    leg_h = 205
    leg_gap = 18
    legs_top = 282
    footer_h = 58
    footer_gap = 26
    height = legs_top + len(legs) * leg_h + max(0, len(legs) - 1) * leg_gap + footer_gap + footer_h + 28

    im = Image.new("RGBA", (1080, height), BG + (255,))
    d = ImageDraw.Draw(im)

    # Header
    d.rounded_rectangle((32, 24, 1048, 112), 24, fill=PANEL, outline=accent, width=3)
    d.text((58, 48), f"{icon} GOOL {title}", font=sc._font(28, True), fill=TEXT)
    d.text((796, 49), "PREMATCH · ЭКСПРЕСС", font=sc._fit(d, "PREMATCH · ЭКСПРЕСС", 220, 18, True), fill=accent)

    # Summary
    combined = float(parlay.get("combined_odd") or 0.0)
    avg_strength = float(parlay.get("average_strength") or 0.0)
    probability = float(parlay.get("combined_probability") or 0.0)
    d.rounded_rectangle((42, 136, 1038, 250), 22, fill=PANEL2, outline=GOLD, width=3)

    summary = [
        ("ОБЩИЙ КЭФ", f"{combined:.2f}", GOLD),
        ("СР. СИЛА", f"R{avg_strength:.0f}", accent),
        ("ВЕРОЯТНОСТЬ ЭКСПРЕССА", f"{probability * 100:.1f}%", TEXT),
    ]
    xs = [(68, 310), (370, 610), (690, 1005)]
    for (label, value, color), (x1, x2) in zip(summary, xs):
        d.text((x1, 158), label, font=sc._fit(d, label, x2 - x1, 14, True), fill=MUTED)
        d.text((x1, 192), value, font=sc._fit(d, value, x2 - x1, 34, True), fill=color)

    y = legs_top
    for idx, leg in enumerate(legs, 1):
        d.rounded_rectangle((42, y, 1038, y + leg_h), 24, fill=PANEL, outline=LINE, width=2)

        # Leg number
        d.rounded_rectangle((58, y + 18, 112, y + 70), 13, fill=PANEL2, outline=accent, width=2)
        _center(d, str(idx), 85, y + 30, 34, 20, accent)

        home = str(leg.get("home") or "?")
        away = str(leg.get("away") or "?")
        league = str(leg.get("league") or "")
        selection = str(leg.get("selection") or "?")
        odd = float(leg.get("odd") or 0.0)
        strength = float(leg.get("strength") or 0.0)

        home_meta = _leg_logo_meta(leg, "home")
        away_meta = _leg_logo_meta(leg, "away")
        sc._badge(im, d, 150, y + 116, sc._logo(home_meta, "home"), home, accent)
        sc._badge(im, d, 930, y + 116, sc._logo(away_meta, "away"), away, accent)

        center_x = 540
        _center(d, league.upper(), center_x, y + 22, 610, 15, MUTED)
        _center(d, f"{home} — {away}", center_x, y + 55, 600, 27, TEXT)

        # Bet strip
        d.rounded_rectangle((276, y + 108, 804, y + 174), 16, fill=PANEL2, outline=accent, width=2)
        d.text((300, y + 124), selection, font=sc._fit(d, selection, 300, 25, True), fill=GOLD)
        d.text((620, y + 124), f"@ {odd:.2f}", font=sc._font(25, True), fill=TEXT)
        d.text((720, y + 128), f"R{strength:.0f}", font=sc._font(18, True), fill=accent)

        y += leg_h + leg_gap

    footer_y = height - footer_h - 28
    d.rounded_rectangle((300, footer_y, 780, footer_y + footer_h), 16, fill=accent)
    _center(d, f"{icon} {title} · ЭКСПРЕСС", 540, footer_y + 16, 410, 20, BG)
    return sc._save(im)


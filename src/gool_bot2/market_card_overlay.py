from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc


def _fmt_odd(value: Any) -> str:
    try:
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    except Exception:
        return "—"


def _primary_target(info: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in (info.get("targets") or []) if isinstance(row, dict)]
    if not rows:
        return {}
    rows.sort(key=lambda row: (float(row.get("prob_delta_pp") or 0.0), float(row.get("weight") or 0.0)), reverse=True)
    return rows[0]


def append_xbet_market_block(png: bytes, info: dict[str, Any] | None) -> bytes:
    info = info or {}
    try:
        src = Image.open(BytesIO(png)).convert("RGBA")
    except Exception:
        return png
    extra = 285
    img = Image.new("RGBA", (src.width, src.height + extra), sc.BG + (255,))
    img.paste(src, (0, 0))
    d = ImageDraw.Draw(img)
    y0 = src.height + 12
    d.rounded_rectangle((36, y0, src.width - 36, src.height + extra - 24), 24, fill=sc.PANEL2, outline=sc.MUTED, width=2)
    level = str(info.get("level") or "NO_DATA")
    confirmed = bool(info.get("confirmed"))
    override = bool(info.get("override"))
    if override:
        state = "🚨 MARKET OVERRIDE · ПРИОРИТЕТ 1xBET"
    elif confirmed:
        state = "ПОДТВЕРЖДАЕТ СИГНАЛ"
    else:
        state = "НЕТ ПРОГРУЗА" if level == "NEUTRAL" else level
    d.text((62, y0 + 20), "1xBET MARKET PRESSURE", font=sc._font(24, True), fill=sc.TEXT)
    d.text((62, y0 + 60), state, font=sc._fit(d, state, 900, 28, True), fill=sc.TEXT)
    score = info.get("score_pp")
    delta = "—" if score is None else f"{float(score):+.1f} п.п."
    d.text((720, y0 + 27), f"ΔP {delta}", font=sc._font(22, True), fill=sc.TEXT)
    target = _primary_target(info)
    label = str(target.get("label") or "рынок недоступен")
    old_odd = _fmt_odd(target.get("old_odd")); new_odd = _fmt_odd(target.get("new_odd"))
    d.text((62, y0 + 112), f"Рынок: {label}", font=sc._font(20, True), fill=sc.TEXT)
    d.text((62, y0 + 148), f"Кэф: {old_odd} → {new_odd}", font=sc._font(20, True), fill=sc.TEXT)
    move = float(target.get("prob_delta_pp") or 0.0)
    one_way = int(target.get("one_way_moves") or 0)
    d.text((420, y0 + 148), f"Движение: {move:+.1f} п.п. · импульсов {one_way}", font=sc._font(19, True), fill=sc.TEXT)
    age = info.get("age_seconds")
    age_text = "—" if age is None else f"{float(age):.0f} сек"
    d.text((62, y0 + 184), f"Свежесть рынка: {age_text} · уровень {level}", font=sc._font(18, True), fill=sc.MUTED)
    reason = str(info.get("reason") or "1xBet ещё прогревается")
    reason_font = sc._fit(d, reason, src.width - 124, 17, True)
    d.text((62, y0 + 218), reason, font=reason_font, fill=sc.MUTED)
    out = BytesIO(); img.convert("RGB").save(out, "PNG", optimize=True); return out.getvalue()

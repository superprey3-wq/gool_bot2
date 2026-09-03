from __future__ import annotations

import sys
from collections import Counter
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from . import signal_cards as sc
from .value_bet_policy import is_value_row


def _fmt_odd(value: Any) -> str:
    try:
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    except Exception:
        return "—"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "—"


def _primary_target(info: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in (info.get("targets") or []) if isinstance(row, dict)]
    if not rows:
        return {}
    head = str(info.get("head") or "")
    if head == "both_teams_to_score":
        for row in rows:
            if str(row.get("market") or "") == "btts_yes":
                return row
    rows.sort(key=lambda row: (float(row.get("weight") or 0.0), float(row.get("prob_delta_pp") or 0.0)), reverse=True)
    return rows[0]


def append_xbet_market_block(png: bytes, info: dict[str, Any] | None) -> bytes:
    info = info or {}
    try:
        src = Image.open(BytesIO(png)).convert("RGBA")
    except Exception:
        return png
    extra = 365
    img = Image.new("RGBA", (src.width, src.height + extra), sc.BG + (255,))
    img.paste(src, (0, 0))
    d = ImageDraw.Draw(img)
    y0 = src.height + 12
    d.rounded_rectangle((36, y0, src.width - 36, src.height + extra - 24), 24, fill=sc.PANEL2, outline=sc.MUTED, width=2)
    level = str(info.get("level") or "NO_DATA")
    confirmed = bool(info.get("confirmed"))
    override = bool(info.get("override"))
    value_bet = bool(info.get("value_bet"))
    value_override = bool(info.get("value_override"))
    result_state = str(info.get("result_state") or "").lower()

    if result_state in {"won", "lost"}:
        won = result_state == "won"
        if override and value_bet:
            state = "ПРОГРУЗ + VALUE ЗАШЛИ" if won else "ПРОГРУЗ + VALUE НЕ ЗАШЛИ"
        elif value_bet:
            state = "VALUE BET ЗАШЁЛ" if won else "VALUE BET НЕ ЗАШЁЛ"
        else:
            state = "ПРОГРУЗ ЗАШЁЛ" if won else "ПРОГРУЗ НЕ ЗАШЁЛ"
    elif override and value_bet:
        state = "MARKET OVERRIDE + VALUE · 1xBET"
    elif override:
        state = "MARKET OVERRIDE · ПРИОРИТЕТ 1xBET"
    elif value_override:
        state = "VALUE OVERRIDE · ПРИОРИТЕТ ЦЕНЫ"
    elif value_bet:
        state = "VALUE BET · ВЫГОДНАЯ ЦЕНА 1xBET"
    elif confirmed:
        state = "1xBET ПОДТВЕРЖДАЕТ СИГНАЛ"
    else:
        state = "НЕТ ПРОГРУЗА" if level == "NEUTRAL" else level

    d.text((62, y0 + 20), "1xBET MARKET + VALUE", font=sc._font(24, True), fill=sc.TEXT)
    d.text((62, y0 + 60), state, font=sc._fit(d, state, 900, 28, True), fill=sc.TEXT)
    score = info.get("score_pp")
    delta = "—" if score is None else f"{float(score):+.1f} п.п."
    d.text((720, y0 + 27), f"STEAM ΔP {delta}", font=sc._font(19, True), fill=sc.TEXT)

    target = _primary_target(info)
    label = str(info.get("value_market_label") or target.get("label") or "рынок недоступен")
    old_odd = _fmt_odd(target.get("old_odd")); new_odd = _fmt_odd(target.get("new_odd"))
    if new_odd == "—":
        new_odd = _fmt_odd(info.get("value_odd"))
    d.text((62, y0 + 112), f"Рынок: {label}", font=sc._font(20, True), fill=sc.TEXT)
    d.text((62, y0 + 148), f"Кэф: {old_odd} → {new_odd}", font=sc._font(20, True), fill=sc.TEXT)
    move = float(target.get("prob_delta_pp") or 0.0)
    one_way = int(target.get("one_way_moves") or 0)
    d.text((420, y0 + 148), f"Прогруз: {move:+.1f} п.п. · импульсов {one_way}", font=sc._font(18, True), fill=sc.TEXT)

    value_level = str(info.get("value_level") or "NO_DATA")
    edge = info.get("value_edge_pp")
    edge_text = "—" if edge is None else f"{float(edge):+.1f} п.п."
    gool_p = _fmt_pct(info.get("value_model_probability"))
    market_p = _fmt_pct(info.get("value_market_probability"))
    d.text((62, y0 + 194), f"VALUE: {value_level} · EDGE {edge_text}", font=sc._font(21, True), fill=sc.TEXT)
    d.text((62, y0 + 230), f"GOOL {gool_p}  vs  1xBet fair {market_p} · цена @{_fmt_odd(info.get('value_odd'))}", font=sc._font(18, True), fill=sc.TEXT)

    age = info.get("age_seconds")
    age_text = "—" if age is None else f"{float(age):.0f} сек"
    d.text((62, y0 + 270), f"Свежесть: {age_text} · steam {level}", font=sc._font(17, True), fill=sc.MUTED)
    reason = str(info.get("value_reason") or info.get("reason") or "1xBet ещё прогревается")
    reason_font = sc._fit(d, reason, src.width - 124, 16, True)
    d.text((62, y0 + 304), reason, font=reason_font, fill=sc.MUTED)
    out = BytesIO(); img.convert("RGB").save(out, "PNG", optimize=True); return out.getvalue()


def append_xbet_result_block(png: bytes, row: dict[str, Any]) -> bytes:
    info = dict(row.get("xbet_market") or ((row.get("analysis") or {}).get("xbet_market") or {}))
    info["result_state"] = str(row.get("result") or "lost").lower()
    return append_xbet_market_block(png, info)


def _is_override_row(row: dict[str, Any]) -> bool:
    info = row.get("xbet_market") or ((row.get("analysis") or {}).get("xbet_market") or {})
    return bool(
        str(row.get("signal_source") or "") in {"xbet_market_override", "xbet_market_value_combo"}
        or row.get("market_override")
        or (row.get("analysis") or {}).get("market_override")
        or (isinstance(info, dict) and info.get("override"))
    )


def _is_special_row(row: dict[str, Any]) -> bool:
    return _is_override_row(row) or is_value_row(row)


def _main_special_active() -> bool:
    module = sys.modules.get("gool_bot2.storage_market_signal_worker")
    record = getattr(module, "_CURRENT", None) if module is not None else None
    if not isinstance(record, dict):
        return False
    market = record.get("xbet_market") or {}
    return any(
        isinstance(v, dict) and (v.get("override") or v.get("value_bet"))
        for k, v in market.items() if k != "source"
    )


def _signal_like_text(text: Any) -> bool:
    value = str(text or "")
    return any(token in value for token in ("ЕЩЁ ГОЛ", "ЕЩЁ +2 ГОЛА", "ОБЕ ЗАБЬЮТ", "КОМАНДА ЗАБЬЁТ"))


def _special_caption(row: dict[str, Any], won: bool) -> str:
    override = _is_override_row(row)
    value = is_value_row(row)
    if override and value:
        return "✅ <b>ПРОГРУЗ + VALUE ЗАШЛИ</b>" if won else "❌ <b>ПРОГРУЗ + VALUE НЕ ЗАШЛИ</b>"
    if value:
        return "✅ <b>VALUE BET ЗАШЁЛ</b>" if won else "❌ <b>VALUE BET НЕ ЗАШЁЛ</b>"
    return "✅ <b>ПРОГРУЗ ЗАШЁЛ</b>" if won else "❌ <b>ПРОГРУЗ НЕ ЗАШЁЛ</b>"


def _source_for_analysis(analysis: dict[str, Any], info: dict[str, Any]) -> str:
    if info.get("override") and info.get("value_bet"):
        return "xbet_market_value_combo"
    if info.get("override"):
        return "xbet_market_override"
    if info.get("value_override"):
        return "xbet_value_override"
    if info.get("value_bet"):
        return "xbet_value_bet"
    return "gool"


def _install_runtime_patches() -> None:
    telegram = sys.modules.get("gool_bot2.telegram")
    if telegram is None:
        return

    menu = sys.modules.get("gool_bot2.bot_menu")
    if menu is not None and not getattr(menu, "_xbet_value_stats_patched", False):
        original_stats = menu._stats

        def stats_with_market(rows):
            lines = list(original_stats(rows))
            override_rows = [r for r in rows if _is_override_row(r)]
            value_rows = [r for r in rows if is_value_row(r)]

            def stat_line(label, selected):
                counts = Counter(str(r.get("result") or "pending").lower() for r in selected)
                won, lost, pending = counts["won"], counts["lost"], counts["pending"]
                total = won + lost
                pct = "—" if not total else f"{won / total * 100:.1f}%"
                return f"{label}: ✅ {won} · ❌ {lost} · ⏳ {pending} · <b>{pct}</b>"

            extras = [
                stat_line("🚨 Прогруз 1xBet", override_rows),
                stat_line("💎 VALUE BET", value_rows),
            ]
            strategy_count = len(getattr(menu, "ALL_HEADS", ()))
            return lines[:strategy_count] + extras + lines[strategy_count:]

        menu._stats = stats_with_market
        menu._xbet_value_stats_patched = True

    for name in ("gool_bot2.signal_worker", "gool_bot2.signal_worker_all", "gool_bot2.signal_worker_all_cards"):
        module = sys.modules.get(name)
        if module is None or getattr(module, "_xbet_special_broadcast_patched", False) or not hasattr(module, "broadcast"):
            continue
        original_broadcast = module.broadcast

        def guarded_broadcast(text, *args, _orig=original_broadcast, **kwargs):
            if _main_special_active() and _signal_like_text(text):
                print("XBET_SPECIAL_CARD_REQUIRED text_fallback_suppressed=1", flush=True)
                return 0
            return _orig(text, *args, **kwargs)

        module.broadcast = guarded_broadcast
        module._xbet_special_broadcast_patched = True

    signal_worker = sys.modules.get("gool_bot2.signal_worker")
    signal_all = sys.modules.get("gool_bot2.signal_worker_all")
    if signal_worker is not None and signal_all is not None and not getattr(signal_all, "_xbet_special_result_patched", False):
        original_result_sender = signal_all._send_result_cards

        def send_result_cards(rows):
            normal = []
            for row in rows:
                if not _is_special_row(row):
                    normal.append(row); continue
                result = str(row.get("result") or "lost").lower()
                score = row.get("settled_score") or [0, 0]
                minute = int(row.get("settled_minute") or 0)
                try:
                    png = signal_worker.render_result_card(row, result, minute, int(score[0] or 0), int(score[1] or 0))
                    png = append_xbet_result_block(png, row)
                    caption = _special_caption(row, result == "won")
                    caption += f" · {signal_worker.HEAD_LABELS.get(str(row.get('head')), str(row.get('head')))}"
                    sent = telegram.broadcast_photo(png, caption=caption)
                    print(f"XBET_SPECIAL_RESULT_CARD result={result} deliveries={sent} match={row.get('match_id')}", flush=True)
                except Exception as exc:
                    print(f"XBET_SPECIAL_RESULT_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
            if normal:
                original_result_sender(normal)

        signal_all._send_result_cards = send_result_cards
        signal_all._xbet_special_result_patched = True

    cards = sys.modules.get("gool_bot2.signal_worker_all_cards")
    if signal_all is not None and cards is not None and not getattr(signal_all, "_xbet_special_plus2_result_patched", False):
        original_plus2_sender = signal_all._send_two_more_results

        def send_two_more_results(rows):
            normal = []
            for row in rows:
                if not _is_special_row(row):
                    normal.append(row); continue
                result = str(row.get("result") or "lost").lower()
                try:
                    png = cards.render_gool_live_result_card(row, result)
                    png = append_xbet_result_block(png, row)
                    caption = _special_caption(row, result == "won") + " · ЕЩЁ +2 ГОЛА"
                    sent = telegram.broadcast_photo(png, caption=caption)
                    print(f"XBET_SPECIAL_PLUS2_RESULT_CARD result={result} deliveries={sent} match={row.get('match_id')}", flush=True)
                except Exception as exc:
                    print(f"XBET_SPECIAL_PLUS2_RESULT_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
            if normal:
                original_plus2_sender(normal)

        signal_all._send_two_more_results = send_two_more_results
        signal_all._xbet_special_plus2_result_patched = True

    shadow = sys.modules.get("gool_bot2.shadow_market_worker")
    if shadow is not None and not getattr(shadow, "_xbet_special_delivery_patched", False):
        original_shadow_save = shadow.save_signal_journal
        original_shadow_send = shadow._send_signal
        original_shadow_notify = shadow._notify_results

        def shadow_save(path, rows):
            for row in rows:
                analysis = row.get("analysis") or {}
                info = row.get("xbet_market") or analysis.get("xbet_market") or {}
                if isinstance(info, dict) and (info.get("override") or info.get("value_bet")):
                    row["signal_source"] = _source_for_analysis(analysis, info)
                    row["market_override"] = bool(info.get("override"))
                    row["value_bet"] = bool(info.get("value_bet"))
                    row["value_override"] = bool(info.get("value_override"))
                    row["value_edge_pp"] = info.get("value_edge_pp")
                    row["value_level"] = info.get("value_level")
                    row["xbet_market"] = dict(info)
            return original_shadow_save(path, rows)

        def shadow_send(record, result, png):
            info = result.get("xbet_market") or {}
            if not (isinstance(info, dict) and (info.get("override") or info.get("value_bet"))):
                return original_shadow_send(record, result, png)
            try:
                card = png if png is not None else shadow.render_shadow_market_card(record, result)
                card = append_xbet_market_block(card, info)
                head = str(result.get("head") or "")
                label = "ОБЕ ЗАБЬЮТ — ДА" if head == "both_teams_to_score" else "КОМАНДА ЗАБЬЁТ"
                if info.get("override") and info.get("value_bet"):
                    prefix = "🚨💎 <b>ПРОГРУЗ + VALUE · 1xBet</b>"
                elif info.get("override"):
                    prefix = "🚨 <b>MARKET OVERRIDE · 1xBet</b>"
                else:
                    prefix = "💎 <b>VALUE BET · 1xBet</b>"
                sent = telegram.broadcast_photo(card, caption=f"{prefix} · {label}")
                print(f"XBET_SPECIAL_SHADOW_CARD head={head} deliveries={sent}", flush=True)
                return int(sent > 0)
            except Exception as exc:
                print(f"XBET_SPECIAL_SHADOW_CARD_ERROR {type(exc).__name__}:{exc}", flush=True)
                return 0

        def shadow_notify(rows):
            normal = []
            for row in rows:
                if not _is_special_row(row):
                    normal.append(row); continue
                if row.get("result_notified"):
                    continue
                result = str(row.get("result") or "lost").lower()
                try:
                    card = shadow.render_shadow_market_result_card(row)
                    card = append_xbet_result_block(card, row)
                    head = str(row.get("head") or "")
                    label = "ОБЕ ЗАБЬЮТ — ДА" if head == "both_teams_to_score" else "КОМАНДА ЗАБЬЁТ"
                    caption = _special_caption(row, result == "won") + f" · {label}"
                    sent = telegram.broadcast_photo(card, caption=caption)
                    if sent > 0:
                        row["result_notified"] = True
                    print(f"XBET_SPECIAL_SHADOW_RESULT result={result} deliveries={sent} match={row.get('match_id')}", flush=True)
                except Exception as exc:
                    print(f"XBET_SPECIAL_SHADOW_RESULT_ERROR {type(exc).__name__}:{exc}", flush=True)
            if normal:
                original_shadow_notify(normal)

        shadow.save_signal_journal = shadow_save
        shadow._send_signal = shadow_send
        shadow._notify_results = shadow_notify
        shadow._xbet_special_delivery_patched = True


_install_runtime_patches()

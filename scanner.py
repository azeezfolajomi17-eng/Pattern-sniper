"""
Scanner loop + Telegram command handler.

Two threads:
  * scanner  — walks the watchlist on a schedule, pushes new signals
  * commands — long-polls getUpdates so you can talk to the bot

Alerts are deduped per symbol/timeframe/closed-bar, so one setup pings you once.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import List

from . import config, state
from .data import timeframe_minutes
from .formatting import format_digest, format_signal, signal_markup
from .signals import Signal, scan_all
from .telegram import Telegram

log = logging.getLogger(__name__)
tg = Telegram()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def push(signals: List[Signal], force: bool = False) -> int:
    """Send new signals to every subscribed chat. Returns how many were sent."""
    st = state.load()
    if st.get("muted") and not force:
        return 0
    min_score = int(st.get("min_score", config.MIN_SCORE))
    targets = state.chats()
    if not targets:
        log.info("no subscribed chats yet — send /start to the bot")
    sent = 0
    for sig in signals:
        if sent >= config.MAX_ALERTS_PER_SCAN:
            log.info("alert cap (%d) reached for this scan", config.MAX_ALERTS_PER_SCAN)
            break
        if sig.score < min_score:
            continue
        if not force and state.already_alerted(sig.key):
            continue
        if not force and state.in_cooldown(sig.symbol, sig.timeframe, sig.bar_time,
                                           timeframe_minutes(sig.timeframe),
                                           config.COOLDOWN_BARS):
            log.debug("cooldown: skipping %s %s", sig.symbol, sig.timeframe)
            continue
        text = format_signal(sig)
        for cid in targets:
            tg.send(cid, text, markup=signal_markup(sig))
            time.sleep(0.3)
        state.mark_alerted(sig.key, sig.to_dict())
        sent += 1
        log.info("ALERT %s %s %s score=%s", sig.symbol, sig.timeframe, sig.pattern, sig.score)
    return sent


def run_scan(force: bool = False) -> List[Signal]:
    started = time.time()
    signals = scan_all()
    state.note_scan(_now())
    log.info("scan finished in %.1fs — %d candidate setup(s)", time.time() - started, len(signals))
    push(signals, force=False)
    return signals


def scanner_loop(stop_event: threading.Event) -> None:
    log.info("scanner started: %d symbols x %s, every %ss",
             len(config.WATCHLIST), ",".join(config.TIMEFRAMES), config.SCAN_INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            run_scan()
        except Exception as exc:
            log.exception("scan error: %s", exc)
        stop_event.wait(config.SCAN_INTERVAL_SECONDS)


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------
HELP = (
    "<b>Pattern Scanner Bot</b>\n\n"
    "I run an <b>HTF→LTF model</b> on FX majors, metals and US indices: higher-timeframe POIs "
    "first, then <b>candlestick reactions at key levels</b> inside them, "
    "and alert you the moment a bar closes with a valid setup.\n\n"
    "<b>Patterns:</b> engulfing, pin bar (hammer/shooting star), morning &amp; evening star, "
    "tweezer top/bottom, inside-bar break.\n"
    "<b>HTF→LTF:</b> entries must tap a higher-timeframe FVG or order block "
    "(15m→4H, 1h→4H, 4h→D1), priced in HTF discount/premium, targeting unswept HTF liquidity.\n"
    "<b>SMC:</b> LTF fair value gaps, order blocks, liquidity sweeps, BOS/CHoCH.\n"
    "<b>Levels:</b> swing pivots, prior day/week high &amp; low, psychological round numbers — "
    "clustered into zones and weighted by how often price respected them.\n"
    "<b>Filters:</b> EMA trend on the signal timeframe, daily bias, RSI location, "
    "close strength and a minimum reward:risk.\n\n"
    "Each setup gets a 0-100 confluence score; you only get pinged at or above your threshold.\n\n"
    "<b>Commands</b>\n"
    "/scan — run a scan now\n"
    "/status — settings and stats\n"
    "/watchlist — what's being scanned\n"
    "/last — recent alerts\n"
    "/score 70 — change the alert threshold\n"
    "/mute · /unmute\n"
)


def handle_command(chat_id: int, text: str) -> None:
    cmd, *args = text.strip().split()
    cmd = cmd.lower().split("@")[0]

    if cmd == "/start":
        state.add_chat(chat_id)
        tg.send(chat_id, "✅ Subscribed. I'll alert you the moment a setup closes.\n\n" + HELP)
    elif cmd == "/help":
        tg.send(chat_id, HELP)
    elif cmd == "/watchlist":
        syms = ", ".join(i.name for i in config.WATCHLIST)
        tg.send(chat_id, f"<b>Watchlist</b>\n{syms}\n\n<b>Timeframes</b>\n"
                         f"{', '.join(config.TIMEFRAMES)}")
    elif cmd == "/status":
        st = state.load()
        tg.send(chat_id, (
            "<b>Status</b>\n"
            f"Muted: {'yes' if st.get('muted') else 'no'}\n"
            f"Min score: {st.get('min_score', config.MIN_SCORE)}\n"
            f"Min R:R: {config.MIN_RR}\n"
            f"Symbols: {len(config.WATCHLIST)} · Timeframes: {', '.join(config.TIMEFRAMES)}\n"
            f"Scan interval: {config.SCAN_INTERVAL_SECONDS}s\n"
            f"Scans run: {st['stats'].get('scans', 0)} · Alerts sent: {st['stats'].get('alerts', 0)}\n"
            f"Last scan: {st['stats'].get('last_scan') or 'never'}"
        ))
    elif cmd == "/scan":
        tg.send(chat_id, "🔍 Scanning the watchlist…")
        sigs = scan_all()
        tg.send(chat_id, format_digest(sigs, "Manual scan"))
        push(sigs)
    elif cmd == "/last":
        recent = state.load().get("recent", [])[:5]
        if not recent:
            tg.send(chat_id, "No alerts recorded yet.")
        else:
            lines = ["<b>Recent alerts</b>", ""]
            for r in recent:
                lines.append(f"• {r['symbol']} {r['timeframe']} {r['pattern']} "
                             f"({r['direction']}, score {r['score']}) — {r['bar_time']}")
            tg.send(chat_id, "\n".join(lines))
    elif cmd == "/score":
        if args and args[0].isdigit():
            v = max(0, min(100, int(args[0])))
            state.set_flag("min_score", v)
            tg.send(chat_id, f"Minimum score set to <b>{v}</b>.")
        else:
            tg.send(chat_id, "Usage: <code>/score 70</code>")
    elif cmd == "/mute":
        state.set_flag("muted", True)
        tg.send(chat_id, "🔕 Alerts paused. /unmute to resume.")
    elif cmd == "/unmute":
        state.set_flag("muted", False)
        tg.send(chat_id, "🔔 Alerts resumed.")
    else:
        tg.send(chat_id, "Unknown command. /help for the list.")


def command_loop(stop_event: threading.Event) -> None:
    if not tg.enabled:
        log.warning("TELEGRAM_BOT_TOKEN not set — command loop disabled")
        return
    tg.set_commands()
    offset = 0
    log.info("command loop started")
    while not stop_event.is_set():
        for upd in tg.get_updates(offset):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("channel_post")
            if not msg:
                continue
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            if text.startswith("/"):
                state.add_chat(chat_id)
                try:
                    handle_command(chat_id, text)
                except Exception as exc:
                    log.exception("command error: %s", exc)
                    tg.send(chat_id, "⚠️ Something went wrong handling that command.")

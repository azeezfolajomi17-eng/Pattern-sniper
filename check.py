"""
Telegram connection checker — run this BEFORE deploying.

    python -m app.check              # verify token, find your chat, send a test alert
    python -m app.check --no-test    # verify only, don't send anything

It answers the three questions that actually go wrong:
  1. Is my token valid?               (getMe)
  2. Does the bot know my chat?       (getUpdates / state.json)
  3. Can it actually deliver a card?  (sends a real formatted sample alert)
"""
from __future__ import annotations

import argparse
import sys

from . import config, state
from .data import check_provider
from .formatting import format_signal, signal_markup
from .signals import Signal
from .telegram import Telegram

GREEN, RED, YELL, DIM, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"
OK, BAD, WARN = f"{GREEN}✓{RESET}", f"{RED}✗{RESET}", f"{YELL}!{RESET}"


def sample_signal() -> Signal:
    return Signal(
        symbol="XAUUSD", timeframe="15m", direction="bullish",
        pattern="Bullish Engulfing", score=95, price=4519.0,
        entry=4519.0, stop=4491.65, target=4573.69, rr=2.0,
        level_price=4511.32, level_label="swing low x4 + swing high", level_touches=6,
        trend="up", htf_trend="up", rsi=49.7, atr=6.98,
        bar_time="test message — connection OK",
        reasons=[
            "This is a test alert from python -m app.check",
            "Bullish Engulfing — buyers fully reversed the prior candle",
            "Entry tapping the 4H bullish FVG (4493.4–4520.3)",
            "Priced in 4H discount (eq 4542.1)",
        ],
        smc_tags=["FVG", "OB"], htf_tags=["4H FVG", "discount"],
        htf_timeframe="4h", htf_structure="up", htf_zone="4H FVG 4493.4–4520.3",
        htf_pd="discount", target_note="flat 2R", structure="up",
        chart_url=config.tv_chart_url(config.WATCHLIST[0], "15m"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-test", action="store_true", help="don't send a test alert")
    args = ap.parse_args()

    print("\nTelegram connection check\n" + "─" * 46)

    # 0 ── market data deps -----------------------------------------------------
    problem = check_provider()
    if problem:
        print(f"{BAD} Market data not available:\n    {problem}")
        return 1
    from .data import DATA_PROVIDER
    print(f"{OK} Market data ready  {DIM}(provider: {DATA_PROVIDER}){RESET}")

    # 1 ── token present? -------------------------------------------------------
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        print(f"{BAD} No TELEGRAM_BOT_TOKEN found.\n")
        print("  Fix it in 3 steps:")
        print("   1. Open Telegram, message @BotFather, send /newbot")
        print("   2. Copy the token it gives you (looks like 8123456789:AAH...)")
        print("   3. Put it in .env:   TELEGRAM_BOT_TOKEN=8123456789:AAH...")
        print(f"\n  {DIM}cp .env.example .env  &&  nano .env{RESET}\n")
        return 1
    print(f"{OK} Token found  {DIM}({token[:10]}…){RESET}")

    tg = Telegram(token)

    # 2 ── token valid? ---------------------------------------------------------
    me = tg._call("getMe", {})
    if not me.get("ok"):
        print(f"{BAD} Telegram rejected the token: {me.get('description', 'unknown error')}")
        print("  → Re-copy it from @BotFather (no spaces, no quotes).")
        return 1
    bot = me["result"]
    print(f"{OK} Bot is live: {GREEN}@{bot['username']}{RESET}  ({bot['first_name']})")

    # 3 ── who is subscribed? ---------------------------------------------------
    known = state.chats()
    discovered = []
    for upd in tg.get_updates(0, timeout=2):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") and str(chat["id"]) not in known:
            discovered.append((chat["id"], chat.get("username") or chat.get("title") or "you"))

    for cid, name in discovered:
        state.add_chat(cid)
        print(f"{OK} Found new chat: {name} {DIM}(id {cid}){RESET} — subscribed")

    targets = state.chats()
    if not targets:
        print(f"{WARN} No chats subscribed yet.")
        print(f"   → Open Telegram, search {GREEN}@{bot['username']}{RESET}, "
              f"press Start (or send /start), then run this again.")
        return 2
    print(f"{OK} {len(targets)} chat(s) subscribed: {', '.join(targets)}")

    # 4 ── can we deliver? ------------------------------------------------------
    if args.no_test:
        print(f"\n{OK} All good. Start the bot with:  python -m app.main\n")
        return 0

    sig = sample_signal()
    sent = 0
    for cid in targets:
        res = tg.send(cid, format_signal(sig), markup=signal_markup(sig))
        if res.get("ok"):
            sent += 1
        else:
            print(f"{BAD} Could not message {cid}: {res.get('description')}")
            if "bot was blocked" in str(res.get("description", "")).lower():
                print("   → Unblock the bot in Telegram and press Start again.")
    if sent:
        print(f"{OK} Test alert delivered to {sent} chat(s) — check Telegram.")
        print(f"\n{GREEN}Ready.{RESET} Start the scanner with:  python -m app.main\n")
        return 0
    print(f"{BAD} Nothing delivered.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())

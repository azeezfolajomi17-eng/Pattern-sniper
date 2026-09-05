"""Alert formatting for Telegram (HTML parse mode)."""
from __future__ import annotations

from typing import List

from .signals import Signal

_DIR_ICON = {"bullish": "🟢", "bearish": "🔴"}
_ARROW = {"bullish": "LONG", "bearish": "SHORT"}


def _bars(score: int) -> str:
    filled = round(score / 10)
    return "█" * filled + "░" * (10 - filled)


def format_signal(sig: Signal) -> str:
    icon = _DIR_ICON.get(sig.direction, "⚪")
    lines = [
        f"{icon} <b>{sig.symbol} — {_ARROW[sig.direction]}</b>  <code>{sig.timeframe}</code>",
        f"<b>{sig.pattern}</b> at {sig.level_label}",
    ]
    if sig.htf_tags:
        lines.append("🏛 <b>HTF:</b> " + " · ".join(sig.htf_tags))
    if sig.smc_tags:
        lines.append("🧠 <b>SMC:</b> " + " · ".join(sig.smc_tags))
    lines += [
        "",
        f"<b>Score</b> {sig.score}/100  {_bars(sig.score)}",
        "",
        f"<b>Entry</b>   <code>{sig.entry}</code>",
        f"<b>Stop</b>    <code>{sig.stop}</code>",
        f"<b>Target</b>  <code>{sig.target}</code>" +
        (f"  <i>({sig.target_note})</i>" if sig.target_note else ""),
        f"<b>R:R</b>     <code>{sig.rr}</code>",
        "",
        "<b>Why this fired</b>",
    ]
    for r in sig.reasons:
        lines.append(f"• {r}")
    lines += [
        "",
        f"<i>Level</i> {sig.level_price} ({sig.level_touches} touches) · "
        f"<i>Trend</i> {sig.timeframe} {sig.trend} / D1 {sig.htf_trend} · "
        f"<i>Structure</i> {sig.structure} · <i>RSI</i> {sig.rsi} · <i>ATR</i> {sig.atr}",
    ]
    if sig.htf_timeframe:
        htf_line = f"<i>{sig.htf_timeframe.upper()} context</i> structure {sig.htf_structure}"
        if sig.htf_pd and sig.htf_pd != "unknown":
            htf_line += f" · {sig.htf_pd}"
        if sig.htf_zone:
            htf_line += f" · POI {sig.htf_zone}"
        lines.append(htf_line)
    lines += [
        f"<i>Bar closed</i> {sig.bar_time}",
        f'<a href="{sig.chart_url}">Open chart on TradingView</a>',
    ]
    return "\n".join(lines)


def signal_markup(sig: Signal) -> dict:
    return {"inline_keyboard": [[{"text": "📈 Open TradingView", "url": sig.chart_url}]]}


def format_digest(signals: List[Signal], title: str = "Scan results") -> str:
    if not signals:
        return f"<b>{title}</b>\nNo setups matching the model right now."
    lines = [f"<b>{title}</b> — {len(signals)} setup(s)", ""]
    for s in signals:
        icon = _DIR_ICON.get(s.direction, "⚪")
        lines.append(
            f"{icon} <b>{s.symbol}</b> {s.timeframe} · {s.pattern} · "
            f"score {s.score} · {s.rr}R\n   at {s.level_label} {s.level_price}"
            + (f"  [{' · '.join(s.htf_tags + s.smc_tags)}]" if (s.htf_tags or s.smc_tags) else "")
        )
    return "\n".join(lines)


def format_markdown_report(signals: List[Signal]) -> str:
    """Plain-markdown version used by the offline demo report."""
    if not signals:
        return "No setups currently match the model.\n"
    out = []
    for s in signals:
        out.append(
            f"### {s.symbol} — {_ARROW[s.direction]} ({s.timeframe})\n\n"
            f"- **Pattern:** {s.pattern} at {s.level_label} ({s.level_price}, {s.level_touches} touches)\n"
            f"- **Score:** {s.score}/100\n"
            + (f"- **HTF ({s.htf_timeframe}):** {' · '.join(s.htf_tags)}"
               + (f" — POI {s.htf_zone}" if s.htf_zone else "") + "\n" if s.htf_tags else "")
            + (f"- **SMC:** {' · '.join(s.smc_tags)}\n" if s.smc_tags else "")
            + f"- **Entry / Stop / Target:** {s.entry} / {s.stop} / {s.target}  "
              f"(**{s.rr}R** → {s.target_note})\n"
            + f"- **Context:** {s.timeframe} trend {s.trend}, D1 bias {s.htf_trend}, "
              f"structure {s.structure}, RSI {s.rsi}\n"
            + f"- **Bar closed:** {s.bar_time}\n"
            + "".join(f"  - {r}\n" for r in s.reasons)
        )
    return "\n".join(out)

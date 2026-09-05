"""
Offline demo / sanity check — no Telegram token needed.

    python -m app.demo                # scan the live watchlist now
    python -m app.demo --history 200  # replay the last N bars to show what WOULD have fired
    python -m app.demo --score 55     # loosen the threshold

Writes a markdown report to scan_report.md so you can eyeball the output.
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from . import config
from .data import check_provider, get_candles
from .formatting import format_markdown_report
from .indicators import enrich
from .signals import Signal, evaluate, htf_bias, scan_all

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("demo")


def replay(bars: int, timeframes) -> list[Signal]:
    """Walk backwards through recent history and collect signals the model would have fired."""
    hits: list[Signal] = []
    for inst in config.WATCHLIST:
        bias = htf_bias(inst)
        for tf in timeframes:
            df = get_candles(inst, tf)
            if df is None or len(df) < 120:
                continue
            n = len(df)
            start = max(120, n - bars)
            for i in range(start, n + 1):
                window = df.iloc[:i]
                try:
                    hits.extend(evaluate(inst, tf, df=window, htf=bias))
                except Exception as exc:
                    log.debug("replay error %s %s: %s", inst.name, tf, exc)
    return sorted(hits, key=lambda s: (s.bar_time, s.score), reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=int, default=0, help="replay N recent bars per symbol")
    ap.add_argument("--score", type=int, default=None, help="override minimum score")
    ap.add_argument("--tf", type=str, default=None, help="comma separated timeframes")
    ap.add_argument("--out", type=str, default="scan_report.md")
    args = ap.parse_args()

    if args.score is not None:
        config.MIN_SCORE = args.score
    timeframes = [t.strip() for t in args.tf.split(",")] if args.tf else config.TIMEFRAMES

    problem = check_provider()
    if problem:
        print(f"ERROR: {problem}")
        return 1

    print(f"Watchlist : {', '.join(i.name for i in config.WATCHLIST)}")
    print(f"Timeframes: {', '.join(timeframes)}")
    print(f"Min score : {config.MIN_SCORE}   Min R:R: {config.MIN_RR}\n")

    if args.history:
        print(f"Replaying the last {args.history} closed bars per symbol/timeframe…\n")
        signals = replay(args.history, timeframes)
        title = f"Historical replay — last {args.history} bars"
    else:
        signals = scan_all(timeframes=timeframes)
        title = "Live scan"

    if not signals:
        print("No setups matched the model.")
    for s in signals[:40]:
        arrow = "LONG " if s.direction == "bullish" else "SHORT"
        print(f"{s.bar_time}  {s.symbol:<7} {s.timeframe:<3} {arrow} {s.pattern:<22} "
              f"score {s.score:>3}  {s.rr}R  @ {s.level_label}")

    report = (
        f"# {title}\n\n"
        f"_Generated {pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M UTC')} · "
        f"min score {config.MIN_SCORE} · min R:R {config.MIN_RR}_\n\n"
        f"**Watchlist:** {', '.join(i.name for i in config.WATCHLIST)}  \n"
        f"**Timeframes:** {', '.join(timeframes)}\n\n---\n\n"
        + format_markdown_report(signals[:40])
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"\nReport written to {args.out}  ({len(signals)} signal(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())

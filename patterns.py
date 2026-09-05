"""
Candlestick pattern detection.

Each detector inspects the LAST CLOSED bar (plus the bars before it where the
pattern needs context) and returns a Pattern with a base strength. Strength is
the pattern's own quality only — location, trend and confluence are scored later
in signals.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pandas as pd


@dataclass
class Pattern:
    name: str
    direction: str      # bullish | bearish
    strength: float     # 0-40, the candle's contribution to the final score
    note: str = ""
    # price used as the "reaction point" that must sit at a level
    reaction_price: float = 0.0
    stop_price: float = 0.0   # natural invalidation (wick extreme)


def _bull(c) -> bool:
    return c["close"] > c["open"]


def _bear(c) -> bool:
    return c["close"] < c["open"]


def detect_engulfing(df: pd.DataFrame) -> Optional[Pattern]:
    if len(df) < 2:
        return None
    c, p = df.iloc[-1], df.iloc[-2]
    rng = c["high"] - c["low"]
    if rng <= 0:
        return None
    body = abs(c["close"] - c["open"])
    prev_body = abs(p["close"] - p["open"])
    if body < prev_body or body / rng < 0.55:
        return None
    # engulf must be meaningful vs recent volatility
    if pd.notna(c.get("avg_range")) and body < 0.5 * c["avg_range"]:
        return None

    if _bull(c) and _bear(p) and c["close"] >= p["open"] and c["open"] <= p["close"]:
        strength = 30 + min(6, (body / max(prev_body, 1e-9) - 1) * 4)
        return Pattern("Bullish Engulfing", "bullish", min(strength, 36),
                       "buyers fully reversed the prior candle",
                       reaction_price=float(c["low"]), stop_price=float(min(c["low"], p["low"])))
    if _bear(c) and _bull(p) and c["close"] <= p["open"] and c["open"] >= p["close"]:
        strength = 30 + min(6, (body / max(prev_body, 1e-9) - 1) * 4)
        return Pattern("Bearish Engulfing", "bearish", min(strength, 36),
                       "sellers fully reversed the prior candle",
                       reaction_price=float(c["high"]), stop_price=float(max(c["high"], p["high"])))
    return None


def detect_pin_bar(df: pd.DataFrame) -> Optional[Pattern]:
    """Hammer / shooting star: long rejection wick, small body at the opposite end."""
    if len(df) < 1:
        return None
    c = df.iloc[-1]
    rng = c["high"] - c["low"]
    if rng <= 0:
        return None
    body = abs(c["close"] - c["open"])
    upper = c["high"] - max(c["open"], c["close"])
    lower = min(c["open"], c["close"]) - c["low"]
    if pd.notna(c.get("avg_range")) and rng < 0.6 * c["avg_range"]:
        return None
    if body / rng > 0.4:
        return None

    if lower >= 2 * body and lower / rng >= 0.55 and upper / rng <= 0.25:
        strength = 26 + min(8, (lower / rng - 0.55) * 20)
        return Pattern("Bullish Pin Bar", "bullish", min(strength, 34),
                       f"{lower / rng:.0%} lower-wick rejection",
                       reaction_price=float(c["low"]), stop_price=float(c["low"]))
    if upper >= 2 * body and upper / rng >= 0.55 and lower / rng <= 0.25:
        strength = 26 + min(8, (upper / rng - 0.55) * 20)
        return Pattern("Bearish Pin Bar", "bearish", min(strength, 34),
                       f"{upper / rng:.0%} upper-wick rejection",
                       reaction_price=float(c["high"]), stop_price=float(c["high"]))
    return None


def detect_star(df: pd.DataFrame) -> Optional[Pattern]:
    """Morning / evening star — three-bar reversal."""
    if len(df) < 3:
        return None
    a, b, c = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    a_body, b_body, c_body = (abs(x["close"] - x["open"]) for x in (a, b, c))
    b_range = b["high"] - b["low"]
    if b_range <= 0 or a_body <= 0:
        return None
    if b_body > 0.45 * a_body:          # middle candle must be indecisive
        return None
    mid_a = (a["open"] + a["close"]) / 2

    if _bear(a) and _bull(c) and c["close"] > mid_a and c_body > 0.5 * a_body:
        return Pattern("Morning Star", "bullish", 32, "three-bar bullish reversal",
                       reaction_price=float(min(a["low"], b["low"], c["low"])),
                       stop_price=float(min(a["low"], b["low"], c["low"])))
    if _bull(a) and _bear(c) and c["close"] < mid_a and c_body > 0.5 * a_body:
        return Pattern("Evening Star", "bearish", 32, "three-bar bearish reversal",
                       reaction_price=float(max(a["high"], b["high"], c["high"])),
                       stop_price=float(max(a["high"], b["high"], c["high"])))
    return None


def detect_tweezer(df: pd.DataFrame) -> Optional[Pattern]:
    """Two candles rejecting from the same price — double rejection."""
    if len(df) < 2:
        return None
    c, p = df.iloc[-1], df.iloc[-2]
    tol = (c.get("atr") or 0) * 0.08
    if tol <= 0:
        return None
    if (_bear(p) and _bull(c) and abs(c["low"] - p["low"]) <= tol
            and (c["close"] - c["low"]) > 0.6 * (c["high"] - c["low"])
            and (p["close"] - p["low"]) > 0.3 * (p["high"] - p["low"])):
        return Pattern("Tweezer Bottom", "bullish", 22, "two candles rejected the same low",
                       reaction_price=float(min(c["low"], p["low"])),
                       stop_price=float(min(c["low"], p["low"])))
    if (_bull(p) and _bear(c) and abs(c["high"] - p["high"]) <= tol
            and (c["high"] - c["close"]) > 0.6 * (c["high"] - c["low"])
            and (p["high"] - p["close"]) > 0.3 * (p["high"] - p["low"])):
        return Pattern("Tweezer Top", "bearish", 22, "two candles rejected the same high",
                       reaction_price=float(max(c["high"], p["high"])),
                       stop_price=float(max(c["high"], p["high"])))
    return None


def detect_inside_bar_break(df: pd.DataFrame) -> Optional[Pattern]:
    """Inside bar compression followed by a close outside the mother bar."""
    if len(df) < 3:
        return None
    mother, inside, c = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if not (inside["high"] <= mother["high"] and inside["low"] >= mother["low"]):
        return None
    if c["close"] > mother["high"] and _bull(c):
        return Pattern("Inside Bar Breakout", "bullish", 22, "compression released upward",
                       reaction_price=float(mother["low"]), stop_price=float(inside["low"]))
    if c["close"] < mother["low"] and _bear(c):
        return Pattern("Inside Bar Breakdown", "bearish", 22, "compression released downward",
                       reaction_price=float(mother["high"]), stop_price=float(inside["high"]))
    return None


DETECTORS = (
    detect_engulfing,
    detect_pin_bar,
    detect_star,
    detect_tweezer,
    detect_inside_bar_break,
)


def detect_all(df: pd.DataFrame) -> List[Pattern]:
    """Every pattern present on the last closed bar, strongest first."""
    found: List[Pattern] = []
    for fn in DETECTORS:
        try:
            p = fn(df)
        except Exception:
            p = None
        if p:
            found.append(p)
    return sorted(found, key=lambda p: p.strength, reverse=True)

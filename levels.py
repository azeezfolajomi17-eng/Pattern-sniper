"""
Key-level engine.

The model trades candlestick reactions AT levels, so the quality of the level
matters as much as the candle. We build four kinds and merge them into zones:

  * swing pivots      - fractal highs/lows (the classic support/resistance)
  * prior day H/L     - PDH / PDL
  * prior week H/L    - PWH / PWL
  * round numbers     - psychological 00 / 50 levels near price

Overlapping levels are clustered into a single zone and the number of touches is
counted, because a level that has been respected 4 times is worth more than one
that has been touched once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import pandas as pd


@dataclass
class Level:
    price: float
    kind: str            # swing_high | swing_low | PDH | PDL | PWH | PWL | round
    touches: int = 1
    sources: List[str] = field(default_factory=list)
    last_touch: pd.Timestamp | None = None

    _PRIORITY = {
        "prev week high": 0, "prev week low": 0,
        "prev day high": 1, "prev day low": 1,
        "swing high": 2, "swing low": 2,
        "round number": 3,
    }

    @property
    def label(self) -> str:
        """Readable zone name: the 2 most significant sources that formed it."""
        counts: dict[str, int] = {}
        for s in self.sources:
            counts[s] = counts.get(s, 0) + 1
        ranked = sorted(counts, key=lambda s: (self._PRIORITY.get(s, 9), -counts[s]))
        picked = []
        for s in ranked:
            n = counts[s]
            picked.append(f"{s} x{n}" if n > 1 else s)
            if len(picked) == 2:
                break
        return " + ".join(picked) if picked else self.kind


def find_pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> List[Level]:
    """Fractal swing highs and lows confirmed by `right` bars to the right."""
    levels: List[Level] = []
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    for i in range(left, n - right):
        window_h = highs[i - left: i + right + 1]
        window_l = lows[i - left: i + right + 1]
        if highs[i] == window_h.max() and (window_h.argmax() == left):
            levels.append(Level(float(highs[i]), "swing_high", 1, ["swing high"], df.index[i]))
        if lows[i] == window_l.min() and (window_l.argmin() == left):
            levels.append(Level(float(lows[i]), "swing_low", 1, ["swing low"], df.index[i]))
    return levels


def session_levels(df: pd.DataFrame) -> List[Level]:
    """Previous day and previous week high/low."""
    out: List[Level] = []
    if df.empty:
        return out
    try:
        daily = df.resample("1D").agg({"high": "max", "low": "min"}).dropna()
        if len(daily) >= 2:
            prev = daily.iloc[-2]
            out.append(Level(float(prev["high"]), "PDH", 1, ["prev day high"], daily.index[-2]))
            out.append(Level(float(prev["low"]), "PDL", 1, ["prev day low"], daily.index[-2]))
        weekly = df.resample("1W").agg({"high": "max", "low": "min"}).dropna()
        if len(weekly) >= 2:
            prevw = weekly.iloc[-2]
            out.append(Level(float(prevw["high"]), "PWH", 1, ["prev week high"], weekly.index[-2]))
            out.append(Level(float(prevw["low"]), "PWL", 1, ["prev week low"], weekly.index[-2]))
    except Exception:
        pass
    return out


def round_levels(price: float, step: float, span: float) -> List[Level]:
    """Psychological levels stepping around current price within +/- span."""
    if step <= 0:
        return []
    out: List[Level] = []
    start = (price - span) // step * step
    x = start
    while x <= price + span:
        if x > 0:
            out.append(Level(round(float(x), 8), "round", 1, ["round number"]))
        x += step
    return out


def count_touches(df: pd.DataFrame, price: float, tol: float) -> tuple[int, pd.Timestamp | None]:
    """How many bars traded into the zone but failed to close beyond it."""
    lo, hi = price - tol, price + tol
    hit = (df["low"] <= hi) & (df["high"] >= lo)
    if not hit.any():
        return 0, None
    idx = df.index[hit]
    # collapse consecutive bars into a single touch event
    touches, last = 0, None
    prev_pos = -10
    positions = [df.index.get_loc(t) for t in idx]
    for p in positions:
        if p - prev_pos > 2:
            touches += 1
        prev_pos = p
        last = df.index[p]
    return touches, last


def cluster(levels: List[Level], tol: float) -> List[Level]:
    """Merge levels that sit inside one tolerance band into a single zone."""
    if not levels:
        return []
    levels = sorted(levels, key=lambda l: l.price)
    merged: List[Level] = [levels[0]]
    for lv in levels[1:]:
        cur = merged[-1]
        if abs(lv.price - cur.price) <= tol:
            total = cur.touches + lv.touches
            cur.price = (cur.price * cur.touches + lv.price * lv.touches) / max(total, 1)
            cur.touches = total
            cur.sources = cur.sources + lv.sources
            if lv.last_touch is not None and (cur.last_touch is None or lv.last_touch > cur.last_touch):
                cur.last_touch = lv.last_touch
            if cur.kind == "round" and lv.kind != "round":
                cur.kind = lv.kind
        else:
            merged.append(lv)
    return merged


def build_levels(df: pd.DataFrame, inst, atr_value: float,
                 pivot_left: int = 3, pivot_right: int = 3,
                 tol_atr: float = 0.55) -> List[Level]:
    """Full level map for an instrument on one timeframe."""
    tol = max(atr_value * tol_atr, 1e-9)
    price = float(df["close"].iloc[-1])

    levels: List[Level] = []
    levels += find_pivots(df, pivot_left, pivot_right)
    levels += session_levels(df)
    levels += round_levels(price, inst.round_step, span=atr_value * 6)

    zones = cluster(levels, tol)

    # Score each zone by how often price actually respected it
    recent = df.tail(300)
    for z in zones:
        t, last = count_touches(recent, z.price, tol)
        z.touches = max(z.touches, t)
        z.last_touch = last or z.last_touch
    return zones


def nearest_level(levels: List[Level], price: float, direction: str,
                  max_distance: float) -> Level | None:
    """
    Closest level acting in the right place for a trade:
    bullish setups want support at/below the reaction, bearish wants resistance above.
    """
    best, best_d = None, max_distance
    for lv in levels:
        d = abs(lv.price - price)
        if d > max_distance:
            continue
        if direction == "bullish" and lv.price > price + max_distance * 0.5:
            continue
        if direction == "bearish" and lv.price < price - max_distance * 0.5:
            continue
        score_d = d / max(lv.touches, 1) ** 0.5  # prefer well-tested levels
        if score_d < best_d:
            best, best_d = lv, score_d
    return best


def next_target(levels: List[Level], price: float, direction: str, min_gap: float) -> float | None:
    """First opposing level that can act as a take-profit."""
    candidates = []
    for lv in levels:
        if direction == "bullish" and lv.price > price + min_gap:
            candidates.append(lv.price)
        if direction == "bearish" and lv.price < price - min_gap:
            candidates.append(lv.price)
    if not candidates:
        return None
    return min(candidates) if direction == "bullish" else max(candidates)

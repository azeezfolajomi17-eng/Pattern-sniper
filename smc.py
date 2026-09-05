"""
Smart Money Concepts layer — a *confluence bonus*, not a gate.

Nothing here can create a signal on its own. The core model is still
"candlestick pattern reacting at a key level"; this module answers the question
"is smart-money context backing that reaction?" and returns a capped bonus.

What it looks for:

  * Fair Value Gap (FVG)  - 3-candle imbalance, still unmitigated, that the
                            reaction is sitting inside → institutional discount/premium
  * Order Block (OB)      - last opposing candle before a displacement leg that
                            broke structure; reaction into it counts
  * Liquidity Sweep       - wick took out a prior swing high/low then closed back
                            inside (stop-hunt / turtle soup)
  * BOS / CHoCH           - market structure direction, and whether the most
                            recent break flipped it
  * Displacement          - the signal candle itself is an outsized, decisive bar
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pandas as pd

from . import config


@dataclass
class Zone:
    top: float
    bottom: float
    direction: str          # bullish | bearish
    kind: str               # fvg | ob
    index: pd.Timestamp

    def contains(self, price: float, pad: float = 0.0) -> bool:
        return (self.bottom - pad) <= price <= (self.top + pad)

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class SMCContext:
    structure: str = "range"            # up | down | range
    last_break: str = ""                # BOS | CHoCH
    fvgs: List[Zone] = field(default_factory=list)
    order_blocks: List[Zone] = field(default_factory=list)
    swept: str = ""                     # sellside | buyside | ""
    displacement: bool = False


# --------------------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------------------
def find_fvgs(df: pd.DataFrame, lookback: int = 120, min_size_atr: float = 0.15) -> List[Zone]:
    """Unmitigated 3-candle imbalances within the lookback window."""
    out: List[Zone] = []
    if len(df) < 5:
        return out
    d = df.tail(lookback)
    highs, lows = d["high"].values, d["low"].values
    atr_v = float(d["atr"].iloc[-1]) if "atr" in d.columns else 0.0
    min_size = atr_v * min_size_atr

    for i in range(2, len(d)):
        # bullish imbalance: candle i's low sits above candle i-2's high
        if lows[i] > highs[i - 2] and (lows[i] - highs[i - 2]) >= min_size:
            top, bottom = float(lows[i]), float(highs[i - 2])
            # mitigated if price later traded back down through the gap
            later_low = lows[i + 1:].min() if i + 1 < len(d) else None
            if later_low is not None and later_low <= bottom:
                continue  # fully filled
            out.append(Zone(top, bottom, "bullish", "fvg", d.index[i]))
        # bearish imbalance
        if highs[i] < lows[i - 2] and (lows[i - 2] - highs[i]) >= min_size:
            top, bottom = float(lows[i - 2]), float(highs[i])
            later_high = highs[i + 1:].max() if i + 1 < len(d) else None
            if later_high is not None and later_high >= top:
                continue
            out.append(Zone(top, bottom, "bearish", "fvg", d.index[i]))
    return out


def _swing_points(df: pd.DataFrame, left: int = 2, right: int = 2):
    highs, lows = df["high"].values, df["low"].values
    sh, sl = [], []
    for i in range(left, len(df) - right):
        wh = highs[i - left: i + right + 1]
        wl = lows[i - left: i + right + 1]
        if highs[i] == wh.max() and wh.argmax() == left:
            sh.append((i, float(highs[i])))
        if lows[i] == wl.min() and wl.argmin() == left:
            sl.append((i, float(lows[i])))
    return sh, sl


def market_structure(df: pd.DataFrame, lookback: int = 150) -> Tuple[str, str]:
    """
    Walk confirmed swings and track breaks of structure.
    Returns (structure, last_break) where last_break is 'BOS', 'CHoCH' or ''.
    """
    d = df.tail(lookback)
    sh, sl = _swing_points(d, 2, 2)
    if not sh or not sl:
        return "range", ""

    events = sorted([(i, p, "high") for i, p in sh] + [(i, p, "low") for i, p in sl])
    closes = d["close"].values
    structure, last_break = "range", ""
    active_high: Optional[Tuple[int, float]] = None
    active_low: Optional[Tuple[int, float]] = None

    for i in range(len(d)):
        for idx, price, kind in events:
            if idx == i:
                if kind == "high":
                    active_high = (idx, price)
                else:
                    active_low = (idx, price)
        c = closes[i]
        if active_high and i > active_high[0] and c > active_high[1]:
            last_break = "CHoCH" if structure == "down" else "BOS"
            structure, active_high = "up", None
        elif active_low and i > active_low[0] and c < active_low[1]:
            last_break = "CHoCH" if structure == "up" else "BOS"
            structure, active_low = "down", None

    if structure == "range" and len(sh) >= 2 and len(sl) >= 2:
        # no clean break yet — fall back to swing sequencing (HH/HL vs LH/LL)
        if sh[-1][1] > sh[-2][1] and sl[-1][1] > sl[-2][1]:
            structure = "up"
        elif sh[-1][1] < sh[-2][1] and sl[-1][1] < sl[-2][1]:
            structure = "down"
    return structure, last_break


def find_order_blocks(df: pd.DataFrame, lookback: int = 120, max_zones: int = 6) -> List[Zone]:
    """Last opposing candle before a displacement leg that broke a recent swing."""
    out: List[Zone] = []
    d = df.tail(lookback)
    if len(d) < 20:
        return out
    body = (d["close"] - d["open"]).abs()
    avg_body = body.rolling(20).mean()
    o, c, h, l = d["open"].values, d["close"].values, d["high"].values, d["low"].values

    for i in range(20, len(d)):
        if pd.isna(avg_body.iloc[i]) or avg_body.iloc[i] <= 0:
            continue
        if body.iloc[i] < 1.6 * avg_body.iloc[i]:
            continue
        window_hi = h[max(0, i - 12):i].max()
        window_lo = l[max(0, i - 12):i].min()
        if c[i] > o[i] and c[i] > window_hi:                 # bullish displacement
            for j in range(i - 1, max(0, i - 6), -1):
                if c[j] < o[j]:
                    out.append(Zone(float(h[j]), float(l[j]), "bullish", "ob", d.index[j]))
                    break
        elif c[i] < o[i] and c[i] < window_lo:               # bearish displacement
            for j in range(i - 1, max(0, i - 6), -1):
                if c[j] > o[j]:
                    out.append(Zone(float(h[j]), float(l[j]), "bearish", "ob", d.index[j]))
                    break
    return out[-max_zones:]


def liquidity_sweep(df: pd.DataFrame, lookback: int = 12) -> str:
    """Did the last bar raid a prior swing extreme and close back inside?"""
    if len(df) < lookback + 2:
        return ""
    last = df.iloc[-1]
    prior = df.iloc[-(lookback + 1):-1]
    prior_low, prior_high = float(prior["low"].min()), float(prior["high"].max())
    if last["low"] < prior_low and last["close"] > prior_low:
        return "sellside"
    if last["high"] > prior_high and last["close"] < prior_high:
        return "buyside"
    return ""


def is_displacement(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    body = abs(float(last["close"] - last["open"]))
    avg = float(last.get("avg_range") or 0)
    return avg > 0 and body >= 1.2 * avg


def analyse(df: pd.DataFrame) -> SMCContext:
    """Full SMC read for a timeframe."""
    structure, last_break = market_structure(df)
    return SMCContext(
        structure=structure,
        last_break=last_break,
        fvgs=find_fvgs(df),
        order_blocks=find_order_blocks(df),
        swept=liquidity_sweep(df),
        displacement=is_displacement(df),
    )


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------
def confluence(ctx: SMCContext, direction: str, reaction_price: float,
               atr_value: float) -> Tuple[float, List[str], List[str]]:
    """
    Score the SMC backdrop behind a pattern.

    Returns (bonus, tags, reasons). The bonus is capped by SMC_MAX_BONUS so this
    stays a tie-breaker rather than the whole model.
    """
    bonus = 0.0
    tags: List[str] = []
    reasons: List[str] = []
    pad = atr_value * 0.15

    # --- inside an unmitigated FVG in our direction ------------------------------
    for z in reversed(ctx.fvgs):
        if z.direction == direction and z.contains(reaction_price, pad):
            bonus += config.SMC_FVG_BONUS
            tags.append("FVG")
            reasons.append(
                f"Reaction inside an unmitigated {direction} FVG "
                f"({z.bottom:.5g}–{z.top:.5g})".replace(".00000", "")
            )
            break

    # --- inside an order block ---------------------------------------------------
    for z in reversed(ctx.order_blocks):
        if z.direction == direction and z.contains(reaction_price, pad):
            bonus += config.SMC_OB_BONUS
            tags.append("OB")
            reasons.append(f"Tapped a {direction} order block ({z.bottom:.5g}–{z.top:.5g})")
            break

    # --- liquidity sweep ---------------------------------------------------------
    if (direction == "bullish" and ctx.swept == "sellside") or \
       (direction == "bearish" and ctx.swept == "buyside"):
        bonus += config.SMC_SWEEP_BONUS
        side = "sell-side" if direction == "bullish" else "buy-side"
        tags.append("Sweep")
        reasons.append(f"Swept {side} liquidity then closed back inside range")

    # --- market structure --------------------------------------------------------
    aligned = (ctx.structure == "up" and direction == "bullish") or \
              (ctx.structure == "down" and direction == "bearish")
    if aligned:
        bonus += config.SMC_STRUCTURE_BONUS
        if ctx.last_break:               # only badge a confirmed break, not an inferred trend
            tags.append(ctx.last_break)
        reasons.append(f"Structure is {ctx.structure} ({ctx.last_break or 'HH/HL'}) — trading with it")
    elif ctx.last_break == "CHoCH" and ctx.structure != "range":
        # a fresh CHoCH against old structure is exactly the reversal we want
        bonus += config.SMC_STRUCTURE_BONUS * 0.6
        tags.append("CHoCH")
        reasons.append("Recent change of character — structure just flipped")
    elif ctx.structure != "range":
        # trading straight into unbroken opposing structure — dock points
        bonus -= config.SMC_CONTRA_PENALTY
        reasons.append(f"Against {ctx.structure} structure with no CHoCH yet")

    # --- displacement ------------------------------------------------------------
    if ctx.displacement:
        bonus += config.SMC_DISPLACEMENT_BONUS
        reasons.append("Signal candle shows displacement (outsized decisive body)")

    return min(bonus, config.SMC_MAX_BONUS), tags, reasons

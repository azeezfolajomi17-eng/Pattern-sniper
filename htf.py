"""
Higher-timeframe → lower-timeframe (HTF→LTF) model layer.

Your entries are taken on the LTF, but the *decision* belongs to the HTF. This
module builds an HTF picture once per instrument and projects it down onto the
entry timeframe:

  * HTF POIs        - unmitigated HTF FVGs and order blocks, projected onto the
                      LTF reaction price ("is my 15m pin bar tapping the 4H FVG?")
  * Dealing range   - HTF swing range + equilibrium, giving premium / discount
                      (longs wanted in discount, shorts in premium)
  * HTF structure   - BOS / CHoCH direction on the HTF, used as directional bias
  * Draw on liquidity - unswept HTF swing highs/lows used as the natural target

Default pairings (override with the HTF_MAP env var):

    5m → 1h     15m → 4h     30m → 4h     1h → 4h     4h → 1d
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import config
from .data import get_candles
from .indicators import enrich
from .smc import Zone, find_fvgs, find_order_blocks, market_structure

# --------------------------------------------------------------------------------------
# Timeframe pairing
# --------------------------------------------------------------------------------------
_DEFAULT_MAP = {"5m": "1h", "15m": "4h", "30m": "4h", "1h": "4h", "4h": "1d", "1d": "1d"}


def _parse_map(raw: str) -> Dict[str, str]:
    out = dict(_DEFAULT_MAP)
    for part in raw.split(","):
        part = part.strip()
        if ":" in part:
            lo, hi = part.split(":", 1)
            out[lo.strip()] = hi.strip()
    return out


HTF_MAP = _parse_map(config.HTF_MAP_RAW)


def htf_for(timeframe: str) -> Optional[str]:
    """The higher timeframe that governs this entry timeframe."""
    hi = HTF_MAP.get(timeframe)
    return None if not hi or hi == timeframe else hi


# --------------------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------------------
@dataclass
class Pool:
    price: float
    side: str            # buyside (highs) | sellside (lows)
    index: pd.Timestamp


@dataclass
class HTFContext:
    timeframe: str = ""
    structure: str = "range"
    last_break: str = ""
    zones: List[Zone] = field(default_factory=list)      # unmitigated FVGs + OBs
    pools: List[Pool] = field(default_factory=list)      # unswept liquidity
    range_high: float = 0.0
    range_low: float = 0.0
    atr: float = 0.0

    @property
    def equilibrium(self) -> float:
        return (self.range_high + self.range_low) / 2

    def zone_at(self, price: float, direction: str, pad: float) -> Optional[Zone]:
        """Deepest HTF POI in our direction that the LTF reaction is tapping."""
        hits = [z for z in self.zones if z.direction == direction and z.contains(price, pad)]
        if not hits:
            return None
        # prefer the most recent (freshest) POI
        return sorted(hits, key=lambda z: z.index)[-1]

    def premium_discount(self, price: float) -> str:
        if self.range_high <= self.range_low:
            return "unknown"
        pct = (price - self.range_low) / (self.range_high - self.range_low)
        if pct <= 0.45:
            return "discount"
        if pct >= 0.55:
            return "premium"
        return "equilibrium"

    def draw_on_liquidity(self, price: float, direction: str, min_gap: float) -> Optional[float]:
        """Nearest unswept HTF pool the market is likely drawing towards."""
        if direction == "bullish":
            above = [p.price for p in self.pools if p.side == "buyside" and p.price > price + min_gap]
            return min(above) if above else None
        below = [p.price for p in self.pools if p.side == "sellside" and p.price < price - min_gap]
        return max(below) if below else None


def _liquidity_pools(df: pd.DataFrame, left: int = 2, right: int = 2,
                     lookback: int = 60, max_pools: int = 10) -> List[Pool]:
    """Swing highs/lows that price has not yet traded through — resting liquidity."""
    d = df.tail(lookback)
    highs, lows = d["high"].values, d["low"].values
    pools: List[Pool] = []
    n = len(d)
    for i in range(left, n - right):
        wh, wl = highs[i - left:i + right + 1], lows[i - left:i + right + 1]
        if highs[i] == wh.max() and wh.argmax() == left:
            if highs[i + right + 1:].max() < highs[i] if i + right + 1 < n else True:
                pools.append(Pool(float(highs[i]), "buyside", d.index[i]))
        if lows[i] == wl.min() and wl.argmin() == left:
            if lows[i + right + 1:].min() > lows[i] if i + right + 1 < n else True:
                pools.append(Pool(float(lows[i]), "sellside", d.index[i]))
    return pools[-max_pools:]


_CACHE: Dict[Tuple[str, str, str], HTFContext] = {}


def build_context(inst, entry_timeframe: str, as_of: Optional[pd.Timestamp] = None) -> Optional[HTFContext]:
    """
    HTF picture governing `entry_timeframe`.

    `as_of` truncates the HTF series so historical replays stay honest (no peeking
    at HTF candles that had not closed when the LTF signal printed).
    Cached per instrument/HTF/last-closed-HTF-bar.
    """
    if not config.HTF_ENABLED:
        return None
    hi = htf_for(entry_timeframe)
    if not hi:
        return None

    df = get_candles(inst, hi)
    if df is None or len(df) < 60:
        return None
    if as_of is not None:
        df = df[df.index <= as_of]
        if len(df) < 60:
            return None

    key = (inst.name, hi, str(df.index[-1]))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    d = enrich(df).tail(config.LOOKBACK_BARS)
    structure, last_break = market_structure(d)
    zones = find_fvgs(d, lookback=config.HTF_ZONE_LOOKBACK) + \
        find_order_blocks(d, lookback=config.HTF_ZONE_LOOKBACK)
    window = d.tail(config.HTF_RANGE_BARS)

    ctx = HTFContext(
        timeframe=hi,
        structure=structure,
        last_break=last_break,
        zones=zones,
        pools=_liquidity_pools(d),
        range_high=float(window["high"].max()),
        range_low=float(window["low"].min()),
        atr=float(d["atr"].iloc[-1]),
    )
    if len(_CACHE) > 400:
        _CACHE.clear()
    _CACHE[key] = ctx
    return ctx


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------
def confluence(ctx: Optional[HTFContext], direction: str, reaction_price: float,
               atr_value: float) -> Tuple[float, List[str], List[str], bool]:
    """
    Score the HTF backdrop for an LTF setup.

    Returns (bonus, tags, reasons, has_poi) where has_poi is True when the entry
    is actually tapping an HTF FVG / order block — that's the flag HTF_REQUIRED
    enforces.
    """
    if ctx is None:
        return 0.0, [], [], False

    bonus = 0.0
    tags: List[str] = []
    reasons: List[str] = []
    pad = atr_value * 0.35          # LTF wick can sit just outside the HTF zone edge
    tf = ctx.timeframe.upper()

    # --- tapping an HTF POI ------------------------------------------------------
    zone = ctx.zone_at(reaction_price, direction, pad)
    has_poi = zone is not None
    if zone is not None:
        if zone.kind == "fvg":
            bonus += config.HTF_FVG_BONUS
            tags.append(f"{tf} FVG")
            reasons.append(f"Entry tapping the {tf} {direction} FVG "
                           f"({zone.bottom:.6g}–{zone.top:.6g})")
        else:
            bonus += config.HTF_OB_BONUS
            tags.append(f"{tf} OB")
            reasons.append(f"Entry tapping the {tf} {direction} order block "
                           f"({zone.bottom:.6g}–{zone.top:.6g})")

    # --- premium / discount ------------------------------------------------------
    pd_state = ctx.premium_discount(reaction_price)
    if (direction == "bullish" and pd_state == "discount") or \
       (direction == "bearish" and pd_state == "premium"):
        bonus += config.HTF_PD_BONUS
        tags.append(pd_state)
        reasons.append(f"Priced in {tf} {pd_state} (eq {ctx.equilibrium:.6g})")
    elif (direction == "bullish" and pd_state == "premium") or \
         (direction == "bearish" and pd_state == "discount"):
        bonus -= config.HTF_PD_BONUS
        reasons.append(f"Chasing — {direction} entry from {tf} {pd_state}")

    # --- HTF structure -----------------------------------------------------------
    aligned = (ctx.structure == "up" and direction == "bullish") or \
              (ctx.structure == "down" and direction == "bearish")
    if aligned:
        bonus += config.HTF_STRUCTURE_BONUS
        tags.append(f"{tf} {ctx.last_break or 'trend'}")
        reasons.append(f"{tf} structure is {ctx.structure} — entry with HTF flow")
    elif ctx.structure != "range":
        if ctx.last_break == "CHoCH":
            reasons.append(f"{tf} just printed a CHoCH — early reversal entry")
        else:
            bonus -= config.HTF_CONTRA_PENALTY
            reasons.append(f"Fighting {tf} {ctx.structure} structure")

    return max(-config.HTF_MAX_BONUS, min(bonus, config.HTF_MAX_BONUS)), tags, reasons, has_poi

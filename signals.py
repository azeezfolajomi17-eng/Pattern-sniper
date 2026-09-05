"""
The trading model: candlestick pattern + key level + trend confluence.

A candle only becomes a signal when ALL of the core conditions line up:

  1. a recognised reversal/continuation pattern closed on the last bar
  2. the candle's rejection point sits inside a key level zone
  3. the level has been respected before (touch count adds score)
  4. trend/momentum context is not fighting the trade
  5. the resulting trade offers at least MIN_RR reward:risk

Everything is turned into a 0-100 confluence score; only setups at or above
MIN_SCORE get pushed to Telegram.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional

import pandas as pd

from . import config
from .data import get_candles
from .indicators import enrich, trend_state
from .levels import Level, build_levels, nearest_level, next_target
from .patterns import Pattern, detect_all
from .smc import SMCContext, analyse as smc_analyse, confluence as smc_confluence
from .htf import HTFContext, build_context as htf_build, confluence as htf_confluence

log = logging.getLogger(__name__)


@dataclass
class Signal:
    symbol: str
    timeframe: str
    direction: str          # bullish | bearish
    pattern: str
    score: int
    price: float
    entry: float
    stop: float
    target: float
    rr: float
    level_price: float
    level_label: str
    level_touches: int
    trend: str
    htf_trend: str
    rsi: float
    atr: float
    bar_time: str
    reasons: List[str] = field(default_factory=list)
    smc_tags: List[str] = field(default_factory=list)
    htf_tags: List[str] = field(default_factory=list)
    htf_timeframe: str = ""
    htf_structure: str = ""
    htf_zone: str = ""
    htf_pd: str = ""
    target_note: str = ""
    structure: str = "range"
    chart_url: str = ""

    @property
    def key(self) -> str:
        return f"{self.symbol}|{self.timeframe}|{self.bar_time}|{self.pattern}"

    def to_dict(self) -> dict:
        return asdict(self)


def _round(inst, x: float) -> float:
    return round(float(x), inst.digits)


def compress_score(raw: float) -> int:
    """
    Soft-knee normalisation so scores never clip at 100.

    Below the knee the raw additive score passes through unchanged (so the
    calibrated MIN_SCORE keeps its meaning); above it, the remaining headroom is
    compressed into the final stretch. A setup only reaches 100 if literally
    every factor — pattern, level, trend, SMC and HTF — lines up.
    """
    knee = config.SCORE_KNEE
    if raw <= knee:
        return int(max(0, round(raw)))
    span = max(config.SCORE_RAW_MAX - knee, 1e-9)
    scaled = knee + (raw - knee) * (100.0 - knee) / span
    return int(max(0, min(100, round(scaled))))


def _in_session(ts: pd.Timestamp) -> bool:
    if config.SESSION_START_UTC == 0 and config.SESSION_END_UTC >= 24:
        return True
    h = ts.tz_convert("UTC").hour
    return config.SESSION_START_UTC <= h < config.SESSION_END_UTC


def htf_bias(inst) -> str:
    """Higher-timeframe direction used as a context filter (daily EMA stack)."""
    df = get_candles(inst, "1d")
    if df.empty or len(df) < 60:
        return "range"
    return trend_state(enrich(df).iloc[-1])


def evaluate(inst, timeframe: str, df: Optional[pd.DataFrame] = None,
             htf: Optional[str] = None) -> List[Signal]:
    """Run the model on one instrument/timeframe and return qualifying signals."""
    if df is None:
        df = get_candles(inst, timeframe)
    if df is None or len(df) < 60:
        return []

    d = enrich(df).tail(config.LOOKBACK_BARS)
    last = d.iloc[-1]
    atr_v = float(last["atr"])
    if not atr_v or pd.isna(atr_v) or atr_v <= 0:
        return []
    if not _in_session(d.index[-1]):
        return []

    patterns: List[Pattern] = detect_all(d)
    if not patterns:
        return []

    zones: List[Level] = build_levels(
        d, inst, atr_v,
        pivot_left=config.PIVOT_LEFT,
        pivot_right=config.PIVOT_RIGHT,
        tol_atr=config.LEVEL_ATR_TOLERANCE,
    )
    tf_trend = trend_state(last)
    htf_trend = htf if htf is not None else htf_bias(inst)
    rsi_v = float(last["rsi"])
    close = float(last["close"])
    smc_ctx: SMCContext | None = smc_analyse(d) if config.SMC_ENABLED else None
    htf_ctx: HTFContext | None = htf_build(inst, timeframe, as_of=d.index[-1])

    out: List[Signal] = []
    for pat in patterns:
        max_dist = atr_v * config.PROXIMITY_ATR
        level = nearest_level(zones, pat.reaction_price, pat.direction, max_dist)
        if level is None:
            continue

        reasons: List[str] = []
        score = pat.strength
        reasons.append(f"{pat.name} — {pat.note}")

        # --- location quality -------------------------------------------------
        dist = abs(pat.reaction_price - level.price)
        proximity = 1 - min(dist / max_dist, 1.0)
        score += 10 * proximity
        reasons.append(
            f"Reacted at {level.label} ({level.price:.{inst.digits}f}), "
            f"{dist / atr_v:.2f} ATR away"
        )

        touch_bonus = min(14, 4.5 * max(level.touches - 1, 0))
        score += touch_bonus
        if level.touches >= 2:
            reasons.append(f"Level respected {level.touches}x in recent history")

        if level.kind in ("PDH", "PDL", "PWH", "PWL"):
            score += 7
            reasons.append("Session level (prior day/week extreme)")
        elif level.kind == "round":
            score += 4
            reasons.append("Psychological round number")

        # --- context ----------------------------------------------------------
        aligned_tf = (tf_trend == "up" and pat.direction == "bullish") or \
                     (tf_trend == "down" and pat.direction == "bearish")
        aligned_htf = (htf_trend == "up" and pat.direction == "bullish") or \
                      (htf_trend == "down" and pat.direction == "bearish")
        if aligned_tf:
            score += 10
            reasons.append(f"With the {timeframe} trend ({tf_trend})")
        elif tf_trend != "range":
            score -= 6
            reasons.append(f"Counter-trend on {timeframe} ({tf_trend}) — mean-reversion play")
        if aligned_htf:
            score += 10
            reasons.append(f"Aligned with daily bias ({htf_trend})")
        elif config.REQUIRE_HTF_BIAS and htf_trend != "range":
            continue

        # --- momentum ---------------------------------------------------------
        if pat.direction == "bullish" and rsi_v <= 40:
            score += 7
            reasons.append(f"RSI {rsi_v:.0f} — stretched into support")
        elif pat.direction == "bearish" and rsi_v >= 60:
            score += 7
            reasons.append(f"RSI {rsi_v:.0f} — stretched into resistance")
        elif 45 <= rsi_v <= 55:
            score += 2

        # close strength: did the bar close in the right third of its range?
        rng = float(last["high"] - last["low"])
        if rng > 0:
            pos = (close - float(last["low"])) / rng
            if pat.direction == "bullish" and pos >= 0.6:
                score += 5
                reasons.append("Closed in the upper third of its range")
            elif pat.direction == "bearish" and pos <= 0.4:
                score += 5
                reasons.append("Closed in the lower third of its range")

        # --- SMC confluence (bonus layer, capped) -----------------------------
        smc_tags: List[str] = []
        if smc_ctx is not None:
            smc_bonus, smc_tags, smc_reasons = smc_confluence(
                smc_ctx, pat.direction, pat.reaction_price, atr_v
            )
            if config.SMC_REQUIRED and not smc_tags:
                continue
            score += smc_bonus
            reasons.extend(smc_reasons)

        # --- HTF -> LTF confluence: is the entry inside an HTF POI? -----------
        htf_tags: List[str] = []
        htf_zone_label = ""
        htf_pd_state = ""
        if htf_ctx is not None:
            htf_bonus, htf_tags, htf_reasons, has_poi = htf_confluence(
                htf_ctx, pat.direction, pat.reaction_price, atr_v
            )
            if config.HTF_REQUIRED and not has_poi:
                continue
            score += htf_bonus
            reasons.extend(htf_reasons)
            htf_pd_state = htf_ctx.premium_discount(pat.reaction_price)
            z = htf_ctx.zone_at(pat.reaction_price, pat.direction, atr_v * 0.35)
            if z is not None:
                htf_zone_label = (f"{htf_ctx.timeframe.upper()} "
                                  f"{'FVG' if z.kind == 'fvg' else 'OB'} "
                                  f"{z.bottom:.6g}–{z.top:.6g}")

        # --- trade construction ----------------------------------------------
        buffer = atr_v * 0.25
        htf_zone = (htf_ctx.zone_at(pat.reaction_price, pat.direction, atr_v * 0.35)
                    if htf_ctx is not None else None)
        target_note = "next opposing level"

        if pat.direction == "bullish":
            entry = close
            stop = min(pat.stop_price, level.price)
            if htf_zone is not None:                       # protect the whole HTF POI
                stop = min(stop, htf_zone.bottom)
            stop -= buffer
            risk = entry - stop
            tgt = next_target(zones, entry, "bullish", min_gap=risk * 1.2)
            target = tgt if tgt else entry + risk * 2.0
        else:
            entry = close
            stop = max(pat.stop_price, level.price)
            if htf_zone is not None:
                stop = max(stop, htf_zone.top)
            stop += buffer
            risk = stop - entry
            tgt = next_target(zones, entry, "bearish", min_gap=risk * 1.2)
            target = tgt if tgt else entry - risk * 2.0

        if risk <= 0:
            continue

        # Draw on liquidity: prefer the next unswept HTF pool when it beats the
        # local target and still sits within a sane distance.
        if config.HTF_TARGET_LIQUIDITY and htf_ctx is not None:
            pool = htf_ctx.draw_on_liquidity(entry, pat.direction, min_gap=risk * 1.5)
            if pool is not None:
                pool_rr = abs(pool - entry) / risk
                local_rr = abs(target - entry) / risk
                if config.MIN_RR <= pool_rr <= config.HTF_TARGET_MAX_RR and pool_rr > local_rr:
                    target = pool
                    target_note = f"{htf_ctx.timeframe.upper()} liquidity pool"
                    reasons.append(
                        f"Draw on liquidity: unswept {htf_ctx.timeframe.upper()} "
                        f"{'high' if pat.direction == 'bullish' else 'low'} at {pool:.6g}"
                    )

        rr = abs(target - entry) / risk
        if rr < config.MIN_RR:
            # fall back to a flat 2R target before discarding
            target = entry + risk * 2 if pat.direction == "bullish" else entry - risk * 2
            rr = 2.0
            target_note = "flat 2R"
        if rr >= 2.5:
            score += 4
            reasons.append(f"Room to run: {rr:.1f}R to the {target_note}")

        score = compress_score(score)
        if score < config.MIN_SCORE:
            continue

        out.append(
            Signal(
                symbol=inst.name,
                timeframe=timeframe,
                direction=pat.direction,
                pattern=pat.name,
                score=score,
                price=_round(inst, close),
                entry=_round(inst, entry),
                stop=_round(inst, stop),
                target=_round(inst, target),
                rr=round(rr, 2),
                level_price=_round(inst, level.price),
                level_label=level.label,
                level_touches=level.touches,
                trend=tf_trend,
                htf_trend=htf_trend,
                rsi=round(rsi_v, 1),
                atr=_round(inst, atr_v),
                bar_time=d.index[-1].strftime("%Y-%m-%d %H:%M UTC"),
                reasons=reasons,
                smc_tags=smc_tags,
                htf_tags=htf_tags,
                htf_timeframe=(htf_ctx.timeframe if htf_ctx else ""),
                htf_structure=(htf_ctx.structure if htf_ctx else ""),
                htf_zone=htf_zone_label,
                htf_pd=htf_pd_state,
                target_note=target_note,
                structure=(smc_ctx.structure if smc_ctx else "range"),
                chart_url=config.tv_chart_url(inst, timeframe),
            )
        )

    # one signal per symbol/timeframe — the best one
    return sorted(out, key=lambda s: s.score, reverse=True)[:1]


def scan_all(instruments=None, timeframes=None) -> List[Signal]:
    """Scan the whole watchlist across all configured timeframes."""
    instruments = instruments if instruments is not None else config.WATCHLIST
    timeframes = timeframes if timeframes is not None else config.TIMEFRAMES
    results: List[Signal] = []
    for inst in instruments:
        try:
            bias = htf_bias(inst)
        except Exception:
            bias = "range"
        for tf in timeframes:
            try:
                results.extend(evaluate(inst, tf, htf=bias))
            except Exception as exc:
                log.warning("evaluate failed %s %s: %s", inst.name, tf, exc)
    return sorted(results, key=lambda s: s.score, reverse=True)

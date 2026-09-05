"""Synthetic-candle tests for the SMC confluence layer."""
import pandas as pd

from app import config
from app.indicators import enrich
from app.smc import (analyse, confluence, find_fvgs, find_order_blocks,
                     is_displacement, liquidity_sweep, market_structure)


def frame(rows):
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 100.0
    return enrich(df)



def path(points, steps=4):
    """Build candles walking linearly between price waypoints (creates real swings)."""
    rows = []
    for a, b in zip(points, points[1:]):
        for k in range(steps):
            o = a + (b - a) * k / steps
            c = a + (b - a) * (k + 1) / steps
            rows.append([o, max(o, c) + 0.15, min(o, c) - 0.15, c])
    return rows

def flat(n=30, price=100.0):
    return [[price, price + 0.4, price - 0.4, price + 0.1] for _ in range(n)]


def test_bullish_fvg_detected():
    # gap: candle 3's low (103) sits above candle 1's high (100.5), never filled after
    rows = flat(25) + [[100.0, 100.5, 99.6, 100.4],
                       [100.4, 103.5, 100.3, 103.3],
                       [103.3, 104.5, 103.0, 104.2]] + \
           [[104.2, 104.6, 103.9, 104.3] for _ in range(3)]
    gaps = [z for z in find_fvgs(frame(rows)) if z.direction == "bullish"]
    assert gaps, "expected a bullish FVG"
    z = gaps[-1]
    assert z.bottom < z.top and z.bottom >= 100.0


def test_bearish_fvg_detected():
    rows = flat(25) + [[100.0, 100.4, 99.5, 99.6],
                       [99.6, 99.7, 96.5, 96.7],
                       [96.7, 97.0, 95.8, 96.0]] + \
           [[96.0, 96.4, 95.6, 96.1] for _ in range(3)]
    gaps = [z for z in find_fvgs(frame(rows)) if z.direction == "bearish"]
    assert gaps


def test_filled_fvg_is_ignored():
    # same bullish gap, but price comes back and fills it completely
    rows = flat(25) + [[100.0, 100.5, 99.6, 100.4],
                       [100.4, 103.5, 100.3, 103.3],
                       [103.3, 104.5, 103.0, 104.2],
                       [104.0, 104.2, 99.0, 99.5],
                       [99.5, 100.0, 99.0, 99.4]]
    gaps = [z for z in find_fvgs(frame(rows)) if z.direction == "bullish" and z.bottom >= 100.0]
    assert not gaps


def test_sellside_liquidity_sweep():
    rows = flat(20, 100.0) + [[100.0, 100.2, 98.0, 100.1]]
    assert liquidity_sweep(frame(rows)) == "sellside"


def test_buyside_liquidity_sweep():
    rows = flat(20, 100.0) + [[100.0, 102.5, 99.9, 100.0]]
    assert liquidity_sweep(frame(rows)) == "buyside"


def test_no_sweep_on_quiet_bar():
    assert liquidity_sweep(frame(flat(25))) == ""


def test_market_structure_turns_bullish():
    # HH / HL zig-zag: 100 -> 110 -> 104 -> 118 -> 111 -> 126
    rows = path([100, 110, 104, 118, 111, 126])
    structure, brk = market_structure(frame(rows))
    assert structure == "up"
    assert brk in ("BOS", "CHoCH")


def test_market_structure_turns_bearish():
    rows = path([130, 118, 124, 108, 114, 98])
    structure, _ = market_structure(frame(rows))
    assert structure == "down"


def test_choch_flags_a_flip():
    # clean uptrend, then price closes below the last higher low
    rows = path([100, 112, 106, 122, 114, 128]) + path([128, 112], steps=6)
    structure, brk = market_structure(frame(rows))
    assert structure == "down" and brk == "CHoCH"


def test_order_block_found_before_displacement():
    rows = flat(25, 100.0)
    rows.append([100.0, 100.2, 99.2, 99.3])   # down candle = the order block
    rows.append([99.3, 104.0, 99.2, 103.8])   # displacement leg breaking structure
    obs = [z for z in find_order_blocks(frame(rows)) if z.direction == "bullish"]
    assert obs and obs[-1].bottom <= 99.3 <= obs[-1].top


def test_displacement_flag():
    rows = flat(25, 100.0) + [[100.0, 103.0, 99.9, 102.8]]
    assert is_displacement(frame(rows)) is True
    assert is_displacement(frame(flat(25))) is False


def test_confluence_is_capped_and_tagged():
    rows = flat(20, 100.0) + [[100.0, 100.2, 98.0, 100.1]]
    df = frame(rows)
    ctx = analyse(df)
    bonus, tags, reasons = confluence(ctx, "bullish", 98.0, atr_value=1.0)
    assert bonus <= config.SMC_MAX_BONUS
    assert "Sweep" in tags
    assert any("liquidity" in r for r in reasons)


def test_confluence_neutral_when_nothing_lines_up():
    ctx = analyse(frame(flat(40)))
    bonus, tags, _ = confluence(ctx, "bullish", 100.0, atr_value=1.0)
    assert bonus <= config.SMC_MAX_BONUS
    assert "FVG" not in tags and "Sweep" not in tags


def test_smc_cannot_create_a_signal_alone():
    """No candlestick pattern -> no signal, however good the SMC picture is."""
    from app.patterns import detect_all
    rows = flat(20, 100.0) + [[100.0, 100.2, 98.0, 98.2]]   # sweep but bearish close, no pattern
    df = frame(rows)
    ctx = analyse(df)
    bonus, tags, _ = confluence(ctx, "bullish", 98.0, atr_value=1.0)
    assert bonus > 0                      # SMC context exists
    assert not [p for p in detect_all(df) if p.direction == "bullish"]

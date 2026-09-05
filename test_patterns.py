"""Synthetic-candle unit tests for the pattern detectors and the level engine."""
import pandas as pd
import pytest

from app.indicators import enrich
from app.levels import cluster, find_pivots, Level
from app.patterns import (detect_engulfing, detect_inside_bar_break, detect_pin_bar,
                          detect_star, detect_tweezer)


def frame(rows):
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 100.0
    return enrich(df)


def base(n=30, price=100.0):
    return [[price, price + 0.4, price - 0.4, price + 0.1] for _ in range(n)]


def test_bullish_engulfing():
    rows = base() + [[100.0, 100.2, 99.0, 99.2], [99.2, 101.0, 99.1, 100.9]]
    p = detect_engulfing(frame(rows))
    assert p and p.direction == "bullish" and "Engulfing" in p.name


def test_bearish_engulfing():
    rows = base() + [[100.0, 101.0, 99.9, 100.9], [100.9, 101.0, 99.0, 99.1]]
    p = detect_engulfing(frame(rows))
    assert p and p.direction == "bearish"


def test_no_engulfing_on_noise():
    assert detect_engulfing(frame(base())) is None


def test_bullish_pin_bar():
    rows = base() + [[100.0, 100.2, 98.0, 100.1]]
    p = detect_pin_bar(frame(rows))
    assert p and p.direction == "bullish" and p.stop_price == pytest.approx(98.0)


def test_bearish_pin_bar():
    rows = base() + [[100.0, 102.0, 99.9, 100.0]]
    p = detect_pin_bar(frame(rows))
    assert p and p.direction == "bearish"


def test_morning_star():
    rows = base() + [[100.0, 100.1, 98.0, 98.1], [98.0, 98.2, 97.8, 97.9], [98.0, 99.9, 97.9, 99.8]]
    p = detect_star(frame(rows))
    assert p and p.direction == "bullish" and p.name == "Morning Star"


def test_inside_bar_breakout():
    rows = base() + [[100.0, 101.0, 99.0, 100.5], [100.4, 100.8, 99.6, 100.0], [100.1, 101.8, 100.0, 101.6]]
    p = detect_inside_bar_break(frame(rows))
    assert p and p.direction == "bullish"


def test_tweezer_bottom():
    rows = base() + [[100.0, 100.1, 98.5, 99.4], [99.4, 100.6, 98.52, 100.4]]
    p = detect_tweezer(frame(rows))
    assert p and p.direction == "bullish"


def test_pivots_found():
    rows = base(10) + [[100, 105, 99.5, 100]] + base(10)
    piv = find_pivots(frame(rows), 3, 3)
    assert any(l.kind == "swing_high" and l.price == 105 for l in piv)


def test_cluster_merges_nearby_levels():
    lv = [Level(100.0, "swing_high"), Level(100.05, "round"), Level(105.0, "swing_low")]
    out = cluster(lv, tol=0.2)
    assert len(out) == 2 and out[0].touches == 2


def test_closed_bar_filter():
    from app.data import _drop_unclosed
    idx = pd.date_range(pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=3),
                        periods=4, freq="1h")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}, index=idx)
    out = _drop_unclosed(df, "1h")
    assert len(out) < len(df)

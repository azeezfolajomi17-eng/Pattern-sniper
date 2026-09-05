"""Tests for the HTF -> LTF projection layer."""
import pandas as pd
import pytest

from app import config
from app.htf import HTFContext, Pool, confluence, htf_for, _liquidity_pools
from app.indicators import enrich
from app.smc import Zone


def frame(rows):
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="4h", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 100.0
    return enrich(df)


def path(points, steps=4):
    rows = []
    for a, b in zip(points, points[1:]):
        for k in range(steps):
            o = a + (b - a) * k / steps
            c = a + (b - a) * (k + 1) / steps
            rows.append([o, max(o, c) + 0.15, min(o, c) - 0.15, c])
    return rows


def ctx(**kw):
    base = dict(timeframe="4h", structure="up", last_break="BOS",
                zones=[], pools=[], range_high=120.0, range_low=100.0, atr=1.0)
    base.update(kw)
    return HTFContext(**base)


# --- timeframe pairing ------------------------------------------------------------
def test_default_pairings():
    assert htf_for("15m") == "4h"
    assert htf_for("1h") == "4h"
    assert htf_for("4h") == "1d"


def test_no_pairing_for_top_timeframe():
    assert htf_for("1d") is None


# --- premium / discount -----------------------------------------------------------
def test_discount_premium_equilibrium():
    c = ctx()
    assert c.equilibrium == 110.0
    assert c.premium_discount(102.0) == "discount"
    assert c.premium_discount(118.0) == "premium"
    assert c.premium_discount(110.0) == "equilibrium"


def test_long_in_discount_scores_better_than_long_in_premium():
    c = ctx()
    good, tags, _, _ = confluence(c, "bullish", 102.0, atr_value=1.0)
    bad, _, _, _ = confluence(c, "bullish", 118.0, atr_value=1.0)
    assert good > bad
    assert "discount" in tags


# --- HTF POI projection -----------------------------------------------------------
def test_ltf_entry_tapping_htf_fvg_is_rewarded():
    zone = Zone(top=105.0, bottom=103.0, direction="bullish", kind="fvg",
                index=pd.Timestamp("2025-01-05", tz="UTC"))
    with_poi, tags, reasons, has_poi = confluence(ctx(zones=[zone]), "bullish", 104.0, 1.0)
    without, _, _, no_poi = confluence(ctx(), "bullish", 104.0, 1.0)
    assert has_poi is True and no_poi is False
    assert with_poi > without
    assert "4H FVG" in tags
    assert any("4H bullish FVG" in r for r in reasons)


def test_zone_tolerance_allows_a_wick_just_outside():
    zone = Zone(top=105.0, bottom=103.0, direction="bullish", kind="ob",
                index=pd.Timestamp("2025-01-05", tz="UTC"))
    c = ctx(zones=[zone])
    # 0.35 * ATR(2.0) = 0.7 pad -> 102.5 still counts, 101.0 does not
    assert c.zone_at(102.5, "bullish", pad=0.7) is not None
    assert c.zone_at(101.0, "bullish", pad=0.7) is None


def test_opposite_direction_zone_is_ignored():
    zone = Zone(top=105.0, bottom=103.0, direction="bearish", kind="fvg",
                index=pd.Timestamp("2025-01-05", tz="UTC"))
    assert ctx(zones=[zone]).zone_at(104.0, "bullish", pad=0.5) is None


def test_freshest_zone_wins():
    old = Zone(105.0, 103.0, "bullish", "ob", pd.Timestamp("2025-01-01", tz="UTC"))
    new = Zone(105.5, 103.5, "bullish", "fvg", pd.Timestamp("2025-02-01", tz="UTC"))
    z = ctx(zones=[old, new]).zone_at(104.0, "bullish", pad=0.5)
    assert z.kind == "fvg"


# --- structure --------------------------------------------------------------------
def test_fighting_htf_structure_is_penalised():
    aligned, _, _, _ = confluence(ctx(structure="up"), "bullish", 102.0, 1.0)
    against, _, reasons, _ = confluence(ctx(structure="up"), "bearish", 118.0, 1.0)
    assert against < aligned
    assert any("Fighting" in r for r in reasons)


def test_choch_is_not_penalised():
    c = ctx(structure="up", last_break="CHoCH")
    score, _, reasons, _ = confluence(c, "bearish", 118.0, 1.0)
    assert any("CHoCH" in r for r in reasons)
    assert score > -config.HTF_CONTRA_PENALTY


def test_bonus_is_capped():
    zone = Zone(105.0, 103.0, "bullish", "fvg", pd.Timestamp("2025-01-05", tz="UTC"))
    score, _, _, _ = confluence(ctx(zones=[zone]), "bullish", 104.0, 1.0)
    assert score <= config.HTF_MAX_BONUS


def test_disabled_context_is_neutral():
    score, tags, reasons, has_poi = confluence(None, "bullish", 100.0, 1.0)
    assert (score, tags, reasons, has_poi) == (0.0, [], [], False)


# --- liquidity --------------------------------------------------------------------
def test_draw_on_liquidity_picks_nearest_pool_above():
    c = ctx(pools=[Pool(115.0, "buyside", pd.Timestamp("2025-01-02", tz="UTC")),
                   Pool(125.0, "buyside", pd.Timestamp("2025-01-03", tz="UTC")),
                   Pool(95.0, "sellside", pd.Timestamp("2025-01-04", tz="UTC"))])
    assert c.draw_on_liquidity(105.0, "bullish", min_gap=1.0) == 115.0
    assert c.draw_on_liquidity(105.0, "bearish", min_gap=1.0) == 95.0


def test_draw_on_liquidity_respects_min_gap():
    c = ctx(pools=[Pool(106.0, "buyside", pd.Timestamp("2025-01-02", tz="UTC"))])
    assert c.draw_on_liquidity(105.0, "bullish", min_gap=5.0) is None


def test_liquidity_pools_found_from_swings():
    df = frame(path([100, 112, 104, 118, 110, 116]))
    pools = _liquidity_pools(df)
    assert any(p.side == "buyside" for p in pools)
    assert any(p.side == "sellside" for p in pools)


# --- integration ------------------------------------------------------------------
def test_htf_required_blocks_setups_without_a_poi(monkeypatch):
    """With HTF_REQUIRED on, an entry outside every HTF POI must be dropped."""
    from app import signals

    monkeypatch.setattr(config, "HTF_REQUIRED", True)
    _, _, _, has_poi = confluence(ctx(), "bullish", 104.0, 1.0)
    assert has_poi is False   # signals.evaluate `continue`s on exactly this flag


# --- score normalisation ----------------------------------------------------------
def test_score_below_knee_passes_through():
    from app.signals import compress_score
    assert compress_score(70) == 70
    assert compress_score(82) == 82


def test_score_never_clips_and_stays_ordered():
    from app.signals import compress_score
    vals = [compress_score(r) for r in (86, 95, 105, 120, 135, 150)]
    assert all(v <= 100 for v in vals)
    assert vals == sorted(vals)
    assert len(set(vals[:-1])) == len(vals[:-1])      # distinct below the ceiling
    assert compress_score(135) == 100


# --- dependency guard -------------------------------------------------------------
def test_check_provider_flags_missing_yfinance(monkeypatch):
    """A missing data package must fail loudly, not look like 'no setups found'."""
    import builtins
    from app import data as data_mod

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "yfinance":
            raise ImportError("No module named 'yfinance'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(data_mod, "DATA_PROVIDER", "yahoo")
    monkeypatch.setattr(builtins, "__import__", fake_import)
    msg = data_mod.check_provider()
    assert "yfinance" in msg and "pip install" in msg


def test_check_provider_requires_key_for_twelvedata(monkeypatch):
    from app import data as data_mod
    monkeypatch.setattr(data_mod, "DATA_PROVIDER", "twelvedata")
    monkeypatch.setattr(data_mod, "TWELVEDATA_API_KEY", "")
    assert "TWELVEDATA_API_KEY" in data_mod.check_provider()


def test_check_provider_ok_when_installed(monkeypatch):
    from app import data as data_mod
    monkeypatch.setattr(data_mod, "DATA_PROVIDER", "yahoo")
    assert data_mod.check_provider() == ""

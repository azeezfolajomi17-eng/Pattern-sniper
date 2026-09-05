"""
Market data layer.

Default provider is Yahoo Finance via `yfinance`: free, no API key, and it covers
all three asset classes you asked for (FX majors, metals futures, index futures).

A Twelve Data provider is included as a drop-in alternative — set
DATA_PROVIDER=twelvedata and TWELVEDATA_API_KEY if you'd rather use a keyed feed.

Both providers return the same thing: a UTC-indexed DataFrame with
open/high/low/close/volume columns containing CLOSED candles only.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, Tuple

import pandas as pd

log = logging.getLogger(__name__)

DATA_PROVIDER = os.getenv("DATA_PROVIDER", "yahoo").strip().lower()
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")

# timeframe -> (native yfinance interval, history period, resample rule or None)
_YF_SPEC: Dict[str, Tuple[str, str, str | None]] = {
    "5m": ("5m", "20d", None),
    "15m": ("15m", "60d", None),
    "30m": ("30m", "60d", None),
    "1h": ("60m", "180d", None),
    "4h": ("60m", "360d", "4h"),
    "1d": ("1d", "3y", None),
}

_TF_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}

_CACHE: Dict[Tuple[str, str], Tuple[float, pd.DataFrame]] = {}
_CACHE_TTL = {"5m": 60, "15m": 120, "30m": 200, "1h": 300, "4h": 600, "1d": 1800}


def timeframe_minutes(timeframe: str) -> int:
    return _TF_MINUTES.get(timeframe, 60)


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase OHLCV columns, UTC index, no NaN rows, sorted."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={c: str(c).strip().lower() for c in df.columns})
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep].copy()
    if "volume" not in df.columns:
        df["volume"] = 0.0
    idx = pd.to_datetime(df.index, utc=True)
    df.index = idx
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["open", "high", "low", "close"])


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = df.resample(rule, label="left", closed="left", origin="epoch").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open", "high", "low", "close"])


def _drop_unclosed(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Keep only bars whose close time has already passed — never trade a forming candle."""
    if df.empty:
        return df
    minutes = timeframe_minutes(timeframe)
    now = pd.Timestamp.now(tz="UTC")
    bar_end = df.index + pd.Timedelta(minutes=minutes)
    return df[bar_end <= now]


def check_provider() -> str:
    """
    Verify the data provider is usable. Returns '' when fine, else an error message.
    Called at startup so a missing dependency fails loudly instead of looking like
    'no setups found'.
    """
    if DATA_PROVIDER == "twelvedata":
        if not TWELVEDATA_API_KEY:
            return ("DATA_PROVIDER=twelvedata but TWELVEDATA_API_KEY is not set. "
                    "Add the key or switch to DATA_PROVIDER=yahoo.")
        return ""
    try:
        import yfinance  # noqa: F401
    except ImportError:
        return ("The 'yfinance' package is not installed, so no market data can be "
                "fetched (every scan will look empty). Fix with:\n"
                "    pip install -r requirements.txt")
    return ""


def _fetch_yahoo(yf_symbol: str, timeframe: str) -> pd.DataFrame:
    import yfinance as yf  # imported lazily so tests can stub the provider

    interval, period, rule = _YF_SPEC.get(timeframe, _YF_SPEC["1h"])
    raw = yf.Ticker(yf_symbol).history(period=period, interval=interval, auto_adjust=False)
    df = _normalise(raw)
    if df.empty:
        return df
    if rule:
        df = _resample(df, rule)
    return df


def _fetch_twelvedata(symbol: str, timeframe: str) -> pd.DataFrame:
    import requests

    td_interval = {"5m": "5min", "15m": "15min", "30m": "30min",
                   "1h": "1h", "4h": "4h", "1d": "1day"}.get(timeframe, "1h")
    r = requests.get(
        "https://api.twelvedata.com/time_series",
        params={"symbol": symbol, "interval": td_interval, "outputsize": 500,
                "apikey": TWELVEDATA_API_KEY, "timezone": "UTC"},
        timeout=20,
    )
    payload = r.json()
    if payload.get("status") == "error" or "values" not in payload:
        raise RuntimeError(f"twelvedata error for {symbol}: {payload.get('message', payload)}")
    df = pd.DataFrame(payload["values"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").astype(float)
    return _normalise(df)


def get_candles(inst, timeframe: str, use_cache: bool = True) -> pd.DataFrame:
    """Closed candles for an instrument/timeframe, cached briefly to spare the API."""
    key = (inst.name, timeframe)
    ttl = _CACHE_TTL.get(timeframe, 120)
    now = time.time()
    if use_cache and key in _CACHE:
        ts, cached = _CACHE[key]
        if now - ts < ttl:
            return cached

    try:
        if DATA_PROVIDER == "twelvedata":
            df = _fetch_twelvedata(inst.td if hasattr(inst, "td") else inst.name, timeframe)
        else:
            df = _fetch_yahoo(inst.yf, timeframe)
    except Exception as exc:  # network hiccup -> serve stale cache rather than crash the loop
        log.warning("data fetch failed %s %s: %s", inst.name, timeframe, exc)
        return _CACHE.get(key, (0, pd.DataFrame()))[1]

    df = _drop_unclosed(df, timeframe)
    if not df.empty:
        _CACHE[key] = (now, df)
    return df

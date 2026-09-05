"""Small, dependency-light indicator toolkit (pandas only)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the indicator set the model uses."""
    out = df.copy()
    out["ema20"] = ema(out["close"], 20)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["atr"] = atr(out, 14)
    out["rsi"] = rsi(out["close"], 14)
    out["body"] = (out["close"] - out["open"]).abs()
    out["range"] = (out["high"] - out["low"]).replace(0, np.nan)
    out["upper_wick"] = out["high"] - out[["open", "close"]].max(axis=1)
    out["lower_wick"] = out[["open", "close"]].min(axis=1) - out["low"]
    out["avg_range"] = out["range"].rolling(20).mean()
    return out


def trend_state(row: pd.Series) -> str:
    """Coarse trend read from the EMA stack: up / down / range."""
    e20, e50, e200, close = row.get("ema20"), row.get("ema50"), row.get("ema200"), row["close"]
    if any(pd.isna(x) for x in (e20, e50)):
        return "range"
    if pd.isna(e200):
        e200 = e50
    if close > e50 and e20 > e50 and close > e200:
        return "up"
    if close < e50 and e20 < e50 and close < e200:
        return "down"
    if close > e50 and e20 > e50:
        return "up"
    if close < e50 and e20 < e50:
        return "down"
    return "range"

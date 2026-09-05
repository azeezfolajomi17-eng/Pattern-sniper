"""
Configuration: watchlist, timeframes, model thresholds, env vars.

Everything here can be overridden with environment variables so you can tune the
bot on your host without editing code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader so local runs don't need python-dotenv."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_dotenv()



def _env(key: str, default: str) -> str:
    v = os.getenv(key)
    return v if v not in (None, "") else default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(_env(key, str(default))))
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    return _env(key, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN", "")
# Optional: pre-seed a chat id so the bot pushes alerts without waiting for /start
TELEGRAM_CHAT_ID: str = _env("TELEGRAM_CHAT_ID", "")

# --------------------------------------------------------------------------------------
# Engine settings
# --------------------------------------------------------------------------------------
SCAN_INTERVAL_SECONDS: int = _env_int("SCAN_INTERVAL_SECONDS", 60)
MIN_SCORE: int = _env_int("MIN_SCORE", 82)          # alert threshold (0-100)
MIN_RR: float = _env_float("MIN_RR", 1.8)           # discard setups with worse reward:risk
# Soft-knee score normalisation: raw points pass through untouched up to the knee,
# then the theoretical maximum is compressed into the remaining headroom.
SCORE_KNEE: float = _env_float("SCORE_KNEE", 85.0)
SCORE_RAW_MAX: float = _env_float("SCORE_RAW_MAX", 135.0)
COOLDOWN_BARS: int = _env_int("COOLDOWN_BARS", 6)   # min bars between alerts on same symbol/tf
MAX_ALERTS_PER_SCAN: int = _env_int("MAX_ALERTS_PER_SCAN", 6)
STATE_FILE: str = _env("STATE_FILE", "state.json")
HEALTH_PORT: int = _env_int("PORT", 8080)           # Render/Railway inject PORT
LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")

# Level detection
PIVOT_LEFT: int = _env_int("PIVOT_LEFT", 3)
PIVOT_RIGHT: int = _env_int("PIVOT_RIGHT", 3)
LEVEL_ATR_TOLERANCE: float = _env_float("LEVEL_ATR_TOLERANCE", 0.40)  # zone half-width in ATR
PROXIMITY_ATR: float = _env_float("PROXIMITY_ATR", 0.55)              # candle must react this close
LOOKBACK_BARS: int = _env_int("LOOKBACK_BARS", 400)

# Timeframes to scan
TIMEFRAMES: List[str] = [t.strip() for t in _env("TIMEFRAMES", "15m,1h,4h").split(",") if t.strip()]

# Trading session filter (UTC hours). Empty = 24/5.
SESSION_START_UTC: int = _env_int("SESSION_START_UTC", 0)
SESSION_END_UTC: int = _env_int("SESSION_END_UTC", 24)

# Only alert when the setup agrees with higher-timeframe bias
REQUIRE_HTF_BIAS: bool = _env_bool("REQUIRE_HTF_BIAS", False)

# --------------------------------------------------------------------------------------
# SMC confluence layer (bonus points only — never creates a signal by itself)
# --------------------------------------------------------------------------------------
SMC_ENABLED: bool = _env_bool("SMC_ENABLED", True)
SMC_MAX_BONUS: float = _env_float("SMC_MAX_BONUS", 12.0)   # total cap, keeps it "slight"
SMC_FVG_BONUS: float = _env_float("SMC_FVG_BONUS", 6.0)
SMC_OB_BONUS: float = _env_float("SMC_OB_BONUS", 5.0)
SMC_SWEEP_BONUS: float = _env_float("SMC_SWEEP_BONUS", 6.0)
SMC_STRUCTURE_BONUS: float = _env_float("SMC_STRUCTURE_BONUS", 3.0)
SMC_DISPLACEMENT_BONUS: float = _env_float("SMC_DISPLACEMENT_BONUS", 2.0)
SMC_CONTRA_PENALTY: float = _env_float("SMC_CONTRA_PENALTY", 6.0)  # into opposing structure
# Require at least one SMC element (FVG / OB / sweep / aligned structure) to alert
SMC_REQUIRED: bool = _env_bool("SMC_REQUIRED", False)

# --------------------------------------------------------------------------------------
# HTF -> LTF layer: higher-timeframe POIs projected onto the entry timeframe
# --------------------------------------------------------------------------------------
HTF_ENABLED: bool = _env_bool("HTF_ENABLED", True)
# entry timeframe : governing timeframe
HTF_MAP_RAW: str = _env("HTF_MAP", "5m:1h,15m:4h,30m:4h,1h:4h,4h:1d")
HTF_ZONE_LOOKBACK: int = _env_int("HTF_ZONE_LOOKBACK", 150)   # HTF bars scanned for POIs
HTF_RANGE_BARS: int = _env_int("HTF_RANGE_BARS", 60)          # dealing range window
HTF_MAX_BONUS: float = _env_float("HTF_MAX_BONUS", 20.0)
HTF_FVG_BONUS: float = _env_float("HTF_FVG_BONUS", 10.0)
HTF_OB_BONUS: float = _env_float("HTF_OB_BONUS", 9.0)
HTF_PD_BONUS: float = _env_float("HTF_PD_BONUS", 5.0)         # discount longs / premium shorts
HTF_STRUCTURE_BONUS: float = _env_float("HTF_STRUCTURE_BONUS", 5.0)
HTF_CONTRA_PENALTY: float = _env_float("HTF_CONTRA_PENALTY", 8.0)
# Only alert when the LTF entry is actually tapping an HTF FVG / order block
HTF_REQUIRED: bool = _env_bool("HTF_REQUIRED", True)
# Target the next unswept HTF liquidity pool instead of a flat 2R when it's reachable
HTF_TARGET_LIQUIDITY: bool = _env_bool("HTF_TARGET_LIQUIDITY", True)
HTF_TARGET_MAX_RR: float = _env_float("HTF_TARGET_MAX_RR", 6.0)


@dataclass(frozen=True)
class Instrument:
    """One tradable symbol on the watchlist."""
    name: str          # display name, e.g. "XAUUSD"
    yf: str            # yfinance ticker used to pull candles
    kind: str          # fx | metal | index
    digits: int        # price rounding for messages
    pip: float         # 1 pip / 1 point in price terms
    round_step: float  # psychological level spacing (00 / 50 levels)
    tv: str            # TradingView symbol, used to build a chart link


# Majors + metals + US indices (your "core" watchlist)
WATCHLIST: List[Instrument] = [
    Instrument("EURUSD", "EURUSD=X", "fx", 5, 0.0001, 0.0050, "FX:EURUSD"),
    Instrument("GBPUSD", "GBPUSD=X", "fx", 5, 0.0001, 0.0050, "FX:GBPUSD"),
    Instrument("USDJPY", "USDJPY=X", "fx", 3, 0.01, 0.50, "FX:USDJPY"),
    Instrument("AUDUSD", "AUDUSD=X", "fx", 5, 0.0001, 0.0050, "FX:AUDUSD"),
    Instrument("USDCAD", "USDCAD=X", "fx", 5, 0.0001, 0.0050, "FX:USDCAD"),
    Instrument("USDCHF", "USDCHF=X", "fx", 5, 0.0001, 0.0050, "FX:USDCHF"),
    Instrument("NZDUSD", "NZDUSD=X", "fx", 5, 0.0001, 0.0050, "FX:NZDUSD"),
    Instrument("EURJPY", "EURJPY=X", "fx", 3, 0.01, 0.50, "FX:EURJPY"),
    Instrument("XAUUSD", "GC=F", "metal", 2, 0.01, 10.0, "OANDA:XAUUSD"),
    Instrument("XAGUSD", "SI=F", "metal", 3, 0.001, 0.50, "OANDA:XAGUSD"),
    Instrument("NAS100", "NQ=F", "index", 1, 0.1, 100.0, "PEPPERSTONE:NAS100"),
    Instrument("US30", "YM=F", "index", 0, 1.0, 250.0, "PEPPERSTONE:US30"),
    Instrument("SPX500", "ES=F", "index", 2, 0.25, 25.0, "PEPPERSTONE:US500"),
]

# Allow trimming the watchlist from env: WATCHLIST=EURUSD,XAUUSD,NAS100
_wl = _env("WATCHLIST", "")
if _wl:
    wanted = {s.strip().upper() for s in _wl.split(",") if s.strip()}
    WATCHLIST = [i for i in WATCHLIST if i.name.upper() in wanted] or WATCHLIST


def by_name(name: str) -> Instrument | None:
    for i in WATCHLIST:
        if i.name.upper() == name.upper():
            return i
    return None


def tv_chart_url(inst: Instrument, timeframe: str) -> str:
    tf_map = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}
    return f"https://www.tradingview.com/chart/?symbol={inst.tv}&interval={tf_map.get(timeframe, '60')}"

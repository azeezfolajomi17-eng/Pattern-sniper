"""Persistent state: subscribed chats, dedupe keys, mute flag, recent alerts."""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List

from . import config

_LOCK = threading.Lock()

_DEFAULT: Dict[str, Any] = {
    "chats": [],
    "muted": False,
    "alerted_keys": [],     # dedupe: one alert per symbol/timeframe/bar
    "last_alert": {},       # cooldown: symbol|tf -> bar_time of last alert
    "recent": [],           # last 25 signals for /last
    "min_score": config.MIN_SCORE,
    "stats": {"scans": 0, "alerts": 0, "last_scan": None},
}


def _path() -> str:
    return os.path.abspath(config.STATE_FILE)


def load() -> Dict[str, Any]:
    with _LOCK:
        try:
            with open(_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            merged = dict(_DEFAULT)
            merged.update(data)
            return merged
        except Exception:
            return dict(_DEFAULT)


def save(state: Dict[str, Any]) -> None:
    with _LOCK:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        os.replace(tmp, _path())


def add_chat(chat_id: int | str) -> bool:
    st = load()
    cid = str(chat_id)
    if cid in [str(c) for c in st["chats"]]:
        return False
    st["chats"].append(cid)
    save(st)
    return True


def chats() -> List[str]:
    st = load()
    out = [str(c) for c in st["chats"]]
    if config.TELEGRAM_CHAT_ID and config.TELEGRAM_CHAT_ID not in out:
        out.append(config.TELEGRAM_CHAT_ID)
    return out


def already_alerted(key: str) -> bool:
    return key in load()["alerted_keys"]


def mark_alerted(key: str, signal_dict: Dict[str, Any] | None = None) -> None:
    st = load()
    st["alerted_keys"] = (st["alerted_keys"] + [key])[-500:]
    if signal_dict:
        st["recent"] = ([signal_dict] + st["recent"])[:25]
        st["stats"]["alerts"] = st["stats"].get("alerts", 0) + 1
        slot = f"{signal_dict['symbol']}|{signal_dict['timeframe']}"
        st.setdefault("last_alert", {})[slot] = signal_dict["bar_time"]
    save(st)


def in_cooldown(symbol: str, timeframe: str, bar_time: str, tf_minutes: int,
                cooldown_bars: int) -> bool:
    """True if we already alerted this symbol/timeframe less than N bars ago."""
    if cooldown_bars <= 0:
        return False
    import datetime as _dt
    prev = load().get("last_alert", {}).get(f"{symbol}|{timeframe}")
    if not prev:
        return False
    fmt = "%Y-%m-%d %H:%M UTC"
    try:
        a = _dt.datetime.strptime(prev, fmt)
        b = _dt.datetime.strptime(bar_time, fmt)
    except ValueError:
        return False
    return (b - a).total_seconds() < cooldown_bars * tf_minutes * 60


def note_scan(when: str) -> None:
    st = load()
    st["stats"]["scans"] = st["stats"].get("scans", 0) + 1
    st["stats"]["last_scan"] = when
    save(st)


def set_flag(name: str, value: Any) -> None:
    st = load()
    st[name] = value
    save(st)


def get_flag(name: str, default: Any = None) -> Any:
    return load().get(name, default)

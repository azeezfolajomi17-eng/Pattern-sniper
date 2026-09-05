"""Minimal Telegram Bot API client — send messages and long-poll for commands."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from . import config

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: Optional[str] = None):
        self.token = token or config.TELEGRAM_BOT_TOKEN
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _call(self, method: str, payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
        if not self.enabled:
            log.info("[telegram disabled] %s %s", method, payload.get("text", "")[:120])
            return {"ok": False, "description": "no token"}
        url = API.format(token=self.token, method=method)
        for attempt in range(3):
            try:
                r = self.session.post(url, json=payload, timeout=timeout)
                data = r.json()
                if data.get("ok"):
                    return data
                if r.status_code == 429:
                    wait = data.get("parameters", {}).get("retry_after", 3)
                    time.sleep(wait + 1)
                    continue
                log.warning("telegram %s failed: %s", method, data.get("description"))
                return data
            except Exception as exc:
                log.warning("telegram %s error (%s/3): %s", method, attempt + 1, exc)
                time.sleep(2 * (attempt + 1))
        return {"ok": False}

    def send(self, chat_id: str | int, text: str, markup: Optional[dict] = None,
             preview: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": not preview,
        }
        if markup:
            payload["reply_markup"] = markup
        return self._call("sendMessage", payload)

    def broadcast(self, chat_ids: List[str | int], text: str, markup: Optional[dict] = None) -> None:
        for cid in chat_ids:
            self.send(cid, text, markup)
            time.sleep(0.35)  # stay under Telegram's ~30 msg/sec global limit

    def get_updates(self, offset: int, timeout: int = 25) -> List[Dict[str, Any]]:
        if not self.enabled:
            time.sleep(timeout)
            return []
        url = API.format(token=self.token, method="getUpdates")
        try:
            r = self.session.get(
                url, params={"offset": offset, "timeout": timeout},
                timeout=timeout + 10,
            )
            data = r.json()
            return data.get("result", []) if data.get("ok") else []
        except requests.exceptions.ReadTimeout:
            return []
        except Exception as exc:
            log.warning("getUpdates error: %s", exc)
            time.sleep(3)
            return []

    def set_commands(self) -> None:
        self._call("setMyCommands", {"commands": [
            {"command": "start", "description": "Subscribe this chat to alerts"},
            {"command": "scan", "description": "Force a scan right now"},
            {"command": "status", "description": "Bot status and settings"},
            {"command": "watchlist", "description": "Symbols and timeframes being scanned"},
            {"command": "last", "description": "Most recent alerts"},
            {"command": "score", "description": "Set minimum score, e.g. /score 70"},
            {"command": "mute", "description": "Pause alerts"},
            {"command": "unmute", "description": "Resume alerts"},
            {"command": "help", "description": "How the model works"},
        ]})

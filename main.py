"""Entrypoint: starts the scanner, the Telegram command loop and a health endpoint."""
from __future__ import annotations

import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import config, state
from .data import check_provider
from .scanner import command_loop, scanner_loop, tg

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("main")

stop_event = threading.Event()


class Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        st = state.load()
        body = (
            f"ok\nsymbols={len(config.WATCHLIST)}\n"
            f"timeframes={','.join(config.TIMEFRAMES)}\n"
            f"chats={len(st['chats'])}\nscans={st['stats'].get('scans', 0)}\n"
            f"alerts={st['stats'].get('alerts', 0)}\n"
            f"last_scan={st['stats'].get('last_scan')}\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):  # silence access logs
        pass


def serve_health():
    try:
        HTTPServer(("0.0.0.0", config.HEALTH_PORT), Health).serve_forever()
    except Exception as exc:
        log.warning("health server not started: %s", exc)


def main() -> None:
    log.info("starting pattern bot | provider=%s | %d symbols | tf=%s",
             __import__("os").getenv("DATA_PROVIDER", "yahoo"),
             len(config.WATCHLIST), ",".join(config.TIMEFRAMES))

    problem = check_provider()
    if problem:
        log.error("MARKET DATA UNAVAILABLE — %s", problem)
        raise SystemExit(1)

    if not tg.enabled:
        log.warning("No TELEGRAM_BOT_TOKEN — running in dry-run mode (alerts go to the log)")

    threads = [
        threading.Thread(target=serve_health, daemon=True, name="health"),
        threading.Thread(target=scanner_loop, args=(stop_event,), daemon=True, name="scanner"),
        threading.Thread(target=command_loop, args=(stop_event,), daemon=True, name="commands"),
    ]
    for t in threads:
        t.start()

    def shutdown(*_):
        log.info("shutting down…")
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    for cid in state.chats():
        tg.send(cid, "🤖 Scanner online — watching "
                     f"{len(config.WATCHLIST)} instruments on {', '.join(config.TIMEFRAMES)}.")

    while not stop_event.is_set():
        stop_event.wait(1)


if __name__ == "__main__":
    main()

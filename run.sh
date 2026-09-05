#!/usr/bin/env bash
# Convenience launcher: install deps, verify Telegram + data, start the scanner.
set -e
cd "$(dirname "$0")"

python3 -m pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created .env — paste your TELEGRAM_BOT_TOKEN into it, then run ./run.sh again."
  echo "  (Get a token from @BotFather in Telegram: /newbot)"
  exit 1
fi

python3 -m app.check --no-test || true
echo
python3 -m app.main

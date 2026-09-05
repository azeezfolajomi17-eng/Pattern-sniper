# Telegram Pattern Scanner Bot

An **HTF→LTF entry model** for FX majors, metals and US indices. It maps the higher
timeframe first (FVGs, order blocks, structure, premium/discount, resting liquidity),
then waits for a **candlestick reaction at a key level** on your entry timeframe *inside*
one of those HTF POIs — and pushes an instant Telegram alert with entry, stop, target and
the full reasoning, the moment the candle closes.

```
🟢 XAUUSD — LONG  15m
Bullish Engulfing at swing low x4 + swing high
🏛 HTF: 4H FVG · discount · 4H CHoCH
🧠 SMC: FVG · OB · CHoCH

Score 95/100  ██████████

Entry   3421.60
Stop    3413.20
Target  3438.40
R:R     2.0

Why this fired
• Bullish Engulfing — buyers fully reversed the prior candle
• Reacted at prev day low + swing low x4 (3415.80), 0.18 ATR away
• Level respected 4x in recent history
• Session level (prior day/week extreme)
• With the 1h trend (up)
• Aligned with daily bias (up)
• RSI 38 — stretched into support
• Closed in the upper third of its range
• Reaction inside an unmitigated bullish FVG (4495.6–4512.5)
• Tapped a bullish order block (4506.6–4510.8)
• Entry tapping the 4H bullish FVG (4493.4–4520.3)
• Priced in 4H discount (eq 4542.1)
• 4H structure is up — entry with HTF flow
• Draw on liquidity: unswept 4H high at 4573.7
```

---

## The model it trades

**Patterns** (last closed bar only — never a forming candle)

| Pattern | Bars | Base strength |
|---|---|---|
| Bullish / Bearish Engulfing | 2 | 30–36 |
| Pin Bar (hammer / shooting star) | 1 | 26–34 |
| Morning / Evening Star | 3 | 32 |
| Tweezer Top / Bottom | 2 | 22 |
| Inside Bar Breakout / Breakdown | 3 | 22 |

**Levels** — built fresh per symbol/timeframe, then clustered into zones (ATR-sized) and
weighted by how many times price actually respected them:

- swing pivots (fractal highs/lows)
- prior day high / low (PDH, PDL)
- prior week high / low (PWH, PWL)
- psychological round numbers (per-instrument step: 50 pips FX, $10 gold, 100 pts NAS100…)

**Confluence filters** that move the score:

| Factor | Effect |
|---|---|
| Distance from the level (in ATR) | up to +10 |
| Level touch count | up to +14 |
| Prior day/week level | +7 · round number +4 |
| With the signal-timeframe EMA trend | +10 (counter-trend −6) |
| Aligned with the daily bias | +10 |
| RSI stretched into the level | +7 |
| Closed in the top/bottom third of range | +5 |
| ≥2.5R to the next opposing level | +4 |

### HTF → LTF layer (the primary filter)

The higher timeframe decides *where*; the entry timeframe decides *when*. The bot builds
an HTF context per instrument and projects it down onto your entry candle.

| Entry TF | Governing HTF |
|---|---|
| 5m | 1h |
| 15m | 4h |
| 30m | 4h |
| 1h | 4h |
| 4h | 1d |

Override with `HTF_MAP=15m:1h,1h:1d` etc.

| HTF element | Effect | What it means |
|---|---|---|
| **Entry inside an HTF FVG** | +10 | your LTF candle is reacting in the 4H imbalance |
| **Entry inside an HTF order block** | +9 | LTF reaction tapping the 4H OB |
| **Premium / discount** | ±5 | longs wanted below HTF equilibrium, shorts above |
| **HTF structure aligned** | +5 | entry with 4H BOS/CHoCH flow |
| **Fighting HTF structure** | −8 | counter-HTF with no CHoCH yet |

Two behaviours that come free with it:

- **Stops respect the HTF POI** — a long tapping a 4H FVG gets its stop below the *zone*,
  not just below the candle wick, so you don't get wicked out inside your own POI.
- **Draw on liquidity targeting** — the target defaults to the next **unswept HTF swing
  high/low** rather than a flat 2R (`HTF_TARGET_LIQUIDITY`). In the last replay 27 of 40
  top setups targeted a 4H liquidity pool, the rest fell back to a local level or 2R.

**`HTF_REQUIRED=true` is the default**, so a setup is only alerted when the entry is
actually inside an HTF FVG or order block — that's the HTF→LTF model in one switch. Set
it to `false` to also receive good candle-at-level setups outside an HTF POI (raise
`MIN_SCORE` to ~85 if you do, to keep the same alert volume).

### SMC layer (bonus only, capped at +12)

Smart-money context **never creates a signal** — it re-ranks candle-at-level setups that
already qualified, so a pin bar into an unmitigated FVG after a liquidity sweep outranks
the same pin bar in no-man's land.

| SMC element | Effect | How it's detected |
|---|---|---|
| **Fair Value Gap** | +6 | 3-candle imbalance, still unmitigated, reaction sitting inside it |
| **Order Block** | +5 | last opposing candle before a displacement leg that broke a swing |
| **Liquidity Sweep** | +6 | wick raided a prior swing extreme, then closed back inside |
| **BOS / CHoCH aligned** | +3 (CHoCH reversal +1.8) | structure walk over confirmed swings |
| **Displacement** | +2 | signal candle body ≥1.2× the 20-bar average range |
| **Against unbroken structure** | **−6** | trading into opposing structure with no CHoCH yet |

Alerts carry an `🧠 SMC:` badge (`FVG · Sweep · BOS`) so you can see the backdrop at a
glance. Turn the whole layer off with `SMC_ENABLED=false`, or flip `SMC_REQUIRED=true`
to only get setups an FVG / OB / sweep / structure break is backing.

A setup is only sent when **score ≥ MIN_SCORE (default 82)** and **R:R ≥ MIN_RR (1.8)**.

Scores use a **soft-knee normalisation**: raw points pass through unchanged up to 85, then
the theoretical maximum (135) is compressed into the last 15 points. Nothing clips at 100,
so ranking stays meaningful all the way up — a 95 really is rarer than an 88.

The threshold has been recalibrated at each step (72 → 78 → 82) against a 100-bar replay so
that **alert volume stayed flat while the quality bar rose**; the extra layers re-rank
setups rather than multiply them.
Stops go beyond the pattern's wick plus a 0.25×ATR buffer; targets are the next opposing
level, falling back to a flat 2R.

**Noise control:** one alert per symbol/timeframe/closed bar, a `COOLDOWN_BARS` gap (6)
before the same symbol/timeframe can fire again, and `MAX_ALERTS_PER_SCAN` (6) per cycle.

---

## Watchlist

`EURUSD · GBPUSD · USDJPY · AUDUSD · USDCAD · USDCHF · NZDUSD · EURJPY · XAUUSD · XAGUSD · NAS100 · US30 · SPX500`
on **15m, 1h, 4h** (daily is pulled separately for bias). Edit `WATCHLIST` in
`app/config.py`, or trim it with the `WATCHLIST` env var.

---

## Data source

TradingView has **no public API** and scraping it gets keys banned, so the bot computes
patterns itself from Yahoo Finance candles via `yfinance` — free, no API key, and it
covers all three asset classes (FX spot, `GC=F`/`SI=F` metals futures, `NQ=F`/`YM=F`/`ES=F`
index futures). Every alert links straight to the matching **TradingView chart**.

Swap providers any time: set `DATA_PROVIDER=twelvedata` + `TWELVEDATA_API_KEY`
(free tier = 800 requests/day, 8/min — enough for a trimmed watchlist).

> Prices are index/futures based, so they'll differ slightly from your broker's CFD feed.
> Levels and patterns are effectively identical.

---

## Quick start

```bash
cd pattern-bot
pip install -r requirements.txt

# 1. dry run — no token needed, prints setups and writes scan_report.md
python -m app.demo --tf 1h,4h

# 2. replay recent history to see what would have fired
#    (HTF context is truncated per bar, so replays don't peek at unclosed HTF candles)
python -m app.demo --history 100 --tf 15m,1h

# 3. connect Telegram
cp .env.example .env      # paste your token from @BotFather
python -m app.check       # verifies token, finds your chat, sends a test alert

# 4. run the bot
python -m app.main
```

Then message your bot **/start** in Telegram — that subscribes your chat.

**Full walkthrough (BotFather → local test → 24/7 hosting → troubleshooting):
see [DEPLOY.md](DEPLOY.md).**

---

## Telegram commands

| Command | Does |
|---|---|
| `/start` | Subscribe this chat to alerts |
| `/scan` | Force a scan right now |
| `/status` | Settings, scan count, alert count |
| `/watchlist` | Symbols + timeframes |
| `/last` | Recent alerts |
| `/score 78` | Change the alert threshold on the fly |
| `/mute` `/unmute` | Pause / resume |
| `/help` | Model explanation |

---

## Deploy free

**Railway** (simplest, always-on)
1. Push this folder to GitHub.
2. railway.app → New Project → Deploy from GitHub repo.
3. Variables → add `TELEGRAM_BOT_TOKEN` (and `STATE_FILE=/tmp/state.json`).
4. Done — `railway.json` sets the start command.

**Render** (`render.yaml` included)
- New → Blueprint → point at the repo → add `TELEGRAM_BOT_TOKEN` in the dashboard.
- The bot serves a health endpoint on `$PORT`, so it runs as a **free web service**.
  Free instances sleep after ~15 min idle — add a free UptimeRobot ping to the service
  URL every 10 min to keep it awake, or use a paid background worker.

**Fly.io / any Docker host** — `Dockerfile` included: `fly launch` then
`fly secrets set TELEGRAM_BOT_TOKEN=...`.

> `state.json` holds subscribed chats and dedupe keys. On ephemeral filesystems set
> `STATE_FILE=/tmp/state.json` and just re-send `/start` after a redeploy, or mount a volume.

---

## Tuning

Everything is env-driven (see `.env.example`):

| Var | Default | Meaning |
|---|---|---|
| `MIN_SCORE` | 82 | Alert threshold. 78 = chattier, 88 = only A+ |
| `MIN_RR` | 1.8 | Discard setups with worse reward:risk |
| `TIMEFRAMES` | 15m,1h,4h | What to scan |
| `SCAN_INTERVAL_SECONDS` | 60 | Loop speed |
| `COOLDOWN_BARS` | 6 | Bars before the same symbol/tf can alert again |
| `REQUIRE_HTF_BIAS` | false | Hard-filter counter-daily-bias setups |
| `PROXIMITY_ATR` | 0.55 | How close to a level the wick must react |
| `LEVEL_ATR_TOLERANCE` | 0.40 | Zone half-width when clustering levels |
| `SESSION_START_UTC` / `SESSION_END_UTC` | 0 / 24 | e.g. `7`/`16` for London+NY only |
| `SMC_ENABLED` | true | Turn the smart-money layer on/off |
| `SMC_REQUIRED` | false | Only alert when FVG / OB / sweep / BOS backs the setup |
| `SMC_MAX_BONUS` | 12 | Cap on the total SMC contribution |
| `SMC_FVG_BONUS` / `SMC_OB_BONUS` / `SMC_SWEEP_BONUS` | 6 / 5 / 6 | Individual weights |
| `SMC_CONTRA_PENALTY` | 6 | Docked for trading into unbroken opposing structure |
| `HTF_ENABLED` | true | Turn the HTF→LTF layer on/off |
| `HTF_REQUIRED` | **true** | Only alert when the entry taps an HTF FVG / order block |
| `HTF_MAP` | 5m:1h,15m:4h,30m:4h,1h:4h,4h:1d | Entry TF → governing TF |
| `HTF_FVG_BONUS` / `HTF_OB_BONUS` | 10 / 9 | Weight for entries inside HTF POIs |
| `HTF_PD_BONUS` | 5 | Premium/discount reward (and penalty for chasing) |
| `HTF_CONTRA_PENALTY` | 8 | Docked for fighting HTF structure |
| `HTF_TARGET_LIQUIDITY` | true | Target unswept HTF liquidity instead of flat 2R |
| `HTF_TARGET_MAX_RR` | 6 | Ignore liquidity pools further than this |

**Tighten your model** by editing `app/patterns.py` (candle detectors), `app/smc.py`
(smart-money definitions), `app/htf.py` (HTF pairing and POI logic) and the scoring block
in `app/signals.py` — that's where your rules live.

Tests: `python -m pytest tests -q` (43 tests — every candle detector, FVG mitigation, sweeps, order blocks, structure flips, HTF POI projection with wick tolerance, premium/discount, liquidity targeting and score normalisation).

---

## Layout

```
app/
  config.py      watchlist, thresholds, env handling
  data.py        candle fetching, 4h resampling, closed-bar filter, caching
  indicators.py  EMA / ATR / RSI / trend state
  levels.py      pivots, PDH/PDL, PWH/PWL, round numbers, clustering, touch counts
  patterns.py    the five candlestick detectors
  smc.py         FVGs, order blocks, liquidity sweeps, BOS/CHoCH, displacement
  htf.py         HTF context: POI projection, premium/discount, draw on liquidity
  signals.py     the model: pattern + level + context -> scored Signal
  formatting.py  Telegram + markdown rendering
  telegram.py    Bot API client (send, long-poll, commands menu)
  state.py       chats, dedupe, cooldown, stats
  scanner.py     scan loop + command handler
  main.py        entrypoint (scanner + commands + health endpoint)
  demo.py        offline scan / historical replay
  check.py       Telegram connection checker (token, chat, test alert)
tests/           unit tests
```

---

*Alerts are technical signals, not financial advice. Backtest and forward-test on demo
before risking capital.*

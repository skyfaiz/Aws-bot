# Live Paper Trading Bot — Implementation Notes

Implements `LIVE_BOT_ARCHITECTURE.md`: MCX CRUDEOIL options paper trader (HalfTrend on the nearest FUT, buys ATM CE/PE). No real orders are ever placed.

## Market hours (as requested)

| Item | Value (IST, Mon–Fri) |
|---|---|
| Market open / candle start | **09:15** |
| Entries allowed | 09:20 – 23:10 |
| Auto square-off | 23:15 |
| Market close | **23:30** |

Sat/Sun are skipped. There is no holiday calendar (the bot just polls and gets stale data on a holiday). The architecture doc said 09:00; `config.yaml` now uses 09:15. Candles are aligned to the session open (09:15, 09:20, ...).

## Layout

```
bot.py                     orchestrator (Bot.step(now) = one poll cycle; Bot.run() = loop)
config/config.yaml         all parameters
core/config.py             YAML load, ${ENV} expansion, .env loader, validation
core/session.py            market hours / entry window / square-off checks
core/instrument_loader.py  filters instrument.csv (MCX, CRUDEOIL only, FUT/CE/PE, buy+sell allowed, not expired)
core/groww_client.py       GET /live-data/ltp with retries, 401 -> GrowwAuthError
core/candle_builder.py     ticks -> OHLCV, gap fill (same day), disk persistence
core/option_selector.py    ATM/OTM/ITM strike + expiry selection with fallback
core/paper_trader.py       open/close, SL/TP modes, P&L, position (de)serialisation
core/trade_log.py          logs/trades_YYYY-MM-DD.csv, metrics, summary
strategies/                BaseStrategy, Signal, registry, HalfTrendStrategy (wraps existing halftrend.py)
deploy/                    tradingbot.service, setup_ec2.sh, tradingbot.logrotate
telegram_service.py        Telegram front-end; runs the bot in a background thread
core/data_store.py         tick/candle/signal recorder with retention
core/telegram_control.py   command logic (auth, status, set, settoken, pause...)
core/config_edit.py        comment-preserving edits of config.yaml
tests/                     35 pytest tests
```

`halftrend.py` is unchanged.

## Behaviour and decisions beyond the architecture doc

- **Reversal**: strategy returns BUY/SELL on every HalfTrend flip. If an opposite position is open the bot closes it (`signal_exit`) and opens the new side; same-direction signals are ignored.
- **Exit priority per tick**: stop loss, take profit, then square-off (23:15), then market close (23:30). Signal exits happen on candle close.
- **No entries** before 09:20, from 23:10, or after square-off.
- **Crash safety**: open position is saved to `logs/state.json`; on restart it is restored. A position found from a previous day is closed at the first price of the new session (`market_close`).
- **Warm-up persistence**: sealed candles are saved to `logs/candles.csv` and reloaded (if newer than 72h). Without this, `warmup_candles: 120` on 5-min candles means ~10 hours of waiting after every cold start. Flat gap-filling only happens within a day, never across overnight gaps.
- **Expiry choice**: options use the first expiry at least `min_days_to_expiry` (default 1) days away, so the bot avoids expiry-day options. Futures use the nearest expiry on/after today. Instruments are reloaded when the date changes.
- **Auth failure**: HTTP 401/403 logs CRITICAL each poll; the bot keeps running so a token renewal plus restart resumes it.
- **Stall warning**: WARNING if no LTP for `stall_warning_seconds` (60) during market hours.
- **ATR SL/TP**: uses `risk.assumed_delta` (0.5) to convert underlying ATR into an option-price distance.
- Not implemented (from "future enhancements"): Flask health endpoint, holiday calendar, token auto-refresh, alerts.

## Telegram service

Run `python telegram_service.py` (or the `tradingbot-telegram` systemd unit) instead of `bot.py`; it runs the same bot plus Telegram control. Needs `TELEGRAM_BOT_TOKEN` (from @BotFather) in `.env`. Only one of the two services should run at a time (the unit files conflict on purpose).

**Access**: send `/login 679902` (override with `TELEGRAM_PASSWORD` in `.env`) in a private chat. The login message is deleted, authorised chats are saved to `logs/telegram_auth.json`, and 5 wrong attempts lock that user out for 15 minutes. Unauthorised users get nothing but the login prompt.

**Automatic messages**: entries (with SL/TP), exits (reason, trade and session P&L), session summary at 23:30, API auth failure / recovery, tick stalls, loop errors, service start. Set `telegram.notify_signals: true` for a message on every candle close.

| Command | Action |
|---|---|
| `/status` | market, pause state, API health, price, candles, position, today's P&L |
| `/position`, `/pnl`, `/trades [n]` | open position, P&L and metrics, last trades |
| `/config [section]` | list settings (token masked) |
| `/set <key> <value>` | change any setting in `config.yaml`, e.g. `/set risk.stop_loss.value 25`, `/set instrument.lots 2` |
| `/settoken <token>` | update the Groww token (checked against Groww first, message deleted) |
| `/pause`, `/resume` | stop / allow new entries (persists across restarts) |
| `/squareoff` | close the open position on the next tick |
| `/logout` | remove this chat |

Notes:
- `/set` keeps the file's comments and layout, validates the whole config, applies it to the running bot immediately, and rejects bad values without touching the file. Changing `candles.interval_minutes` clears the stored candles. SL/TP changes apply to new trades only. `paths.*` cannot be changed remotely.
- **Token storage**: `/settoken` writes the token to `.env` (git-ignored), not into `config.yaml`, which is committed to git; `config.yaml` keeps `access_token: "${GROWW_ACCESS_TOKEN}"`, so the effect is the same without leaking the token to the repo.
- While paused the bot still manages an open position (SL/TP, opposite signal, square-off) but opens nothing new.
- Default `candles.warmup_candles` is now 5 (was 120); the other defaults are unchanged. HalfTrend's ATR smooths over 100 candles, so signals in the first hours are less reliable.
- The service itself could not be run against Telegram here (no bot token): the command logic is covered by tests, and a start-up with a dummy token reaches Telegram and is rejected as expected.

## Position sizing (lots)

Before this change the bot always bought `instrument.lots` (fixed, default 1). Now:

```
equity     = paper.initial_capital_inr + all realised P&L (every logs/trades_*.csv)
limit      = equity * risk.max_capital_pct / 100            (default 50)
cost/lot   = option entry price * lot size (100)
lots       = min(instrument.lots, largest n with n * cost/lot  <  limit)
```

- Trade value is always strictly below 50% of current equity (exactly 50% is rejected).
- `instrument.lots` is the number you want; the cap only ever reduces it. Set it high (e.g. 100) to always trade the maximum the cap allows.
- If even one lot is over the limit the entry is skipped and a message is logged/sent to Telegram.
- Equity includes realised profit and loss, so the lot count shrinks after losses and grows after wins. Both settings can be changed from Telegram (`/set risk.max_capital_pct 40`, `/set instrument.lots 3`).
- Example at Rs 1,00,000 capital and an option at Rs 200 (Rs 20,000 per lot): at most 2 lots (Rs 40,000). At Rs 250 (Rs 25,000 per lot): 1 lot, because 2 lots would be exactly 50%.

## Historical data recorder

Everything the bot sees is appended to daily CSVs in `data/` (git-ignored):

| File | Content |
|---|---|
| `ticks_YYYY-MM-DD.csv` | every LTP poll: timestamp, symbol, price (underlying, plus the option while a position is open) |
| `candles_YYYY-MM-DD.csv` | every sealed candle (including flat-filled gap candles with 0 ticks) |
| `signals_YYYY-MM-DD.csv` | each strategy evaluation: signal, candle close, ATR, paused flag, open position |

Files older than `data.retention_days` (default 30, minimum 7 enforced) are deleted at the start of each day; a file exactly 7 days old is always kept. Trade logs (`logs/trades_*.csv`) are never deleted. Disk errors are logged and never stop trading. Roughly 10k tick rows per symbol per day. Disable with `data.enabled: false`.

## When does HalfTrend start calculating?

- The indicator code (`halftrend.py`) runs over the whole candle window from the first candle on every evaluation; there is no separate start-up phase inside it.
- The bot only evaluates the strategy once `candles.warmup_candles` candles are sealed (default 5, so at 09:40 on a fresh start with 5-minute candles). It is evaluated on every sealed candle after that.
- Buy/Sell flags depend only on highs, lows and closes (the `amplitude` window and previous candle), not on ATR, so `atr_period` has no effect on signals. It only affects the channel bands and `atr` stop-loss/take-profit modes. (Checked: signals are identical for `atr_period` 1 and 100.)
- The indicator keeps a trend state that starts as "up" on the first candle it sees. A different start point can therefore give a different flag for the first few candles. On random-walk test data, runs started at different points agreed with the full-history run after at most ~13 candles. So signals in roughly the first 15 candles (about 75 minutes) after a cold start are less reliable than later ones; `candles.persist` keeps history across restarts and days, so this normally happens only on the very first run.
- Once more than `candles.max_window` (500) candles exist the oldest are dropped, which moves the start point in the same way.
- Earliest possible signal in the code is the 2nd candle; with the default warmup the earliest evaluated candle is the 5th.

## Testing

```
pip install -r requirements.txt
python -m pytest -q tests        # 35 passed
```

Coverage:
- Session boundaries (09:14 closed, 09:15 open, 23:29 open, 23:30 closed, weekends).
- Candle alignment, sealing, gap fill, no cross-day fill, window cap, persistence/staleness.
- Instrument filtering (mini contracts, blocked, expired); ATM/OTM/ITM, next expiry, fallback strikes.
- SL/TP in every mode, P&L, position serialisation.
- Trade log CSV, win rate, profit factor, drawdown, same-day restart.
- HalfTrend strategy signals; Groww client retries, 401, failures (mocked HTTP).
- Bot end-to-end with a fake client: entry, tick SL, TP, reversal, EXIT signal, 23:15 square-off, 23:30 close and summary, entry-time window, off-hours/weekend produce no API calls, API/auth errors, restart restore, next-day carry-over close, and a full-day run with the real HalfTrend.
- Also verified manually that `python bot.py` starts and reaches the real Groww API (a dummy token gives the expected 401 CRITICAL message).

**Instrument CSV**: the real `instrument.csv` loads correctly (1120 CRUDEOIL rows: 10 FUT, 555 CE, 555 PE). Its `groww_symbol` column uses a dashed format (`MCX-CRUDEOIL-19Oct26-FUT`), but the LTP API expects `MCX_CRUDEOIL19OCT26FUT`, so the loader builds the API symbol as `exchange + "_" + trading_symbol`.

**Not verified**: behaviour with a real access token (none available), so the API symbol format is taken from the Groww docs examples, not confirmed live.

## Deployment with Docker (recommended)

The image (`Dockerfile`, Python 3.11-slim, pinned `requirements.txt`) makes the runtime identical on your PC and on EC2. Timezone is IST inside the container. `docker-compose.yml` mounts `.env`, `config/`, `instrument.csv`, `logs/` and `data/` from the host, so settings changed with `/set`, the token set with `/settoken`, trade logs, recorded data and candles all survive rebuilds and restarts. The container runs as uid 1000 (same as `ubuntu` on EC2).

Local (any machine with Docker):
```bash
cp .env.example .env            # fill TELEGRAM_BOT_TOKEN, GROWW_ACCESS_TOKEN
mkdir -p logs data
docker compose run --rm tests   # 35 tests inside the image
docker compose up -d --build
docker compose logs -f
```

EC2 (Ubuntu 22.04, `ap-south-1`, t3.micro/small, SSH open only to your IP):
```bash
git clone https://github.com/skyfaiz/Aws-bot.git ~/tradingbot && cd ~/tradingbot
bash deploy/setup_docker.sh     # installs Docker, builds the image, runs the tests
nano .env                       # TELEGRAM_BOT_TOKEN=..., GROWW_ACCESS_TOKEN=... (token can also be sent later via /settoken)
sudo docker compose up -d       # starts now and on every reboot (restart: unless-stopped)
sudo docker compose logs -f
```
Then message the bot `/login 679902` and `/status`.

Operations:
- Update code: `git pull && sudo docker compose up -d --build`
- Stop / restart: `sudo docker compose stop` / `sudo docker compose restart` (a 60 s grace period lets the bot close its session cleanly)
- Pause trading without stopping: `/pause` in Telegram
- Refresh instruments: replace `instrument.csv`, commit, `git pull`, `sudo docker compose restart`
- Back up `logs/` and `data/` (trade logs, state, recorded data)
- Run only one of the Docker container or the systemd units, never both.

## Deployment without Docker (systemd, alternative)

1. Launch a `t3.micro`/`t3.small`, open only SSH (22) from your IP.
2. Copy the project to `/home/ubuntu/tradingbot` (git clone or `scp -r`). The service file assumes this path and user `ubuntu`. Put `instrument.csv` there too.
3. Run the setup script (installs Python/venv/deps, sets timezone to IST, runs tests, installs the service and logrotate):
   ```bash
   cd /home/ubuntu/tradingbot
   bash deploy/setup_ec2.sh
   ```
4. Put your token in `.env` (never commit it):
   ```bash
   nano .env      # GROWW_ACCESS_TOKEN=...
   ```
5. Start and watch:
   ```bash
   sudo systemctl start tradingbot
   sudo systemctl status tradingbot
   tail -f logs/bot.log
   ```
6. Trades land in `logs/trades_YYYY-MM-DD.csv`; the session summary is printed in `bot.log` at 23:30 and on `systemctl stop`.

Operations:
- Restart after config change: `sudo systemctl restart tradingbot`.
- Refresh `instrument.csv` regularly (new expiries are listed monthly); the bot reads it at startup and at each date change.
- When the Groww token expires, `bot.log` shows `AUTH FAILURE`; update `.env` and restart.
- Run locally first: `pip install -r requirements.txt`, create `.env`, `python bot.py`.

Before leaving it unattended, run it for a day and sanity-check that FUT/option LTPs are returned for the symbols in your `instrument.csv`.

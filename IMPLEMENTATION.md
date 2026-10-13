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
tests/                     19 pytest tests
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

## Testing

```
pip install -r requirements.txt
python -m pytest -q tests        # 19 passed
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

## Deployment (AWS EC2, Ubuntu 22.04, ap-south-1)

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

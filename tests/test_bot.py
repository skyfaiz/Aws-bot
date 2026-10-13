from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from bot import Bot
from core.groww_client import GrowwAPIError, GrowwAuthError
from strategies import Signal

IST = ZoneInfo("Asia/Kolkata")
FUT = "MCX_CRUDEOIL19OCT26FUT"


def at(d, hh, mm, ss=0):
    return datetime(2026, 10, d, hh, mm, ss, tzinfo=IST)


class FakeClient:
    def __init__(self):
        self.prices = {FUT: 6523.0}
        self.default_opt = 200.0
        self.error = None
        self.calls = []

    def get_ltp(self, symbols, segment="COMMODITY"):
        self.calls.append(list(symbols))
        if self.error:
            raise self.error
        return {s: self.prices.get(s, self.default_opt if s != FUT else 6523.0) for s in symbols}


class Script:
    """Strategy stub: returns queued signals, else HOLD."""

    def __init__(self, bot):
        self.q = []
        bot.strategy.generate_signal = lambda candles: self.q.pop(0) if self.q else Signal.HOLD
        bot.strategy.last_atr = 5.0


def warm(bot, d=13):
    """Feed ticks every 5 min from 09:15 until warm. Returns time of last tick."""
    t = at(d, 9, 15)
    while not bot.builder.is_warm():
        bot.step(t)
        t += timedelta(minutes=5)
    return t


def mk(cfg, base_dir):
    c = FakeClient()
    b = Bot(cfg, client=c, base_dir=base_dir)
    return b, c, Script(b)


def trades(bot):
    return pd.read_csv(bot.trade_log.path) if bot.trade_log.path.exists() else pd.DataFrame()


def test_full_day_flow(cfg, base_dir):
    b, c, s = mk(cfg, base_dir)
    t = warm(b)                       # 5 candles sealed at 09:40 tick
    assert not b.trader.has_open_position()
    s.q = [Signal.BUY]
    b.step(t)                         # 09:40+5 -> candle seals -> BUY
    pos = b.trader.position
    assert pos and pos.contract.option_type == "CE" and pos.contract.strike == 6500
    assert pos.entry_option_ltp == 200 and pos.sl_price == pytest.approx(140) and pos.tp_price == pytest.approx(320)

    # tick-level SL: no exit at 150, exit at 139
    c.prices[pos.contract.groww_symbol] = 150
    b.step(t + timedelta(seconds=10))
    assert b.trader.has_open_position()
    c.prices[pos.contract.groww_symbol] = 139
    b.step(t + timedelta(seconds=20))
    assert not b.trader.has_open_position()
    df = trades(b)
    assert df.iloc[-1]["exit_reason"] == "stop_loss" and df.iloc[-1]["net_pnl_inr"] == pytest.approx(-6100)

    # SELL -> PE; then BUY reverses (closes PE, opens CE)
    c.prices.clear(); c.prices[FUT] = 6523.0
    t += timedelta(minutes=5); s.q = [Signal.SELL]
    b.step(t)
    assert b.trader.position.contract.option_type == "PE"
    pe = b.trader.position.contract.groww_symbol
    c.prices[pe] = 210
    t += timedelta(minutes=5); s.q = [Signal.SELL]
    b.step(t)                         # same direction -> ignored
    assert b.trader.position.contract.option_type == "PE"
    t += timedelta(minutes=5); s.q = [Signal.BUY]
    b.step(t)
    assert b.trader.position.contract.option_type == "CE"
    assert trades(b).iloc[-1]["exit_reason"] == "signal_exit"

    # TP
    ce = b.trader.position.contract.groww_symbol
    c.prices[ce] = 400
    b.step(t + timedelta(seconds=5))
    assert trades(b).iloc[-1]["exit_reason"] == "take_profit"

    # EXIT signal
    c.prices.clear(); c.prices[FUT] = 6523.0
    t += timedelta(minutes=5); s.q = [Signal.SELL]
    b.step(t)
    assert b.trader.has_open_position()
    t += timedelta(minutes=5); s.q = [Signal.EXIT]
    b.step(t)
    assert not b.trader.has_open_position()
    assert trades(b).iloc[-1]["exit_reason"] == "signal_exit"

    # entry open again, then auto squareoff at 23:15
    t += timedelta(minutes=5); s.q = [Signal.BUY]
    b.step(t)
    assert b.trader.has_open_position()
    b.step(at(13, 23, 14, 59))
    assert b.trader.has_open_position()
    b.step(at(13, 23, 15))
    assert not b.trader.has_open_position()
    assert trades(b).iloc[-1]["exit_reason"] == "squareoff"
    assert b.state_path.read_text().find('"position": null') >= 0

    # market close -> session summary, candles persisted, no more API calls afterwards
    b.step(at(13, 23, 30))
    assert not b.session_active and b.candle_path.exists()
    n = len(c.calls)
    b.step(at(13, 23, 40))
    assert len(c.calls) == n


def fresh(cfg, base_dir, name):
    from tests.conftest import make_instruments
    d = base_dir / name
    d.mkdir()
    make_instruments(d / "instrument.csv")
    b, c, s = mk(cfg, d)
    b.builder.warmup = 1
    return b, c, s


def test_entry_time_window(cfg, base_dir):
    # 09:15 candle sealed by the 09:20 tick -> entry allowed at exactly 09:20
    b, c, s = fresh(cfg, base_dir, "a")
    b.step(at(13, 9, 15)); s.q = [Signal.BUY]
    b.step(at(13, 9, 19, 59))
    assert not b.trader.has_open_position()
    b.step(at(13, 9, 20))
    assert b.trader.has_open_position()

    # signal sealed before 09:20 trade window start is ignored (10-min candles not needed: use 09:15 candle, tick 09:19:59 never seals)
    # signal on or after 23:10 must not enter
    b3, c3, s3 = fresh(cfg, base_dir, "c")
    b3.step(at(13, 23, 5)); s3.q = [Signal.BUY]
    b3.step(at(13, 23, 10))
    assert not b3.trader.has_open_position()


def test_weekend_and_off_hours_make_no_calls(cfg, base_dir):
    b, c, _ = mk(cfg, base_dir)
    for t in (at(17, 12, 0), at(18, 12, 0), at(13, 9, 14), at(13, 23, 30), at(13, 3, 0)):
        b.step(t)
    assert c.calls == []


def test_api_errors_do_not_crash(cfg, base_dir, caplog):
    b, c, _ = mk(cfg, base_dir)
    b.step(at(13, 9, 15))
    c.error = GrowwAPIError("boom")
    b.step(at(13, 9, 16)); b.step(at(13, 9, 17))
    assert any("STALL" in r.message for r in caplog.records)
    c.error = GrowwAuthError("expired")
    b.step(at(13, 9, 18))
    assert any(r.levelname == "CRITICAL" for r in caplog.records)
    c.error = None
    b.step(at(13, 9, 19))
    assert b.tick_count == 2


def test_market_close_closes_open_position_and_restart_restores(cfg, base_dir):
    b, c, s = mk(cfg, base_dir)
    t = warm(b)
    s.q = [Signal.BUY]
    b.step(t)
    assert b.trader.has_open_position()
    sym = b.trader.position.contract.groww_symbol

    # crash/restart: new Bot restores the open position from disk
    b2 = Bot(cfg, client=c, base_dir=base_dir)
    Script(b2)
    assert b2.trader.position and b2.trader.position.contract.groww_symbol == sym
    b2.step(t + timedelta(seconds=30))
    assert c.calls[-1] == [FUT, sym]

    # position left from previous day is closed on the first tick of next day
    c.prices[sym] = 230
    b3 = Bot(cfg, client=c, base_dir=base_dir)
    Script(b3)
    b3.step(at(14, 9, 15, 5))
    assert not b3.trader.has_open_position()
    df = pd.read_csv(base_dir / "logs" / "trades_2026-10-14.csv")
    assert df.iloc[-1]["exit_reason"] == "market_close" and df.iloc[-1]["net_pnl_inr"] == pytest.approx(3000)


def test_candles_persist_across_restart(cfg, base_dir):
    b, c, s = mk(cfg, base_dir)
    warm(b)
    n = len(b.builder)
    b.builder.save(b.candle_path)
    b2 = Bot(cfg, client=c, base_dir=base_dir)
    b2.step(at(14, 9, 15))
    assert len(b2.builder) == n and b2.builder.is_warm()


def test_real_strategy_smoke(cfg, base_dir):
    """Run the real HalfTrend through a synthetic zigzag day; bot must never crash and must log consistent trades."""
    import math
    c = FakeClient()
    b = Bot(cfg, client=c, base_dir=base_dir)
    t = at(13, 9, 15)
    while t < at(13, 23, 31):
        px = 6500 + 60 * math.sin((t - at(13, 9, 15)).total_seconds() / 3600 * 1.3)
        c.prices[FUT] = px
        c.default_opt = 150 + (px - 6500) * 0.5
        if b.trader.has_open_position():
            sign = 1 if b.trader.position.contract.option_type == "CE" else -1
            c.prices[b.trader.position.contract.groww_symbol] = 150 + sign * (px - 6500) * 0.5
        b.step(t)
        t += timedelta(minutes=1)
    assert not b.trader.has_open_position()
    df = trades(b)
    assert len(df) >= 1
    assert (df["exit_time"] > df["entry_time"]).all()
    assert df["cumulative_pnl_inr"].iloc[-1] == pytest.approx(df["net_pnl_inr"].sum())

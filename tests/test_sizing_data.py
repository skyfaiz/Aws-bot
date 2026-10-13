from datetime import date, timedelta

import pandas as pd
import pytest

from bot import Bot
from core.data_store import DataStore
from strategies import Signal
from tests.test_bot import FUT, FakeClient, Script, at, warm


def mk(cfg, base_dir, lots=1, capital=100000.0, price=200.0):
    cfg["instrument"]["lots"] = lots
    cfg["paper"]["initial_capital_inr"] = capital
    c = FakeClient()
    c.default_opt = price
    b = Bot(cfg, client=c, base_dir=base_dir)
    s = Script(b)
    sent = []
    b.notify = sent.append
    return b, c, s, sent


def enter(b, s):
    t = warm(b)
    s.q = [Signal.BUY]
    b.step(t)
    return t


def test_size_lots_rules(cfg, base_dir):
    b, *_ = mk(cfg, base_dir, lots=10)
    assert b.size_lots(200, 100)[0] == 2          # 3 lots = 60k > 50k limit; 2 lots = 40k
    assert b.size_lots(250, 100)[0] == 1          # 2 lots = 50k == limit -> must be strictly below
    assert b.size_lots(500, 100)[0] == 0          # 1 lot = 50k == limit -> not allowed
    assert b.size_lots(600, 100)[0] == 0
    b.cfg["instrument"]["lots"] = 1
    assert b.size_lots(10, 100)[0] == 1           # configured lots is the upper bound
    b.cfg["risk"]["max_capital_pct"] = 90.0
    b.cfg["instrument"]["lots"] = 50
    assert b.size_lots(200, 100)[0] == 4          # 80k < 90k, 5 lots = 100k


def test_entry_uses_capped_lots_and_reports(cfg, base_dir):
    b, c, s, sent = mk(cfg, base_dir, lots=10)
    enter(b, s)
    p = b.trader.position
    assert p.lots == 2
    assert p.lots * p.entry_option_ltp * p.contract.lot_size < 0.5 * 100000
    assert any("2 lot(s) = Rs 40,000.00 (40.0% of capital)" in m for m in sent)
    # pnl uses the sized lots
    c.prices[p.contract.groww_symbol] = 139
    b.step(at(13, 9, 55))
    assert not b.trader.has_open_position()
    assert pd.read_csv(b.trade_log.path).iloc[-1]["net_pnl_inr"] == pytest.approx((139 - 200) * 2 * 100)


def test_equity_includes_realised_pnl_and_blocks_when_too_small(cfg, base_dir):
    b, c, s, sent = mk(cfg, base_dir, lots=10)
    t = enter(b, s)                                   # 2 lots @200
    c.prices[b.trader.position.contract.groww_symbol] = 100
    b.step(t + timedelta(seconds=5))                  # stop loss at 140 -> exit @100: -20,000
    assert b.size_lots(200, 100)[0] == 1              # equity 80k -> limit 40k -> 1 lot (2 lots = 40k not below)
    cfg["paper"]["initial_capital_inr"] = 15000       # tiny account: even one lot is too big
    b.cfg = cfg
    c.prices.clear(); c.prices[FUT] = 6523.0
    s.q = [Signal.BUY]
    b.step(t + timedelta(minutes=5))
    assert not b.trader.has_open_position()
    assert any("Entry skipped" in m for m in sent)


def test_data_store_records_and_prunes(cfg, base_dir):
    b, c, s, sent = mk(cfg, base_dir, lots=1)
    t = enter(b, s)
    d = base_dir / "data"
    ticks = pd.read_csv(d / "ticks_2026-10-13.csv")
    assert set(ticks["symbol"]) >= {FUT} and len(ticks) >= 6
    assert (ticks["ltp"] > 0).all()
    candles = pd.read_csv(d / "candles_2026-10-13.csv")
    assert len(candles) >= 5 and {"open", "high", "low", "close"} <= set(candles.columns)
    sig = pd.read_csv(d / "signals_2026-10-13.csv")
    assert sig.iloc[-1]["signal"] == "BUY"
    # option ticks are stored while a position is open
    c.prices[b.trader.position.contract.groww_symbol] = 205
    b.step(t + timedelta(seconds=5))
    assert any("CE" in s for s in pd.read_csv(d / "ticks_2026-10-13.csv")["symbol"])
    # pruning: files older than retention are removed, newer kept
    st = DataStore(d, 7)
    for day in (date(2026, 10, 1), date(2026, 10, 7), date(2026, 10, 8)):
        (d / f"ticks_{day}.csv").write_text("x")
    assert st.prune(date(2026, 10, 14)) == 1          # cutoff Oct 7: only Oct 1 goes; exactly-7-days-old is kept
    assert (d / "ticks_2026-10-07.csv").exists() and not (d / "ticks_2026-10-01.csv").exists()
    assert (d / "ticks_2026-10-08.csv").exists() and (d / "ticks_2026-10-13.csv").exists()
    # retention can never go below a week
    assert DataStore(d, 1).retention_days == 7
    # disabled store writes nothing
    off = DataStore(base_dir / "off", 30, enabled=False)
    off.record_ticks(at(13, 9, 15), {"A": 1.0})
    assert not (base_dir / "off").exists()

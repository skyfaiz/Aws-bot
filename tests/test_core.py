from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
import requests

from core.candle_builder import CandleBuilder
from core.config import validate
from core.groww_client import GrowwAPIError, GrowwAuthError, GrowwClient
from core.instrument_loader import InstrumentLoader
from core.option_selector import OptionSelector
from core.paper_trader import ExitEvent, PaperTrader
from core.session import Session
from core.trade_log import TradeLog
from strategies import Signal, load_strategy

IST = ZoneInfo("Asia/Kolkata")


def dt(d, hh, mm, ss=0):
    return datetime(2026, 10, d, hh, mm, ss, tzinfo=IST)


# ── Session ───────────────────────────────────────────────────────
def test_session_hours(cfg):
    s = Session(cfg["session"])
    assert not s.is_market_open(dt(13, 9, 14, 59))   # Tue before open
    assert s.is_market_open(dt(13, 9, 15))
    assert s.is_market_open(dt(13, 23, 29, 59))
    assert not s.is_market_open(dt(13, 23, 30))
    assert not s.is_market_open(dt(17, 12, 0))       # Saturday
    assert not s.is_market_open(dt(18, 12, 0))       # Sunday
    assert s.is_market_open(dt(16, 12, 0))           # Friday
    assert not s.can_enter(dt(13, 9, 16)) and s.can_enter(dt(13, 9, 20))
    assert not s.can_enter(dt(13, 23, 10))
    assert s.is_squareoff_time(dt(13, 23, 15)) and not s.is_squareoff_time(dt(13, 23, 14))


# ── Candle builder ────────────────────────────────────────────────
def test_candle_aligned_and_sealed(cfg):
    b = CandleBuilder(5, 100, 2, Session(cfg["session"]).open)
    assert b.add_tick(100, dt(13, 9, 15, 5)) == []
    assert b.add_tick(105, dt(13, 9, 17)) == []
    assert b.add_tick(98, dt(13, 9, 19, 58)) == []
    sealed = b.add_tick(101, dt(13, 9, 20, 2))
    assert len(sealed) == 1
    c = sealed[0]
    assert (c.start.minute, c.open, c.high, c.low, c.close, c.volume) == (15, 100, 105, 98, 98, 3)
    assert len(b) == 1 and not b.is_warm()


def test_candle_gap_fill_same_day_but_not_across_days(cfg):
    b = CandleBuilder(5, 100, 2, Session(cfg["session"]).open)
    b.add_tick(100, dt(13, 10, 0))
    sealed = b.add_tick(110, dt(13, 10, 17))  # 10:00 real, 10:05 & 10:10 filled
    assert [c.start.minute for c in sealed] == [0, 5, 10]
    assert sealed[1].open == sealed[1].close == 100 and sealed[1].volume == 0
    b.seal_current()
    n = len(b)
    b.add_tick(120, dt(14, 9, 15))
    assert len(b) == n  # no fill between days
    assert b.add_tick(121, dt(14, 9, 20))[0].start == dt(14, 9, 15)


def test_candle_window_cap_and_persist(cfg, tmp_path):
    b = CandleBuilder(5, 3, 2, Session(cfg["session"]).open)
    for i in range(8):
        b.add_tick(100 + i, dt(13, 9, 15) + timedelta(minutes=5 * i))
    assert len(b) == 3 and b.is_warm()
    b.save(tmp_path / "c.csv")
    b2 = CandleBuilder(5, 3, 2, Session(cfg["session"]).open)
    assert b2.load(tmp_path / "c.csv", dt(13, 23, 0), 72) == 3
    b3 = CandleBuilder(5, 3, 2, Session(cfg["session"]).open)
    assert b3.load(tmp_path / "c.csv", dt(13, 23, 0) + timedelta(days=10), 72) == 0


# ── Instruments & option selection ────────────────────────────────
def test_loader_filters(base_dir):
    ld = InstrumentLoader().load(str(base_dir / "instrument.csv"), date(2026, 10, 13))
    assert set(ld.df["underlying_symbol"]) == {"CRUDEOIL"}
    assert "CRUDEOIL17NOV266500CE_BLOCKED" not in set(ld.df["trading_symbol"])
    assert ld.get_underlying_fut("nearest", date(2026, 10, 13))["api_symbol"] == "MCX_CRUDEOIL19OCT26FUT"
    assert ld.get_underlying_fut("nearest", date(2026, 10, 13))["trading_symbol"] == "CRUDEOIL19OCT26FUT"
    assert ld.get_underlying_fut("next", date(2026, 10, 13))["trading_symbol"] == "CRUDEOIL19NOV26FUT"
    assert date(2026, 9, 19) not in ld.list_expiries("FUT")


def test_option_selection(base_dir, cfg):
    ld = InstrumentLoader().load(str(base_dir / "instrument.csv"), date(2026, 10, 13))
    ins = cfg["instrument"]
    sel = OptionSelector(ld, ins)
    today = date(2026, 10, 13)
    # min_days_to_expiry=1 -> 14 OCT is allowed on 13 OCT, skipped on 14 OCT
    c = sel.select(Signal.BUY, 6523, today)
    assert (c.option_type, c.strike, c.expiry_date) == ("CE", 6500, date(2026, 10, 14))
    assert sel.select(Signal.SELL, 6530, today).strike == 6550
    assert sel.select(Signal.BUY, 6523, date(2026, 10, 14)).expiry_date == date(2026, 11, 17)
    ins2 = {**ins, "strike_selection": "1_OTM"}
    assert OptionSelector(ld, ins2).select(Signal.BUY, 6523, today).strike == 6550
    assert OptionSelector(ld, ins2).select(Signal.SELL, 6523, today).strike == 6450
    ins3 = {**ins, "strike_selection": "1_ITM"}
    assert OptionSelector(ld, ins3).select(Signal.BUY, 6523, today).strike == 6450
    assert OptionSelector(ld, ins3).select(Signal.SELL, 6523, today).strike == 6550
    # fallback: deep OTM falls back toward ATM; far-away underlying falls back to nearest listed strike
    ins4 = {**ins, "strike_selection": "2_OTM"}
    assert OptionSelector(ld, ins4).select(Signal.BUY, 7000, today).strike == 7000
    assert sel.select(Signal.BUY, 9000, today).strike == 7000
    assert OptionSelector(ld, {**ins, "expiry_selection": "next"}).select(Signal.BUY, 6500, today).expiry_date == date(2026, 11, 17)
    with pytest.raises(ValueError):
        sel.select(Signal.HOLD, 6500, today)


# ── Paper trader ──────────────────────────────────────────────────
def _contract(base_dir):
    ld = InstrumentLoader().load(str(base_dir / "instrument.csv"), date(2026, 10, 13))
    return ld.get_option("CE", date(2026, 10, 14), 6500)


def test_sl_tp_modes_and_pnl(base_dir):
    risk = {"assumed_delta": 0.5, "stop_loss": {"mode": "fixed_pct", "value": 30}, "take_profit": {"mode": "reward_risk", "value": 2}}
    t = PaperTrader(risk, 2)
    assert t.compute_levels(200, 10) == (pytest.approx(140), pytest.approx(320))
    t.risk = {**risk, "stop_loss": {"mode": "fixed_inr", "value": 20}, "take_profit": {"mode": "fixed_inr", "value": 50}}
    assert t.compute_levels(200, 10) == (180, 250)
    t.risk = {**risk, "stop_loss": {"mode": "atr", "value": 2}, "take_profit": {"mode": "atr", "value": 4}}
    assert t.compute_levels(200, 10) == (190, 220)
    t.risk = {**risk, "stop_loss": {"mode": "none", "value": 0}, "take_profit": {"mode": "none", "value": 0}}
    assert t.compute_levels(200, 10) == (None, None)

    t = PaperTrader(risk, 2)
    now = dt(13, 10, 0)
    p = t.open_position(_contract(base_dir), 200, 6500, 10, Signal.BUY, now)
    with pytest.raises(RuntimeError):
        t.open_position(_contract(base_dir), 200, 6500, 10, Signal.BUY, now)
    assert t.check_exit(150, 6500, now) is None
    assert t.check_exit(140, 6500, now).reason == "stop_loss"
    assert t.check_exit(320, 6500, now).reason == "take_profit"
    assert t.get_unrealized_pnl(210) == (210 - 200) * 2 * 100
    tr = t.close_position(ExitEvent("take_profit", 320, 6550, now))
    assert tr.gross_pnl_inr == 24000 and not t.has_open_position()


def test_position_roundtrip(base_dir):
    risk = {"stop_loss": {"mode": "fixed_pct", "value": 30}, "take_profit": {"mode": "fixed_pct", "value": 60}}
    t = PaperTrader(risk, 1)
    p = t.open_position(_contract(base_dir), 100, 6500, 5, Signal.SELL, dt(13, 10, 0))
    from core.paper_trader import OpenPosition
    import json
    q = OpenPosition.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q == p


# ── Trade log ─────────────────────────────────────────────────────
def test_trade_log_metrics(tmp_path, base_dir):
    risk = {"stop_loss": {"mode": "none", "value": 0}, "take_profit": {"mode": "none", "value": 0}}
    tl = TradeLog(tmp_path, date(2026, 10, 13), "halftrend", {"a": 1}, 100000)
    for exit_px in (300, 150, 220, 100):  # entry 200 -> +10000, -5000, +2000, -10000 (1 lot)
        t = PaperTrader(risk, 1)
        t.open_position(_contract(base_dir), 200, 6500, 5, Signal.BUY, dt(13, 10, 0))
        tl.record(t.close_position(ExitEvent("signal_exit", exit_px, 6500, dt(13, 11, 0))))
    m = tl.metrics()
    assert (m["total"], m["wins"], m["losses"]) == (4, 2, 2)
    assert m["net_pnl"] == -3000 and m["max_drawdown"] == 13000
    assert m["profit_factor"] == pytest.approx(12000 / 15000)
    assert m["largest_win"] == 10000 and m["largest_loss"] == -10000
    df = pd.read_csv(tl.path)
    assert len(df) == 4 and df["cumulative_pnl_inr"].iloc[-1] == -3000
    assert "TRADING SESSION SUMMARY" in tl.summary()
    # restart on the same day appends & recovers cumulative
    tl2 = TradeLog(tmp_path, date(2026, 10, 13), "halftrend", {"a": 1}, 100000)
    assert tl2.cumulative == -3000


# ── Strategy ──────────────────────────────────────────────────────
def test_halftrend_strategy_emits_signals():
    rng = np.random.default_rng(1)
    close = 6500 + np.cumsum(np.concatenate([np.full(60, 3.0), np.full(60, -3.0), np.full(60, 3.0)]) + rng.normal(0, 1, 180))
    df = pd.DataFrame({"Open": close, "High": close + 2, "Low": close - 2, "Close": close, "Volume": 1})
    s = load_strategy("halftrend", {"amplitude": 2, "channel_deviation": 2.0, "atr_period": 10})
    sigs = {s.generate_signal(df.iloc[: i + 1]) for i in range(20, 180)}
    assert Signal.BUY in sigs and Signal.SELL in sigs and Signal.HOLD in sigs
    assert s.last_atr > 0 and s.params["amplitude"] == 2
    with pytest.raises(ValueError):
        load_strategy("nope", {})


# ── Groww client ──────────────────────────────────────────────────
class FakeResp:
    def __init__(self, code=200, body=None):
        self.status_code, self._b = code, body or {}

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeHTTP:
    def __init__(self, resps):
        self.resps, self.calls = list(resps), []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, headers, params))
        r = self.resps.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_client_ok_retry_and_auth(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    ok = FakeResp(200, {"status": "SUCCESS", "payload": {"MCX_A": 1.5, "MCX_B": 2}})
    h = FakeHTTP([FakeResp(500), requests.ConnectionError("x"), ok])
    c = GrowwClient("tok", session=h)
    assert c.get_ltp(["MCX_A", "MCX_B"], "COMMODITY") == {"MCX_A": 1.5, "MCX_B": 2.0}
    url, headers, params = h.calls[-1]
    assert url.endswith("/live-data/ltp") and params == {"segment": "COMMODITY", "exchange_symbols": "MCX_A,MCX_B"}
    assert headers["Authorization"] == "Bearer tok" and headers["X-API-VERSION"] == "1.0"
    with pytest.raises(GrowwAuthError):
        GrowwClient("tok", session=FakeHTTP([FakeResp(401)])).get_ltp(["X"])
    with pytest.raises(GrowwAPIError):
        GrowwClient("tok", max_retries=1, session=FakeHTTP([FakeResp(200, {"status": "FAILURE"})] * 2)).get_ltp(["X"])


# ── Config ────────────────────────────────────────────────────────
def test_config_validation(cfg):
    validate(cfg)
    assert cfg["session"]["market_open"] == "09:15" and cfg["session"]["market_close"] == "23:30"
    import copy
    bad = copy.deepcopy(cfg); bad["api"]["access_token"] = ""
    with pytest.raises(ValueError):
        validate(bad)
    bad = copy.deepcopy(cfg); bad["risk"]["stop_loss"]["mode"] = "none"
    with pytest.raises(ValueError):
        validate(bad)
    bad = copy.deepcopy(cfg); bad["session"]["auto_squareoff_time"] = "23:45"
    with pytest.raises(ValueError):
        validate(bad)

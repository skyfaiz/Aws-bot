import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from bot import Bot
from core.config import parse_config
from core.config_edit import coerce, flatten, get_leaf, set_yaml_value
from core.telegram_control import Controller, MAX_FAILS
from strategies import Signal
from tests.test_bot import FUT, FakeClient, Script, at, warm

ROOT = Path(__file__).resolve().parent.parent
RAW_TEXT = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
CFG_TEXT = set_yaml_value(RAW_TEXT, "risk.stop_loss.value", 30.0)   # tests do not depend on the tuned default


# ── config editing ────────────────────────────────────────────────
def test_default_warmup_is_5():
    assert yaml.safe_load(CFG_TEXT)["candles"]["warmup_candles"] == 5


def test_set_yaml_value_preserves_everything_else():
    new = set_yaml_value(CFG_TEXT, "risk.stop_loss.value", 25.0)
    a, b = yaml.safe_load(CFG_TEXT), yaml.safe_load(new)
    assert b["risk"]["stop_loss"]["value"] == 25.0
    a["risk"]["stop_loss"]["value"] = 25.0
    assert a == b                                   # nothing else changed
    assert new.count("\n") == CFG_TEXT.count("\n")
    # same key name in a different section is not touched
    t = set_yaml_value(CFG_TEXT, "risk.take_profit.value", 3.0)
    assert yaml.safe_load(t)["risk"]["stop_loss"]["value"] == 30.0 and yaml.safe_load(t)["risk"]["take_profit"]["value"] == 3.0
    # strings stay strings (09:15 style) and comments survive
    t = set_yaml_value(CFG_TEXT, "session.market_close", "23:45")
    assert yaml.safe_load(t)["session"]["market_close"] == "23:45"
    t = set_yaml_value(CFG_TEXT, "instrument.lots", 2)
    assert "lots: 2" in t
    with pytest.raises(KeyError):
        set_yaml_value(CFG_TEXT, "risk.stop_loss", 1)       # section
    with pytest.raises(KeyError):
        set_yaml_value(CFG_TEXT, "risk.nope", 1)


def test_all_leaves_are_settable_roundtrip():
    raw = yaml.safe_load(CFG_TEXT)
    for key, val in flatten(raw).items():
        assert yaml.safe_load(set_yaml_value(CFG_TEXT, key, val)) == raw, key


def test_coerce():
    assert coerce(5, "7") == 7 and coerce(2.0, "3") == 3.0 and coerce(False, "on") is True
    assert coerce("CE", "PE") == "PE" and coerce("x", "null") is None and coerce(None, "2026-11-17") == "2026-11-17"
    with pytest.raises(ValueError):
        coerce(5, "abc")
    with pytest.raises(ValueError):
        coerce(True, "maybe")
    assert get_leaf(yaml.safe_load(CFG_TEXT), "strategy.params.amplitude") == 2


# ── controller ────────────────────────────────────────────────────
@pytest.fixture
def ctl(cfg, base_dir):
    c = FakeClient()
    b = Bot(cfg, client=c, base_dir=base_dir)
    Script(b)
    sent = []
    b.notify = sent.append
    (base_dir / "config").mkdir()
    cp = base_dir / "config" / "config.yaml"
    cp.write_text(CFG_TEXT, encoding="utf-8")
    t = [1000.0]
    ct = Controller(b, cp, base_dir / ".env", "679902", base_dir / "logs" / "auth.json", clock=lambda: t[0])
    ct.sent, ct.fake, ct.t = sent, c, t
    return ct


def test_auth_password_and_lockout(ctl):
    assert "Not authorised" in ctl.handle(1, "status", [])[0]
    assert ctl.handle(1, "set", ["a", "b"])[0].startswith("Not authorised")
    reply, delete = ctl.handle(1, "login", ["nope"])
    assert reply == "Wrong password." and delete
    reply, delete = ctl.handle(1, "login", ["679902"])
    assert reply.startswith("Authorised") and delete and ctl.is_authorized(1)
    # persists across restarts
    ct2 = Controller(ctl.bot, ctl.config_path, ctl.env_path, "679902", ctl.auth_path)
    assert ct2.is_authorized(1) and not ct2.is_authorized(2)
    for _ in range(MAX_FAILS):
        ctl.handle(2, "login", ["bad"])
    assert "Too many" in ctl.handle(2, "login", ["679902"])[0] and not ctl.is_authorized(2)
    ctl.t[0] += 901
    assert ctl.handle(2, "login", ["679902"])[0].startswith("Authorised")
    assert ctl.handle(2, "logout", [])[0] == "Logged out." and not ctl.is_authorized(2)


def login(ctl):
    ctl.handle(1, "login", ["679902"])


def test_set_applies_live_and_writes_yaml(ctl):
    login(ctl)
    b = ctl.bot
    r = ctl.handle(1, "set", ["risk.stop_loss.value", "25"])[0]
    assert "30.0 -> 25.0" in r and b.cfg["risk"]["stop_loss"]["value"] == 25.0 and b.trader.risk["stop_loss"]["value"] == 25.0
    assert yaml.safe_load(ctl.config_path.read_text())["risk"]["stop_loss"]["value"] == 25.0
    ctl.handle(1, "set", ["instrument.lots", "3"])
    assert b.trader.lots == 3
    ctl.handle(1, "set", ["candles.warmup_candles", "9"])
    assert b.builder.warmup == 9
    ctl.handle(1, "set", ["strategy.params.amplitude", "3"])
    assert b.strategy.params["amplitude"] == 3
    ctl.handle(1, "set", ["session.market_close", "23:45"])
    assert b.sess.close.minute == 45
    ctl.handle(1, "set", ["candles.interval_minutes", "10"])
    assert b.builder.interval == timedelta(minutes=10)
    # rejected values leave file and bot untouched
    before = ctl.config_path.read_text()
    for args in (["risk.stop_loss.mode", "bogus"], ["risk.stop_loss.value", "abc"], ["nope.key", "1"],
                 ["strategy.name", "doesnotexist"], ["session.auto_squareoff_time", "23:59"],
                 ["risk.stop_loss", "1"], ["paths.log_dir", "x"], ["api.access_token", "tok"]):
        assert not ctl.handle(1, "set", args)[0].count("->"), args
    assert ctl.config_path.read_text() == before
    assert b.cfg["risk"]["stop_loss"]["mode"] == "fixed_pct" and b.strategy.name == "halftrend"
    assert "Usage" in ctl.handle(1, "set", ["x"])[0]


def test_config_view_hides_token(ctl):
    login(ctl)
    out = ctl.handle(1, "config", ["api"])[0]
    assert "api.poll_interval_seconds = 5" in out and "access_token = ***" in out
    assert "risk.stop_loss.mode" in ctl.handle(1, "config", ["risk"])[0]
    assert "No settings" in ctl.handle(1, "config", ["zzz"])[0]


def test_settoken_writes_env_and_updates_client(ctl, monkeypatch):
    login(ctl)
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    ctl.env_path.write_text("TELEGRAM_BOT_TOKEN=abc\nGROWW_ACCESS_TOKEN=old\n")
    tok = "x" * 40
    reply, delete = ctl.handle(1, "settoken", [tok])
    assert "updated" in reply and delete
    env = ctl.env_path.read_text()
    assert f"GROWW_ACCESS_TOKEN={tok}" in env and "TELEGRAM_BOT_TOKEN=abc" in env and "old" not in env
    assert os.environ["GROWW_ACCESS_TOKEN"] == tok and ctl.bot.cfg["api"]["access_token"] == tok
    assert "Usage" in ctl.handle(1, "settoken", ["short"])[0]
    assert tok not in ctl.config_path.read_text()
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)


def test_pause_resume_and_squareoff(ctl):
    login(ctl)
    b, c = ctl.bot, ctl.fake
    assert "PAUSED" in ctl.handle(1, "pause", [])[0] and b.paused
    assert Bot(b.cfg, client=c, base_dir=b.base).paused          # pause survives restart
    t = warm(b)
    b.strategy.generate_signal = lambda candles: Signal.BUY
    b.step(t)
    assert not b.trader.has_open_position()                       # paused -> no entry
    assert "RESUMED" in ctl.handle(1, "resume", [])[0]
    b.step(t + timedelta(minutes=5))
    assert b.trader.has_open_position()
    assert [m for m in ctl.sent if m.startswith("ENTRY")]
    # manual squareoff
    assert "requested" in ctl.handle(1, "squareoff", [])[0]
    b.strategy.generate_signal = lambda candles: Signal.HOLD
    b.step(t + timedelta(minutes=5, seconds=5))
    assert not b.trader.has_open_position()
    assert [m for m in ctl.sent if m.startswith("EXIT") and "manual" in m]
    assert ctl.handle(1, "squareoff", [])[0] == "No open position."


def test_pause_still_allows_exit_on_opposite_signal(ctl):
    login(ctl)
    b = ctl.bot
    t = warm(b)
    b.strategy.generate_signal = lambda candles: Signal.BUY
    b.step(t)
    assert b.trader.has_open_position()
    ctl.handle(1, "pause", [])
    b.strategy.generate_signal = lambda candles: Signal.SELL
    b.step(t + timedelta(minutes=5))
    assert not b.trader.has_open_position()                       # closed, not re-entered


def test_status_position_pnl_trades(ctl):
    login(ctl)
    b = ctl.bot
    assert "No open position" in ctl.handle(1, "position", [])[0]
    assert "No trades" in ctl.handle(1, "trades", [])[0]
    t = warm(b)
    b.strategy.generate_signal = lambda candles: Signal.BUY
    b.step(t)
    ctl.fake.prices[b.trader.position.contract.groww_symbol] = 150
    b.step(t + timedelta(seconds=5))
    st = ctl.handle(1, "status", [])[0]
    assert "Market: OPEN" in st and "Trading: ACTIVE" in st and "Position: OPEN" in st and FUT in st
    assert "Unrealized P&L: Rs -5,000.00" in ctl.handle(1, "position", [])[0]
    assert "Unrealized P&L: Rs -5,000.00" in ctl.handle(1, "pnl", [])[0]
    ctl.fake.prices[b.trader.position.contract.groww_symbol] = 100
    b.step(t + timedelta(seconds=10))
    assert [m for m in ctl.sent if m.startswith("EXIT") and "stop_loss" in m and "-10,000.00" in m]
    assert "Realized P&L: Rs -10,000.00" in ctl.handle(1, "pnl", [])[0]
    tr = ctl.handle(1, "trades", ["3"])[0]
    assert "stop_loss" in tr and "-10,000.00" in tr
    assert "Unknown" in ctl.handle(1, "bogus", [])[0]
    assert "/pause" in ctl.handle(1, "help", [])[0]


def test_notifications_session_end_and_auth(ctl):
    from core.groww_client import GrowwAuthError
    login(ctl)
    b = ctl.bot
    b.step(at(13, 9, 15))
    ctl.fake.error = GrowwAuthError("expired")
    b.step(at(13, 9, 16)); b.step(at(13, 9, 17))
    assert len([m for m in ctl.sent if "AUTH FAILURE" in m]) == 1       # not spammed
    ctl.fake.error = None
    b.step(at(13, 9, 18))
    assert any("restored" in m for m in ctl.sent)
    b.step(at(13, 23, 30))
    assert any(m.startswith("Session ended") for m in ctl.sent)

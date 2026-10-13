from datetime import datetime, time
from zoneinfo import ZoneInfo


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class Session:
    """MCX session: Mon-Fri, open..close in the configured timezone."""

    def __init__(self, cfg: dict):
        self.tz = ZoneInfo(cfg["timezone"])
        self.open = _t(cfg["market_open"])
        self.close = _t(cfg["market_close"])
        self.squareoff = _t(cfg["auto_squareoff_time"])
        self.trade_start = _t(cfg["trade_only_between"]["start"])
        self.trade_end = _t(cfg["trade_only_between"]["end"])

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def is_trading_day(self, dt: datetime) -> bool:
        return dt.weekday() < 5

    def is_market_open(self, dt: datetime) -> bool:
        return self.is_trading_day(dt) and self.open <= dt.time() < self.close

    def is_squareoff_time(self, dt: datetime) -> bool:
        return self.is_trading_day(dt) and dt.time() >= self.squareoff

    def can_enter(self, dt: datetime) -> bool:
        return self.is_trading_day(dt) and self.trade_start <= dt.time() < self.trade_end

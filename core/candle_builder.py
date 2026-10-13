from dataclasses import dataclass
from datetime import datetime, timedelta, time
from pathlib import Path

import pandas as pd

COLUMNS = ["Datetime", "Open", "High", "Low", "Close", "Volume"]


@dataclass
class Candle:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 1

    def row(self) -> list:
        return [self.start, self.open, self.high, self.low, self.close, self.volume]


class CandleBuilder:
    """Aggregates LTP ticks into clock-aligned (to session open) OHLCV candles."""

    def __init__(self, interval_minutes: int, max_window: int, warmup_candles: int, session_open: time = time(9, 15)):
        self.interval = timedelta(minutes=interval_minutes)
        self.max_window, self.warmup = max_window, warmup_candles
        self.session_open = session_open
        self.current: Candle | None = None
        self._rows: list[list] = []

    def _bucket(self, ts: datetime) -> datetime:
        base = ts.replace(hour=self.session_open.hour, minute=self.session_open.minute, second=0, microsecond=0)
        if ts < base:
            return base - self.interval
        return base + ((ts - base) // self.interval) * self.interval

    def add_tick(self, price: float, ts: datetime) -> list[Candle]:
        """Returns list of candles sealed by this tick (usually empty or one; more if gaps are flat-filled)."""
        bucket = self._bucket(ts)
        if self.current is None:
            self.current = Candle(bucket, price, price, price, price)
            return []
        if bucket <= self.current.start:
            c = self.current
            c.high, c.low, c.close, c.volume = max(c.high, price), min(c.low, price), price, c.volume + 1
            return []
        sealed = [self.current]
        prev = self.current
        if prev.start.date() == bucket.date():
            t = prev.start + self.interval
            while t < bucket:
                sealed.append(Candle(t, prev.close, prev.close, prev.close, prev.close, 0))
                t += self.interval
        self.current = Candle(bucket, price, price, price, price)
        self._append(sealed)
        return sealed

    def _append(self, candles: list[Candle]) -> None:
        self._rows.extend(c.row() for c in candles)
        self._rows = self._rows[-self.max_window:]

    def seal_current(self) -> Candle | None:
        """Force-seal the open candle (used at market close)."""
        c, self.current = self.current, None
        if c is not None:
            self._append([c])
        return c

    def get_candles(self) -> pd.DataFrame:
        return pd.DataFrame(self._rows, columns=COLUMNS)

    def is_warm(self) -> bool:
        return len(self._rows) >= self.warmup

    def __len__(self) -> int:
        return len(self._rows)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.get_candles().to_csv(path, index=False)

    def load(self, path: str | Path, now: datetime, max_stale_hours: float) -> int:
        p = Path(path)
        if not p.exists():
            return 0
        df = pd.read_csv(p, parse_dates=["Datetime"])
        if df.empty:
            return 0
        df["Datetime"] = df["Datetime"].apply(lambda d: d.to_pydatetime())
        last = df["Datetime"].iloc[-1]
        if last.tzinfo is None and now.tzinfo is not None:
            return 0
        if now - last > timedelta(hours=max_stale_hours):
            return 0
        self._rows = df[COLUMNS].values.tolist()[-self.max_window:]
        return len(self._rows)

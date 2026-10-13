"""Appends everything the bot sees (ticks, candles, signals) to daily CSVs and prunes old files."""
import csv
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("data")
_NAME = re.compile(r"^(ticks|candles|signals)_(\d{4}-\d{2}-\d{2})\.csv$")
HEADERS = {
    "ticks": ["timestamp", "symbol", "ltp"],
    "candles": ["start", "open", "high", "low", "close", "ticks", "interval_min"],
    "signals": ["timestamp", "candle_start", "close", "atr", "signal", "paused", "position"],
}


class DataStore:
    def __init__(self, directory: str | Path, retention_days: int = 30, enabled: bool = True):
        self.dir, self.enabled = Path(directory), enabled
        self.retention_days = max(7, int(retention_days))

    def _append(self, kind: str, day: date, rows: list[list]) -> None:
        if not self.enabled or not rows:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / f"{kind}_{day.isoformat()}.csv"
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(HEADERS[kind])
                w.writerows(rows)
        except OSError as e:  # never let disk problems stop trading
            log.error("data write failed: %s", e)

    def record_ticks(self, now: datetime, prices: dict[str, float]) -> None:
        self._append("ticks", now.date(), [[now.isoformat(), s, p] for s, p in prices.items()])

    def record_candles(self, candles, interval_min: int) -> None:
        for c in candles:
            self._append("candles", c.start.date(), [[c.start.isoformat(), c.open, c.high, c.low, c.close, c.volume, interval_min]])

    def record_signal(self, now: datetime, candle, atr: float, signal: str, paused: bool, position: str | None) -> None:
        self._append("signals", now.date(), [[now.isoformat(), candle.start.isoformat(), candle.close, atr, signal, paused, position or ""]])

    def prune(self, today: date) -> int:
        if not self.dir.exists():
            return 0
        cutoff = today - timedelta(days=self.retention_days)
        n = 0
        for f in self.dir.iterdir():
            m = _NAME.match(f.name)
            if m and date.fromisoformat(m.group(2)) < cutoff:
                f.unlink()
                n += 1
        if n:
            log.info("pruned %d data files older than %s", n, cutoff)
        return n

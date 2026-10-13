import uuid
from dataclasses import dataclass, asdict, field
from datetime import date, datetime

from strategies.base import Signal
from .instrument_loader import OptionContract


@dataclass
class OpenPosition:
    contract: OptionContract
    entry_time: datetime
    entry_option_ltp: float
    entry_underlying_ltp: float
    entry_atr: float
    lots: int
    sl_price: float | None
    tp_price: float | None
    signal: Signal
    trade_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time"] = self.entry_time.isoformat()
        d["signal"] = self.signal.value
        d["contract"]["expiry_date"] = self.contract.expiry_date.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OpenPosition":
        c = dict(d["contract"])
        c["expiry_date"] = date.fromisoformat(c["expiry_date"])
        d = {**d, "contract": OptionContract(**c), "entry_time": datetime.fromisoformat(d["entry_time"]),
             "signal": Signal(d["signal"])}
        return cls(**d)


@dataclass
class ExitEvent:
    reason: str  # stop_loss | take_profit | signal_exit | squareoff | market_close
    option_ltp: float
    underlying_ltp: float
    time: datetime


@dataclass
class CompletedTrade:
    trade_id: str
    entry_time: datetime
    exit_time: datetime
    signal: str
    option_type: str
    trading_symbol: str
    expiry_date: date
    strike: float
    entry_underlying_ltp: float
    exit_underlying_ltp: float
    entry_option_ltp: float
    exit_option_ltp: float
    lots: int
    lot_size: int
    sl_price: float | None
    tp_price: float | None
    exit_reason: str
    gross_pnl_inr: float
    net_pnl_inr: float


class PaperTrader:
    def __init__(self, risk_cfg: dict, lots: int):
        self.risk, self.lots = risk_cfg, lots
        self.position: OpenPosition | None = None

    @staticmethod
    def _distance(mode: str, value: float, entry: float, atr: float, delta: float) -> float | None:
        if mode == "fixed_inr":
            return value
        if mode == "fixed_pct":
            return entry * value / 100.0
        if mode == "atr":
            return value * atr * delta
        return None

    def compute_levels(self, entry: float, atr: float) -> tuple[float | None, float | None]:
        sl_cfg, tp_cfg = self.risk["stop_loss"], self.risk["take_profit"]
        delta = float(self.risk.get("assumed_delta", 0.5))
        sl_d = self._distance(sl_cfg["mode"], float(sl_cfg["value"]), entry, atr, delta)
        sl = max(entry - sl_d, 0.0) if sl_d is not None else None
        if tp_cfg["mode"] == "reward_risk":
            tp = entry + float(tp_cfg["value"]) * (entry - sl) if sl is not None else None
        else:
            tp_d = self._distance(tp_cfg["mode"], float(tp_cfg["value"]), entry, atr, delta)
            tp = entry + tp_d if tp_d is not None else None
        return sl, tp

    def open_position(self, contract: OptionContract, option_ltp: float, underlying_ltp: float,
                      atr: float, signal: Signal, now: datetime) -> OpenPosition:
        if self.position:
            raise RuntimeError("position already open")
        sl, tp = self.compute_levels(option_ltp, atr)
        self.position = OpenPosition(contract, now, option_ltp, underlying_ltp, atr, self.lots, sl, tp, signal)
        return self.position

    def has_open_position(self) -> bool:
        return self.position is not None

    def check_exit(self, option_ltp: float, underlying_ltp: float, now: datetime) -> ExitEvent | None:
        p = self.position
        if p is None or option_ltp is None:
            return None
        if p.sl_price is not None and option_ltp <= p.sl_price:
            return ExitEvent("stop_loss", option_ltp, underlying_ltp, now)
        if p.tp_price is not None and option_ltp >= p.tp_price:
            return ExitEvent("take_profit", option_ltp, underlying_ltp, now)
        return None

    def close_position(self, ev: ExitEvent) -> CompletedTrade:
        p = self.position
        if p is None:
            raise RuntimeError("no open position")
        c = p.contract
        gross = (ev.option_ltp - p.entry_option_ltp) * p.lots * c.lot_size
        self.position = None
        return CompletedTrade(
            p.trade_id, p.entry_time, ev.time, p.signal.value, c.option_type, c.trading_symbol, c.expiry_date,
            c.strike, p.entry_underlying_ltp, ev.underlying_ltp, p.entry_option_ltp, ev.option_ltp, p.lots,
            c.lot_size, p.sl_price, p.tp_price, ev.reason, round(gross, 2), round(gross, 2),
        )

    def get_unrealized_pnl(self, option_ltp: float) -> float:
        p = self.position
        return 0.0 if p is None else (option_ltp - p.entry_option_ltp) * p.lots * p.contract.lot_size

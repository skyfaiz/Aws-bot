"""MCX CRUDEOIL options paper-trading bot. Run: python bot.py [--config config/config.yaml]"""
import argparse
import json
import logging
import signal as os_signal
import sys
import time
from datetime import datetime
from pathlib import Path

from core.candle_builder import CandleBuilder
from core.config import load_config, load_dotenv
from core.groww_client import GrowwAPIError, GrowwAuthError, GrowwClient
from core.instrument_loader import InstrumentLoader
from core.option_selector import OptionSelector
from core.paper_trader import ExitEvent, OpenPosition, PaperTrader
from core.session import Session
from core.trade_log import TradeLog
from strategies import Signal, load_strategy

log = logging.getLogger("bot")


class Bot:
    def __init__(self, cfg: dict, client=None, session: Session | None = None, base_dir: str | Path = "."):
        self.cfg = cfg
        self.base = Path(base_dir)
        self.sess = session or Session(cfg["session"])
        api, ins, cd = cfg["api"], cfg["instrument"], cfg["candles"]
        self.client = client or GrowwClient(api["access_token"], api["base_url"], api["version"],
                                            api["max_retries"], api["retry_backoff_seconds"])
        self.segment = ins["segment"]
        self.strategy = load_strategy(cfg["strategy"]["name"], cfg["strategy"]["params"])
        self.builder = CandleBuilder(cd["interval_minutes"], cd["max_window"], cd["warmup_candles"], self.sess.open)
        self.trader = PaperTrader(cfg["risk"], ins["lots"])
        self.log_dir = self.base / cfg["paths"]["log_dir"]
        self.candle_path = self.log_dir / "candles.csv"
        self.state_path = self.log_dir / "state.json"
        self.loader: InstrumentLoader | None = None
        self.selector: OptionSelector | None = None
        self.fut_symbol: str | None = None
        self.trade_log: TradeLog | None = None
        self.day = None
        self.session_active = False
        self.last_option_ltp: float | None = None
        self.last_good_tick: datetime | None = None
        self.last_stall_warn: datetime | None = None
        self.tick_count = 0
        self.stop = False
        self._restore_state()

    # ── persistence ────────────────────────────────────────────────
    def _save_state(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        p = self.trader.position
        self.state_path.write_text(json.dumps({"position": p.to_dict() if p else None}), encoding="utf-8")

    def _restore_state(self) -> None:
        if self.state_path.exists():
            d = json.loads(self.state_path.read_text(encoding="utf-8")).get("position")
            if d:
                self.trader.position = OpenPosition.from_dict(d)
                log.warning("Restored open position %s", self.trader.position.contract.trading_symbol)

    # ── day / session setup ────────────────────────────────────────
    def _ensure_day(self, now: datetime) -> None:
        if self.day == now.date():
            return
        self.day = now.date()
        ins = self.cfg["instrument"]
        csv = Path(ins["csv_path"])
        csv = csv if csv.is_absolute() else self.base / csv
        self.loader = InstrumentLoader(ins["exchange"], ins["underlying_symbol"], ins["segment"]).load(str(csv), self.day)
        self.selector = OptionSelector(self.loader, ins)
        fut = self.loader.get_underlying_fut("next" if ins["expiry_selection"] == "next" else "nearest", self.day)
        self.fut_symbol = fut["api_symbol"]
        self.trade_log = TradeLog(self.log_dir, self.day, self.strategy.name, self.strategy.params,
                                  self.cfg["paper"]["initial_capital_inr"])
        n = 0
        if self.cfg["candles"]["persist"] and len(self.builder) == 0:
            n = self.builder.load(self.candle_path, now, self.cfg["candles"]["max_stale_hours"])
        log.info("Day %s | underlying=%s | strategy=%s %s | loaded %d candles",
                 self.day, self.fut_symbol, self.strategy.name, self.strategy.params, n)

    def _end_session(self, now: datetime) -> None:
        self.session_active = False
        if self.trader.has_open_position():
            self._close(ExitEvent("market_close", self.last_option_ltp or self.trader.position.entry_option_ltp,
                                  self.builder.current.close if self.builder.current else 0.0, now))
        self.builder.seal_current()
        if self.cfg["candles"]["persist"]:
            self.builder.save(self.candle_path)
        if self.trade_log:
            log.info("\n%s", self.trade_log.summary())

    # ── trading actions ────────────────────────────────────────────
    def _close(self, ev: ExitEvent) -> None:
        trade = self.trader.close_position(ev)
        self.trade_log.record(trade)
        self._save_state()
        self.last_option_ltp = None
        log.info("EXIT   | %s @ %.2f | reason=%s | net P&L=Rs %.2f | session=Rs %.2f", trade.trading_symbol,
                 trade.exit_option_ltp, trade.exit_reason, trade.net_pnl_inr, self.trade_log.cumulative)

    def _enter(self, sig: Signal, underlying: float, now: datetime) -> None:
        contract = self.selector.select(sig, underlying, now.date())
        px = self.client.get_ltp([contract.groww_symbol], self.segment)
        entry = px.get(contract.groww_symbol)
        if not entry or entry <= 0:
            log.warning("No option LTP for %s; entry skipped", contract.groww_symbol)
            return
        pos = self.trader.open_position(contract, entry, underlying, getattr(self.strategy, "last_atr", 0.0), sig, now)
        self.last_option_ltp = entry
        self._save_state()
        log.info("ENTRY  | %s %s @ %.2f | SL=%s | TP=%s", sig.value, contract.trading_symbol, entry,
                 f"{pos.sl_price:.2f}" if pos.sl_price is not None else "-",
                 f"{pos.tp_price:.2f}" if pos.tp_price is not None else "-")

    def _on_signal(self, sig: Signal, underlying: float, now: datetime) -> None:
        pos = self.trader.position
        if sig == Signal.EXIT:
            if pos:
                self._close(ExitEvent("signal_exit", self.last_option_ltp or pos.entry_option_ltp, underlying, now))
            return
        if sig not in (Signal.BUY, Signal.SELL):
            return
        if pos:
            if pos.signal == sig:
                return
            self._close(ExitEvent("signal_exit", self.last_option_ltp or pos.entry_option_ltp, underlying, now))
        if self.sess.can_enter(now) and not self.sess.is_squareoff_time(now):
            self._enter(sig, underlying, now)

    # ── main step ──────────────────────────────────────────────────
    def step(self, now: datetime) -> None:
        if not self.sess.is_market_open(now):
            if self.session_active:
                self._end_session(now)
            return
        self._ensure_day(now)
        self.session_active = True
        pos = self.trader.position
        symbols = [self.fut_symbol] + ([pos.contract.groww_symbol] if pos else [])
        try:
            prices = self.client.get_ltp(symbols, self.segment)
        except GrowwAuthError as e:
            log.critical("AUTH FAILURE: %s -- renew GROWW_ACCESS_TOKEN and restart", e)
            return
        except GrowwAPIError as e:
            log.warning("LTP fetch failed: %s", e)
            self._stall_check(now)
            return
        underlying = prices.get(self.fut_symbol)
        if underlying is None:
            log.warning("No LTP for %s", self.fut_symbol)
            self._stall_check(now)
            return
        self.last_good_tick = now
        self.tick_count += 1

        if pos:
            opt = prices.get(pos.contract.groww_symbol)
            if opt is not None:
                self.last_option_ltp = opt
                if pos.entry_time.date() < now.date():
                    self._close(ExitEvent("market_close", opt, underlying, now))
                elif self.sess.is_squareoff_time(now):
                    self._close(ExitEvent("squareoff", opt, underlying, now))
                else:
                    ev = self.trader.check_exit(opt, underlying, now)
                    if ev:
                        self._close(ev)
                    elif self.tick_count % 12 == 0:
                        log.info("TICK   | %s LTP=%.2f | unrealized=Rs %.2f", pos.contract.trading_symbol, opt,
                                 self.trader.get_unrealized_pnl(opt))

        sealed = self.builder.add_tick(underlying, now)
        if sealed:
            if self.cfg["candles"]["persist"]:
                self.builder.save(self.candle_path)
            if self.builder.is_warm():
                sig = self.strategy.generate_signal(self.builder.get_candles())
                log.info("CANDLE | %s close=%.2f signal=%s", sealed[-1].start.strftime("%H:%M"), sealed[-1].close, sig.value)
                self._on_signal(sig, underlying, now)
            else:
                log.info("CANDLE | warmup %d/%d", len(self.builder), self.builder.warmup)

    def _stall_check(self, now: datetime) -> None:
        limit = self.cfg["api"]["stall_warning_seconds"]
        ref = self.last_good_tick
        if ref and (now - ref).total_seconds() > limit and (
                not self.last_stall_warn or (now - self.last_stall_warn).total_seconds() > limit):
            log.warning("STALL: no LTP tick for %ds", (now - ref).total_seconds())
            self.last_stall_warn = now

    def run(self) -> None:
        poll = self.cfg["api"]["poll_interval_seconds"]
        log.info("Bot started | poll=%ss | session %s-%s IST", poll, self.sess.open, self.sess.close)
        while not self.stop:
            now = self.sess.now()
            try:
                self.step(now)
            except Exception:
                log.exception("Unhandled error in step")
            time.sleep(poll if self.sess.is_market_open(now) else 30)
        self._end_session(self.sess.now())
        log.info("Bot stopped")


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(log_dir / "bot.log", encoding="utf-8")]
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/config.yaml")
    args = ap.parse_args()
    base = Path(__file__).resolve().parent
    load_dotenv(base / ".env")
    cfg = load_config(base / args.config if not Path(args.config).is_absolute() else args.config)
    setup_logging(base / cfg["paths"]["log_dir"])
    bot = Bot(cfg, base_dir=base)

    def _stop(*_):
        bot.stop = True
    os_signal.signal(os_signal.SIGINT, _stop)
    os_signal.signal(os_signal.SIGTERM, _stop)
    bot.run()


if __name__ == "__main__":
    main()

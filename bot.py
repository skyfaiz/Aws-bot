"""MCX CRUDEOIL options paper-trading bot. Run: python bot.py [--config config/config.yaml]"""
import argparse
import json
import logging
import signal as os_signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from core.candle_builder import CandleBuilder
from core.data_store import DataStore
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
        d = cfg["data"]
        self.store = DataStore(self.base / d["dir"], d["retention_days"], d["enabled"])
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
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.notify = lambda text: None
        self.paused = False
        self.squareoff_requested = False
        self.auth_failed = False
        self.last_underlying: float | None = None
        self.last_signal: Signal | None = None
        self._last_error: str | None = None
        self._restore_state()

    # ── persistence ────────────────────────────────────────────────
    def _save_state(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        p = self.trader.position
        self.state_path.write_text(json.dumps({"position": p.to_dict() if p else None, "paused": self.paused}),
                                   encoding="utf-8")

    def _restore_state(self) -> None:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.paused = bool(state.get("paused", False))
            d = state.get("position")
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
        self.store.prune(self.day)
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
            self.notify("Session ended\n" + self.trade_log.summary())

    # ── trading actions ────────────────────────────────────────────
    def _close(self, ev: ExitEvent) -> None:
        trade = self.trader.close_position(ev)
        self.trade_log.record(trade)
        self._save_state()
        self.last_option_ltp = None
        log.info("EXIT   | %s @ %.2f | reason=%s | net P&L=Rs %.2f | session=Rs %.2f", trade.trading_symbol,
                 trade.exit_option_ltp, trade.exit_reason, trade.net_pnl_inr, self.trade_log.cumulative)
        self.notify(f"EXIT {trade.trading_symbol}\nEntry {trade.entry_option_ltp:.2f} -> Exit {trade.exit_option_ltp:.2f}"
                    f" ({trade.lots} lot)\nReason: {trade.exit_reason}\nTrade P&L: Rs {trade.net_pnl_inr:,.2f}\n"
                    f"Session P&L: Rs {self.trade_log.cumulative:,.2f}")

    def _enter(self, sig: Signal, underlying: float, now: datetime) -> None:
        contract = self.selector.select(sig, underlying, now.date())
        px = self.client.get_ltp([contract.groww_symbol], self.segment)
        entry = px.get(contract.groww_symbol)
        if not entry or entry <= 0:
            log.warning("No option LTP for %s; entry skipped", contract.groww_symbol)
            return
        lots, cost, limit = self.size_lots(entry, contract.lot_size)
        if lots < 1:
            msg = (f"Entry skipped: 1 lot of {contract.trading_symbol} costs Rs {cost:,.2f}, above the "
                   f"{self.cfg['risk']['max_capital_pct']}% capital limit of Rs {limit:,.2f}")
            log.warning(msg)
            self.notify(msg)
            return
        pos = self.trader.open_position(contract, entry, underlying, getattr(self.strategy, "last_atr", 0.0), sig, now, lots)
        self.last_option_ltp = entry
        self._save_state()
        log.info("ENTRY  | %s %s @ %.2f | SL=%s | TP=%s", sig.value, contract.trading_symbol, entry,
                 f"{pos.sl_price:.2f}" if pos.sl_price is not None else "-",
                 f"{pos.tp_price:.2f}" if pos.tp_price is not None else "-")
        self.notify(f"ENTRY {sig.value} {contract.trading_symbol}\nPrice {entry:.2f} | underlying {underlying:.2f}\n"
                    f"SL {f'{pos.sl_price:.2f}' if pos.sl_price is not None else '-'} | "
                    f"TP {f'{pos.tp_price:.2f}' if pos.tp_price is not None else '-'}\n"
                    f"{pos.lots} lot(s) = Rs {cost * lots:,.2f} ({cost * lots / limit * float(self.cfg['risk']['max_capital_pct']):.1f}% of capital)")

    def size_lots(self, price: float, lot_size: int) -> tuple[int, float, float]:
        """Lots to buy: the configured lots, reduced so trade value stays strictly below max_capital_pct of equity.
        Returns (lots, cost_of_one_lot, value_limit)."""
        equity = self.cfg["paper"]["initial_capital_inr"] + TradeLog.all_time_pnl(self.log_dir)
        limit = equity * float(self.cfg["risk"]["max_capital_pct"]) / 100.0
        cost = price * lot_size
        fit = int(limit // cost) if limit > 0 else 0
        if fit * cost >= limit:
            fit -= 1
        return max(0, min(int(self.cfg["instrument"]["lots"]), fit)), cost, limit

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
        if self.paused:
            log.info("Paused: entry for %s skipped", sig.value)
        elif self.sess.can_enter(now) and not self.sess.is_squareoff_time(now):
            self._enter(sig, underlying, now)

    # ── main step ──────────────────────────────────────────────────
    def step(self, now: datetime) -> None:
        with self.lock:
            self._step(now)

    def _step(self, now: datetime) -> None:
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
            if not self.auth_failed:
                self.notify(f"AUTH FAILURE: {e}\nSend /settoken <new token> to fix.")
            self.auth_failed = True
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
        if self.auth_failed:
            self.auth_failed = False
            self.notify("Groww API access restored.")
        self.store.record_ticks(now, prices)
        self.last_good_tick = now
        self.last_underlying = underlying
        self.tick_count += 1

        if pos:
            opt = prices.get(pos.contract.groww_symbol)
            if opt is not None:
                self.last_option_ltp = opt
                if pos.entry_time.date() < now.date():
                    self._close(ExitEvent("market_close", opt, underlying, now))
                elif self.sess.is_squareoff_time(now):
                    self._close(ExitEvent("squareoff", opt, underlying, now))
                elif self.squareoff_requested:
                    self._close(ExitEvent("manual", opt, underlying, now))
                else:
                    ev = self.trader.check_exit(opt, underlying, now)
                    if ev:
                        self._close(ev)
                    elif self.tick_count % 12 == 0:
                        log.info("TICK   | %s LTP=%.2f | unrealized=Rs %.2f", pos.contract.trading_symbol, opt,
                                 self.trader.get_unrealized_pnl(opt))

        self.squareoff_requested = False
        sealed = self.builder.add_tick(underlying, now)
        if sealed:
            self.store.record_candles(sealed, self.cfg["candles"]["interval_minutes"])
            if self.cfg["candles"]["persist"]:
                self.builder.save(self.candle_path)
            if self.builder.is_warm():
                sig = self.strategy.generate_signal(self.builder.get_candles())
                self.last_signal = sig
                self.store.record_signal(now, sealed[-1], getattr(self.strategy, "last_atr", 0.0), sig.value, self.paused,
                                         self.trader.position.contract.trading_symbol if self.trader.position else None)
                log.info("CANDLE | %s close=%.2f signal=%s", sealed[-1].start.strftime("%H:%M"), sealed[-1].close, sig.value)
                if self.cfg.get("telegram", {}).get("notify_signals", False):
                    self.notify(f"Candle {sealed[-1].start.strftime('%H:%M')} close {sealed[-1].close:.2f} -> {sig.value}")
                self._on_signal(sig, underlying, now)
            else:
                log.info("CANDLE | warmup %d/%d", len(self.builder), self.builder.warmup)

    # -- runtime control (used by the Telegram service) --
    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused
            self._save_state()
        log.info("Trading %s", "PAUSED" if paused else "RESUMED")

    def set_token(self, token: str) -> None:
        with self.lock:
            if isinstance(self.client, GrowwClient):
                self.client.headers["Authorization"] = f"Bearer {token}"
            self.cfg["api"]["access_token"] = token

    def apply_config(self, cfg: dict) -> None:
        """Apply a validated config to the running bot (builds new parts first, then swaps)."""
        with self.lock:
            old = self.cfg
            sess = Session(cfg["session"])
            strategy = load_strategy(cfg["strategy"]["name"], cfg["strategy"]["params"])
            cd, ocd = cfg["candles"], old["candles"]
            rebuild = (cd["interval_minutes"] != ocd["interval_minutes"]
                       or cfg["session"]["market_open"] != old["session"]["market_open"])
            builder = CandleBuilder(cd["interval_minutes"], cd["max_window"], cd["warmup_candles"], sess.open) if rebuild else None
            reload_ins = any(cfg["instrument"][k] != old["instrument"][k]
                             for k in ("csv_path", "exchange", "underlying_symbol", "segment", "expiry_selection"))
            self.cfg, self.sess, self.strategy = cfg, sess, strategy
            if builder is not None:
                self.builder = builder
                self.candle_path.unlink(missing_ok=True)
            else:
                self.builder.warmup, self.builder.max_window = cd["warmup_candles"], cd["max_window"]
                self.builder._rows = self.builder._rows[-cd["max_window"]:]
            self.trader.risk, self.trader.lots = cfg["risk"], cfg["instrument"]["lots"]
            self.store.enabled, self.store.retention_days = cfg["data"]["enabled"], max(7, int(cfg["data"]["retention_days"]))
            self.segment = cfg["instrument"]["segment"]
            if self.selector:
                self.selector.cfg = cfg["instrument"]
                self.selector.step = float(cfg["instrument"].get("strike_interval", 50))
            if reload_ins:
                self.day = None
            if isinstance(self.client, GrowwClient):
                self.client.max_retries = cfg["api"]["max_retries"]
                self.client.retry_backoff = cfg["api"]["retry_backoff_seconds"]

    def _stall_check(self, now: datetime) -> None:
        limit = self.cfg["api"]["stall_warning_seconds"]
        ref = self.last_good_tick
        if ref and (now - ref).total_seconds() > limit and (
                not self.last_stall_warn or (now - self.last_stall_warn).total_seconds() > limit):
            log.warning("STALL: no LTP tick for %ds", (now - ref).total_seconds())
            self.notify(f"WARNING: no LTP tick for {int((now - ref).total_seconds())}s")
            self.last_stall_warn = now

    def run(self) -> None:
        poll = self.cfg["api"]["poll_interval_seconds"]
        log.info("Bot started | poll=%ss | session %s-%s IST", poll, self.sess.open, self.sess.close)
        while not self.stop_event.is_set():
            now = self.sess.now()
            try:
                self.step(now)
                self._last_error = None
            except Exception as e:
                log.exception("Unhandled error in step")
                if self._last_error != repr(e):
                    self.notify(f"ERROR in bot loop: {e!r}")
                    self._last_error = repr(e)
            self.stop_event.wait(poll if self.sess.is_market_open(now) else 30)
        with self.lock:
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
        bot.stop_event.set()
    os_signal.signal(os_signal.SIGINT, _stop)
    os_signal.signal(os_signal.SIGTERM, _stop)
    bot.run()


if __name__ == "__main__":
    main()

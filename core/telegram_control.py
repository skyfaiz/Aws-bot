"""Transport-independent command logic for the Telegram service (easy to unit-test)."""
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from core.config import parse_config
from core.config_edit import BLOCKED_PREFIXES, SECRET_KEYS, coerce, flatten, get_leaf, set_yaml_value
from core.groww_client import GrowwAuthError
from core.trade_log import TradeLog

MAX_FAILS, LOCKOUT_SECONDS = 5, 900

HELP = """Commands
/login <password> - authorise this chat
/logout - remove this chat
/status - bot, market and position state
/position - open position
/pnl - today's P&L and metrics
/trades [n] - last n trades today (default 5)
/config [section] - show settings
/set <key> <value> - change a setting, e.g. /set risk.stop_loss.value 25
/settoken <token> - set a Groww access token directly
/setkeys <api_key> <api_secret> - set Groww API key + secret (token fetched automatically and renewed daily)
/refreshtoken - fetch a new token now (after approving the key on the Groww Cloud API Keys page)
/pause - stop opening new trades
/resume - allow new trades again
/squareoff - close the open position now"""


class Controller:
    def __init__(self, bot, config_path: str | Path, env_path: str | Path, password: str, auth_path: str | Path,
                 clock=time.time):
        self.bot, self.config_path, self.env_path = bot, Path(config_path), Path(env_path)
        self.password, self.auth_path, self.clock = str(password), Path(auth_path), clock
        self.fails: dict[int, list[float]] = {}
        self.chats: set[int] = set()
        if self.auth_path.exists():
            self.chats = set(json.loads(self.auth_path.read_text(encoding="utf-8")))

    # ── auth ───────────────────────────────────────────────────────
    def _save_chats(self) -> None:
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_path.write_text(json.dumps(sorted(self.chats)), encoding="utf-8")

    def is_authorized(self, chat_id: int) -> bool:
        return chat_id in self.chats

    def login(self, chat_id: int, supplied: str) -> str:
        now = self.clock()
        recent = [t for t in self.fails.get(chat_id, []) if now - t < LOCKOUT_SECONDS]
        self.fails[chat_id] = recent
        if len(recent) >= MAX_FAILS:
            return "Too many wrong attempts. Try again in 15 minutes."
        if supplied != self.password:
            recent.append(now)
            return "Wrong password."
        self.fails.pop(chat_id, None)
        self.chats.add(chat_id)
        self._save_chats()
        return "Authorised. You will now receive trade updates. Send /help for commands."

    def logout(self, chat_id: int) -> str:
        self.chats.discard(chat_id)
        self._save_chats()
        return "Logged out."

    # ── dispatch ───────────────────────────────────────────────────
    def handle(self, chat_id: int, cmd: str, args: list[str]) -> tuple[str, bool]:
        """Returns (reply, delete_incoming_message)."""
        if cmd == "login":
            if not args:
                return "Usage: /login <password>", False
            return self.login(chat_id, args[0]), True
        if cmd in ("start", "help") and not self.is_authorized(chat_id):
            return "MCX paper trading bot. Send /login <password> to continue.", False
        if not self.is_authorized(chat_id):
            return "Not authorised. Send /login <password>.", False
        fn = {
            "start": lambda: HELP, "help": lambda: HELP, "logout": lambda: self.logout(chat_id),
            "status": self.status, "position": self.position, "pnl": self.pnl,
            "trades": lambda: self.trades(args), "config": lambda: self.show_config(args),
            "set": lambda: self.set_value(args), "pause": lambda: self.pause(True),
            "resume": lambda: self.pause(False), "squareoff": self.squareoff,
        }
        if cmd == "settoken":
            return self.set_token(args), True
        if cmd == "setkeys":
            return self.set_keys(args), True
        if cmd == "refreshtoken":
            return self.bot.refresh_token(), False
        if cmd not in fn:
            return "Unknown command. /help", False
        try:
            return fn[cmd](), False
        except Exception as e:  # keep the service alive on any command failure
            return f"Error: {e}", False

    # ── read-only ──────────────────────────────────────────────────
    def _tlog(self) -> TradeLog:
        b = self.bot
        if b.trade_log:
            return b.trade_log
        return TradeLog(b.log_dir, b.sess.now().date(), b.strategy.name, b.strategy.params,
                        b.cfg["paper"]["initial_capital_inr"])

    def position(self) -> str:
        b = self.bot
        with b.lock:
            p = b.trader.position
            if not p:
                return "No open position."
            ltp = b.last_option_ltp
            upl = b.trader.get_unrealized_pnl(ltp) if ltp is not None else None
            return (f"{p.signal.value} {p.contract.trading_symbol}\nEntered {p.entry_time:%Y-%m-%d %H:%M:%S}\n"
                    f"Entry {p.entry_option_ltp:.2f} | Last {ltp if ltp is not None else '-'}\n"
                    f"SL {p.sl_price if p.sl_price is None else round(p.sl_price, 2)} | "
                    f"TP {p.tp_price if p.tp_price is None else round(p.tp_price, 2)} | {p.lots} lot\n"
                    f"Unrealized P&L: {'-' if upl is None else f'Rs {upl:,.2f}'}")

    def status(self) -> str:
        b = self.bot
        with b.lock:
            now = b.sess.now()
            age = f"{int((now - b.last_good_tick).total_seconds())}s ago" if b.last_good_tick else "never"
            tl = self._tlog()
            return "\n".join([
                f"Time (IST): {now:%Y-%m-%d %H:%M:%S}",
                f"Market: {'OPEN' if b.sess.is_market_open(now) else 'CLOSED'} "
                f"({b.sess.open:%H:%M}-{b.sess.close:%H:%M} Mon-Fri)",
                f"Trading: {'PAUSED' if b.paused else 'ACTIVE'}",
                f"Groww API: {'AUTH FAILED' if b.auth_failed else 'ok'} | last tick {age}",
                f"Underlying: {b.fut_symbol or '-'} @ {b.last_underlying if b.last_underlying is not None else '-'}",
                f"Candles: {len(b.builder)}/{b.builder.warmup} warmup ({'warm' if b.builder.is_warm() else 'warming up'})",
                f"Last signal: {b.last_signal.value if b.last_signal else '-'}",
                f"Strategy: {b.strategy.name} {b.strategy.params}",
                f"Position: {'OPEN ' + b.trader.position.contract.trading_symbol if b.trader.position else 'none'}",
                f"Trades today: {len(tl.pnls)} | P&L today: Rs {tl.cumulative:,.2f}",
            ])

    def pnl(self) -> str:
        b = self.bot
        with b.lock:
            tl = self._tlog()
            m = tl.metrics()
            unreal = 0.0
            if b.trader.position and b.last_option_ltp is not None:
                unreal = b.trader.get_unrealized_pnl(b.last_option_ltp)
            pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
            return "\n".join([
                f"Trades: {m['total']} (W {m['wins']} / L {m['losses']}, win rate {m['win_rate']:.1f}%)",
                f"Realized P&L: Rs {m['net_pnl']:,.2f}",
                f"Unrealized P&L: Rs {unreal:,.2f}",
                f"Total today: Rs {m['net_pnl'] + unreal:,.2f}",
                f"Avg win Rs {m['avg_win']:,.2f} | Avg loss Rs {m['avg_loss']:,.2f} | PF {pf}",
                f"Max drawdown: Rs {m['max_drawdown']:,.2f}",
                f"Equity: Rs {m['final_equity'] + unreal:,.2f}",
            ])

    def trades(self, args: list[str]) -> str:
        n = int(args[0]) if args else 5
        path = self._tlog().path
        if not path.exists():
            return "No trades today."
        df = pd.read_csv(path).tail(max(1, min(n, 50)))
        return "\n".join(
            f"{str(r.entry_time)[11:19]}->{str(r.exit_time)[11:19]} {r.trading_symbol} "
            f"{r.entry_option_ltp:.2f}->{r.exit_option_ltp:.2f} {r.exit_reason} Rs {r.net_pnl_inr:,.2f}"
            for r in df.itertuples())

    def show_config(self, args: list[str]) -> str:
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        items = flatten(raw)
        prefix = args[0] if args else ""
        lines = [f"{k} = {'***' if k in SECRET_KEYS else v}" for k, v in items.items() if k.startswith(prefix)]
        return "\n".join(lines) or f"No settings match '{prefix}'. Sections: {', '.join(raw)}"

    # ── actions ────────────────────────────────────────────────────
    def pause(self, paused: bool) -> str:
        self.bot.set_paused(paused)
        return ("Trading PAUSED: no new entries. An open position is still managed (SL/TP/exit signals/square-off)."
                if paused else "Trading RESUMED.")

    def squareoff(self) -> str:
        b = self.bot
        with b.lock:
            if not b.trader.position:
                return "No open position."
            if not b.sess.is_market_open(b.sess.now()):
                return "Market is closed; position will be closed at the next session's first tick."
            b.squareoff_requested = True
        return "Square-off requested; closes on the next tick."

    def set_value(self, args: list[str]) -> str:
        if len(args) < 2:
            return "Usage: /set <key> <value>   (see /config for keys)"
        key, raw_value = args[0], " ".join(args[1:])
        if key in SECRET_KEYS:
            return "Credentials can only be changed with /settoken or /setkeys."
        if key.startswith(BLOCKED_PREFIXES):
            return f"{key} cannot be changed remotely."
        b = self.bot
        with b.lock:
            text = self.config_path.read_text(encoding="utf-8")
            try:
                old = get_leaf(yaml.safe_load(text), key)
            except KeyError:
                return f"Unknown setting '{key}'. Use /config to list keys."
            try:
                new = coerce(old, raw_value)
                cfg = parse_config(set_yaml_value(text, key, new), require_token=False)
                b.apply_config(cfg)
            except Exception as e:
                return f"Rejected: {e}"
            self.config_path.write_text(set_yaml_value(text, key, new), encoding="utf-8")
        note = "\nApplies to new trades; an open position keeps its SL/TP." if key.startswith("risk.") and b.trader.position else ""
        return f"{key}: {old} -> {new}{note}"

    def set_token(self, args: list[str]) -> str:
        if not args or len(args[0]) < 20:
            return "Usage: /settoken <token>"
        token = args[0]
        b = self.bot
        note = ""
        with b.lock:
            if b.fut_symbol:
                from core.groww_client import GrowwClient
                probe = GrowwClient(token, b.cfg["api"]["base_url"], b.cfg["api"]["version"], 0, 0, timeout=10) \
                    if isinstance(b.client, GrowwClient) else None
                if probe:
                    try:
                        probe.get_ltp([b.fut_symbol], b.segment)
                    except GrowwAuthError:
                        return "Token rejected by Groww (401/403). Not saved."
                    except Exception:
                        note = "\n(Could not verify the token right now; saved anyway.)"
            self._write_env("GROWW_ACCESS_TOKEN", token)
            os.environ["GROWW_ACCESS_TOKEN"] = token
            b.set_token(token)
            b.auth_failed = False
        return "Access token updated (stored in .env, live immediately)." + note

    def set_keys(self, args: list[str]) -> str:
        if len(args) != 2:
            return "Usage: /setkeys <api_key> <api_secret>"
        key, secret = args
        with self.bot.lock:
            self._write_env("GROWW_API_KEY", key)
            self._write_env("GROWW_API_SECRET", secret)
            os.environ["GROWW_API_KEY"], os.environ["GROWW_API_SECRET"] = key, secret
            result = self.bot.set_credentials(key, secret)
        return "API key and secret saved to .env.\n" + result

    def _write_env(self, key: str, value: str) -> None:
        lines = self.env_path.read_text(encoding="utf-8").splitlines() if self.env_path.exists() else []
        out, done = [], False
        for ln in lines:
            if ln.strip().startswith(f"{key}="):
                out.append(f"{key}={value}")
                done = True
            else:
                out.append(ln)
        if not done:
            out.append(f"{key}={value}")
        self.env_path.write_text("\n".join(out) + "\n", encoding="utf-8")

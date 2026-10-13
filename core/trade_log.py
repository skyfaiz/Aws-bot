import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

from .paper_trader import CompletedTrade

EXTRA = ["cumulative_pnl_inr", "session_date", "strategy_name", "strategy_params"]


class TradeLog:
    def __init__(self, log_dir: str | Path, session_date: date, strategy_name: str, strategy_params: dict,
                 initial_capital: float):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.date = session_date
        self.path = self.dir / f"trades_{session_date.isoformat()}.csv"
        self.strategy_name, self.strategy_params = strategy_name, strategy_params
        self.initial_capital = initial_capital
        self.pnls: list[float] = []
        if self.path.exists():
            self.pnls = pd.read_csv(self.path)["net_pnl_inr"].astype(float).tolist()

    @staticmethod
    def all_time_pnl(log_dir: str | Path) -> float:
        total = 0.0
        for f in Path(log_dir).glob("trades_*.csv"):
            total += float(pd.read_csv(f)["net_pnl_inr"].sum())
        return total

    @property
    def cumulative(self) -> float:
        return float(sum(self.pnls))

    def record(self, t: CompletedTrade) -> None:
        self.pnls.append(t.net_pnl_inr)
        row = asdict(t)
        row.update(cumulative_pnl_inr=round(self.cumulative, 2), session_date=self.date.isoformat(),
                   strategy_name=self.strategy_name, strategy_params=json.dumps(self.strategy_params))
        pd.DataFrame([row]).to_csv(self.path, mode="a", header=not self.path.exists(), index=False)

    def metrics(self) -> dict:
        p = self.pnls
        wins, losses = [x for x in p if x > 0], [x for x in p if x < 0]
        peak = dd = cum = 0.0
        for x in p:
            cum += x
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        gl = abs(sum(losses))
        return {
            "total": len(p), "wins": len(wins), "losses": len(losses),
            "win_rate": 100.0 * len(wins) / len(p) if p else 0.0,
            "avg_win": sum(wins) / len(wins) if wins else 0.0,
            "avg_loss": sum(losses) / len(losses) if losses else 0.0,
            "profit_factor": (sum(wins) / gl) if gl else (float("inf") if wins else 0.0),
            "net_pnl": cum, "max_drawdown": dd,
            "largest_win": max(wins, default=0.0), "largest_loss": min(losses, default=0.0),
            "final_equity": self.initial_capital + cum,
        }

    def summary(self) -> str:
        m = self.metrics()
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        bar = "=" * 47
        return "\n".join([
            bar, "  PAPER TRADING SESSION SUMMARY", f"  Date          : {self.date}",
            f"  Strategy      : {self.strategy_name} {self.strategy_params}", "-" * 47,
            f"  Total trades      : {m['total']}",
            f"  Winners           : {m['wins']}  ({m['win_rate']:.1f}%)",
            f"  Losers            : {m['losses']}",
            f"  Avg win           : Rs {m['avg_win']:,.2f}", f"  Avg loss          : Rs {m['avg_loss']:,.2f}",
            f"  Profit factor     : {pf}", f"  Net P&L (session) : Rs {m['net_pnl']:,.2f}",
            f"  Max drawdown      : Rs {m['max_drawdown']:,.2f}",
            f"  Largest win/loss  : Rs {m['largest_win']:,.2f} / Rs {m['largest_loss']:,.2f}",
            f"  Initial capital   : Rs {self.initial_capital:,.2f}", f"  Final equity      : Rs {m['final_equity']:,.2f}", bar,
        ])

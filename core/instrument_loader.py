from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass
class OptionContract:
    trading_symbol: str
    groww_symbol: str
    exchange: str
    segment: str
    option_type: str
    strike: float
    expiry_date: date
    lot_size: int
    exchange_token: int


class InstrumentLoader:
    def __init__(self, exchange="MCX", underlying="CRUDEOIL", segment="COMMODITY"):
        self.exchange, self.underlying, self.segment = exchange, underlying, segment
        self.df = pd.DataFrame()

    def load(self, csv_path: str, today: date | None = None) -> "InstrumentLoader":
        today = today or date.today()
        df = pd.read_csv(csv_path, low_memory=False)
        df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce").dt.date
        for col in ("buy_allowed", "sell_allowed"):
            if col in df:
                df = df[df[col].fillna(1).astype(int) == 1]
        df = df[
            (df["exchange"] == self.exchange)
            & (df["underlying_symbol"] == self.underlying)
            & (df["instrument_type"].isin(["FUT", "CE", "PE"]))
            & (df["expiry_date"].notna())
            & (df["expiry_date"] >= today)
        ].copy()
        if "segment" in df:
            df = df[df["segment"] == self.segment]
        df["strike_price"] = pd.to_numeric(df["strike_price"], errors="coerce").fillna(0.0)
        # Groww LTP API wants EXCHANGE_TRADINGSYMBOL (e.g. MCX_CRUDEOIL19OCT26FUT); the CSV's
        # groww_symbol column uses a different dashed format (MCX-CRUDEOIL-19Oct26-FUT).
        df["api_symbol"] = df["exchange"] + "_" + df["trading_symbol"]
        self.df = df.reset_index(drop=True)
        if self.df.empty:
            raise ValueError(f"No {self.exchange} {self.underlying} instruments found in {csv_path} for {today}")
        self._opts = {
            (r.instrument_type, r.expiry_date, float(r.strike_price)): r
            for r in self.df[self.df["instrument_type"] != "FUT"].itertuples()
        }
        return self

    def list_expiries(self, instrument_type: str, min_date: date | None = None) -> list[date]:
        sub = self.df[self.df["instrument_type"] == instrument_type]
        return sorted(e for e in set(sub["expiry_date"]) if min_date is None or e >= min_date)

    def get_underlying_fut(self, expiry: str = "nearest", today: date | None = None):
        today = today or date.today()
        exps = self.list_expiries("FUT", today)
        if not exps:
            raise ValueError("No active FUT contracts")
        idx = 1 if expiry == "next" and len(exps) > 1 else 0
        return self.df[(self.df["instrument_type"] == "FUT") & (self.df["expiry_date"] == exps[idx])].iloc[0]

    def strikes(self, option_type: str, expiry: date) -> list[float]:
        return sorted(k[2] for k in self._opts if k[0] == option_type and k[1] == expiry)

    def get_option(self, option_type: str, expiry: date, strike: float) -> OptionContract | None:
        r = self._opts.get((option_type, expiry, float(strike)))
        if r is None:
            return None
        return OptionContract(
            trading_symbol=r.trading_symbol, groww_symbol=r.api_symbol, exchange=self.exchange,
            segment=self.segment, option_type=option_type, strike=float(strike), expiry_date=expiry,
            lot_size=int(r.lot_size), exchange_token=int(pd.to_numeric(getattr(r, "exchange_token", 0), errors="coerce") or 0),
        )

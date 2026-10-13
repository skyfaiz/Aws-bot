import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FUT_SYMBOL = "MCX_CRUDEOIL19OCT26FUT"


def make_instruments(path: Path) -> None:
    rows = []

    def add(ts, itype, expiry, strike, lot=100, underlying="CRUDEOIL", allowed=1):
        rows.append(dict(exchange="MCX", exchange_token=len(rows) + 1, trading_symbol=ts, groww_symbol=f"MCX-{ts}-dashed",
                         instrument_type=itype, segment="COMMODITY", underlying_symbol=underlying,
                         expiry_date=expiry, strike_price=strike, lot_size=lot, tick_size=10.0,
                         buy_allowed=allowed, sell_allowed=allowed))

    add("CRUDEOIL19OCT26FUT", "FUT", "2026-10-19", 0)
    add("CRUDEOIL19NOV26FUT", "FUT", "2026-11-19", 0)
    add("CRUDEOILM19OCT26FUT", "FUT", "2026-10-19", 0, lot=10, underlying="CRUDEOILM")
    add("CRUDEOIL19SEP26FUT", "FUT", "2026-09-19", 0)  # expired
    for expiry, tag in (("2026-10-14", "14OCT26"), ("2026-11-17", "17NOV26")):
        for strike in range(6000, 7050, 50):
            for t in ("CE", "PE"):
                add(f"CRUDEOIL{tag}{strike}{t}", t, expiry, strike)
    rows.append({**rows[-1], "trading_symbol": "CRUDEOIL17NOV266500CE_BLOCKED", "groww_symbol": "MCX_BLOCKED",
                 "buy_allowed": 0, "sell_allowed": 0})
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.fixture
def base_dir(tmp_path):
    make_instruments(tmp_path / "instrument.csv")
    return tmp_path


@pytest.fixture
def cfg(base_dir):
    c = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    c["api"]["access_token"] = "test-token"
    c["risk"]["stop_loss"]["value"] = 30.0     # tests do not depend on the tuned default
    c["candles"]["warmup_candles"] = 5
    c["candles"]["max_window"] = 50
    c["strategy"]["params"] = {"amplitude": 2, "channel_deviation": 2.0, "atr_period": 10}
    return c

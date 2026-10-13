import os
import re
from pathlib import Path

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SL_MODES = {"none", "fixed_inr", "fixed_pct", "atr"}
TP_MODES = SL_MODES | {"reward_risk"}


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _expand(obj):
    if isinstance(obj, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    return obj


def validate(cfg: dict, require_token: bool = True) -> None:
    if require_token and not cfg["api"].get("access_token"):
        raise ValueError("GROWW_ACCESS_TOKEN is not set (env var or .env file)")
    if cfg["api"]["poll_interval_seconds"] <= 0:
        raise ValueError("api.poll_interval_seconds must be > 0")
    ins = cfg["instrument"]
    if ins["expiry_selection"] not in ("nearest", "next", "specific"):
        raise ValueError("instrument.expiry_selection must be nearest|next|specific")
    if ins["expiry_selection"] == "specific" and not ins.get("specific_expiry"):
        raise ValueError("instrument.specific_expiry required when expiry_selection=specific")
    if ins["strike_selection"] not in ("ATM", "1_OTM", "2_OTM", "1_ITM"):
        raise ValueError("instrument.strike_selection must be ATM|1_OTM|2_OTM|1_ITM")
    if int(ins["lots"]) < 1:
        raise ValueError("instrument.lots must be >= 1")
    if not 0 < float(cfg["risk"]["max_capital_pct"]) <= 100:
        raise ValueError("risk.max_capital_pct must be > 0 and <= 100")
    if int(cfg["data"]["retention_days"]) < 7:
        raise ValueError("data.retention_days must be >= 7")
    sl, tp = cfg["risk"]["stop_loss"], cfg["risk"]["take_profit"]
    if sl["mode"] not in SL_MODES:
        raise ValueError(f"risk.stop_loss.mode must be one of {sorted(SL_MODES)}")
    if tp["mode"] not in TP_MODES:
        raise ValueError(f"risk.take_profit.mode must be one of {sorted(TP_MODES)}")
    if tp["mode"] == "reward_risk" and sl["mode"] == "none":
        raise ValueError("take_profit.mode=reward_risk requires a stop_loss mode other than none")
    c = cfg["candles"]
    if c["interval_minutes"] < 1 or c["warmup_candles"] < 1 or c["max_window"] < c["warmup_candles"]:
        raise ValueError("invalid candles config (max_window must be >= warmup_candles)")
    s = cfg["session"]
    if not (s["market_open"] < s["trade_only_between"]["start"] < s["trade_only_between"]["end"]
            <= s["auto_squareoff_time"] <= s["market_close"]):
        raise ValueError("session times must satisfy open < trade start < trade end <= squareoff <= close")


def parse_config(text: str, require_token: bool = True) -> dict:
    cfg = _expand(yaml.safe_load(text))
    validate(cfg, require_token)
    return cfg


def load_config(path: str | Path = "config/config.yaml", require_token: bool = True) -> dict:
    return parse_config(Path(path).read_text(encoding="utf-8"), require_token)

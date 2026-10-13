from datetime import date, datetime

from strategies.base import Signal
from .instrument_loader import InstrumentLoader, OptionContract

_OFFSETS = {"ATM": 0, "1_OTM": 1, "2_OTM": 2, "1_ITM": -1}  # in strike steps, from the buyer's perspective


class OptionSelector:
    def __init__(self, loader: InstrumentLoader, cfg: dict):
        self.loader, self.cfg = loader, cfg
        self.step = float(cfg.get("strike_interval", 50))

    def pick_expiry(self, option_type: str, today: date) -> date:
        min_date = today.fromordinal(today.toordinal() + int(self.cfg.get("min_days_to_expiry", 1)))
        exps = self.loader.list_expiries(option_type, min_date)
        if not exps:
            raise ValueError(f"No {option_type} expiries available on/after {min_date}")
        sel = self.cfg["expiry_selection"]
        if sel == "specific":
            want = datetime.strptime(str(self.cfg["specific_expiry"]), "%Y-%m-%d").date()
            if want not in exps:
                raise ValueError(f"Specific expiry {want} not available: {exps}")
            return want
        return exps[1] if sel == "next" and len(exps) > 1 else exps[0]

    def select(self, signal: Signal, underlying_ltp: float, today: date) -> OptionContract:
        if signal == Signal.BUY:
            otype = self.cfg["option_type_on_buy"]
        elif signal == Signal.SELL:
            otype = self.cfg["option_type_on_sell"]
        else:
            raise ValueError(f"Cannot select option for {signal}")
        expiry = self.pick_expiry(otype, today)
        atm = round(underlying_ltp / self.step) * self.step
        n = _OFFSETS[self.cfg["strike_selection"]]
        # OTM CE = higher strike, OTM PE = lower strike
        direction = 1 if otype == "CE" else -1
        strikes = self.loader.strikes(otype, expiry)
        if not strikes:
            raise ValueError(f"No strikes for {otype} {expiry}")
        k = n
        while True:
            strike = atm + direction * k * self.step
            c = self.loader.get_option(otype, expiry, strike)
            if c:
                return c
            if k == 0:
                break
            k += -1 if k > 0 else 1
        nearest = min(strikes, key=lambda s: abs(s - atm))
        return self.loader.get_option(otype, expiry, nearest)

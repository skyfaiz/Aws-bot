import pandas as pd

from halftrend import calculate_halftrend
from .base import BaseStrategy, Signal


class HalfTrendStrategy(BaseStrategy):
    def __init__(self, amplitude: int = 2, channel_deviation: float = 2.0, atr_period: int = 100):
        self._amplitude = int(amplitude)
        self._dev = float(channel_deviation)
        self._atr_period = int(atr_period)
        self.last_atr = 0.0

    @property
    def name(self) -> str:
        return "halftrend"

    @property
    def params(self) -> dict:
        return {"amplitude": self._amplitude, "channel_deviation": self._dev, "atr_period": self._atr_period}

    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        df = calculate_halftrend(candles, self._amplitude, self._dev, self._atr_period)
        last = df.iloc[-1]
        self.last_atr = float(last["ATR"])
        if bool(last["Buy"]):
            return Signal.BUY
        if bool(last["Sell"]):
            return Signal.SELL
        return Signal.HOLD

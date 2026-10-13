from abc import ABC, abstractmethod
from enum import Enum

import pandas as pd


class Signal(Enum):
    BUY = "BUY"    # open CE
    SELL = "SELL"  # open PE
    EXIT = "EXIT"  # close any open position
    HOLD = "HOLD"


class BaseStrategy(ABC):
    """candles: DataFrame(Datetime, Open, High, Low, Close, Volume), chronological, last row = latest completed candle."""

    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame) -> Signal: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def params(self) -> dict: ...

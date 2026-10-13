"""HalfTrend indicator translated from the supplied TradingView script."""
from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_halftrend(
    candles: pd.DataFrame,
    amplitude: int = 2,
    channel_deviation: float = 2.0,
    atr_period: int = 100,
) -> pd.DataFrame:
    """Return candles plus HalfTrend, channel, and buy/sell signal columns."""
    if amplitude < 1 or atr_period < 1:
        raise ValueError("amplitude and atr_period must be at least 1")
    required = {"Open", "High", "Low", "Close"}
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"Missing candle columns: {sorted(missing)}")

    df = candles.copy().reset_index(drop=True)
    high, low, close = (df[col].astype(float) for col in ("High", "Low", "Close"))
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    atr = true_range.ewm(alpha=1 / atr_period, adjust=False, min_periods=1).mean()
    atr2 = atr / 2.0
    dev = channel_deviation * atr2

    high_price = high.rolling(amplitude, min_periods=1).max()
    low_price = low.rolling(amplitude, min_periods=1).min()
    highma = high.rolling(amplitude, min_periods=amplitude).mean()
    lowma = low.rolling(amplitude, min_periods=amplitude).mean()

    trend = np.zeros(len(df), dtype=int)
    next_trend = np.zeros(len(df), dtype=int)
    max_low = np.zeros(len(df), dtype=float)
    min_high = np.zeros(len(df), dtype=float)
    up = np.full(len(df), np.nan)
    down = np.full(len(df), np.nan)
    atr_high = np.full(len(df), np.nan)
    atr_low = np.full(len(df), np.nan)
    buy_signal = np.zeros(len(df), dtype=bool)
    sell_signal = np.zeros(len(df), dtype=bool)

    max_low[0] = low.iloc[0]
    min_high[0] = high.iloc[0]
    for i in range(len(df)):
        if i:
            trend[i] = trend[i - 1]
            next_trend[i] = next_trend[i - 1]
            max_low[i] = max_low[i - 1]
            min_high[i] = min_high[i - 1]

        if next_trend[i] == 1:
            max_low[i] = max(low_price.iloc[i], max_low[i])

        previous_low = low.iloc[i - 1] if i else low.iloc[i]
        previous_high = high.iloc[i - 1] if i else high.iloc[i]
        if highma.iloc[i] < max_low[i] and close.iloc[i] < previous_low:
            trend[i] = 1
            next_trend[i] = 0
            min_high[i] = high_price.iloc[i]
        else:
            min_high[i] = min(high_price.iloc[i], min_high[i])

        if lowma.iloc[i] > min_high[i] and close.iloc[i] > previous_high:
            trend[i] = 0
            next_trend[i] = 1
            max_low[i] = low_price.iloc[i]

        if trend[i] == 0:
            if i and trend[i - 1] != 0:
                up[i] = down[i - 1] if not np.isnan(down[i - 1]) else down[i]
                buy_signal[i] = True
            else:
                up[i] = max_low[i] if i == 0 or np.isnan(up[i - 1]) else max(max_low[i], up[i - 1])
            atr_high[i] = up[i] + dev.iloc[i]
            atr_low[i] = up[i] - dev.iloc[i]
        else:
            if i and trend[i - 1] != 1:
                down[i] = up[i - 1] if not np.isnan(up[i - 1]) else up[i]
                sell_signal[i] = True
            else:
                down[i] = min_high[i] if i == 0 or np.isnan(down[i - 1]) else min(min_high[i], down[i - 1])
            atr_high[i] = down[i] + dev.iloc[i]
            atr_low[i] = down[i] - dev.iloc[i]

    df["ATR"] = atr
    df["HalfTrend"] = np.where(trend == 0, up, down)
    df["ATR_High"] = atr_high
    df["ATR_Low"] = atr_low
    df["Trend"] = trend
    df["Buy"] = buy_signal
    df["Sell"] = sell_signal
    return df

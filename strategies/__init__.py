from .base import BaseStrategy, Signal
from .halftrend import HalfTrendStrategy

STRATEGY_REGISTRY = {
    "halftrend": HalfTrendStrategy,
}


def load_strategy(name: str, params: dict) -> BaseStrategy:
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy: {name!r}. Available: {list(STRATEGY_REGISTRY)}")
    return STRATEGY_REGISTRY[name](**(params or {}))

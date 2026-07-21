from collections import deque
from dataclasses import dataclass

import pandas as pd
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.indicators import Indicator
from nautilus_trader.model.data import Bar

from src.helpers.bar import maxmin_price


@dataclass
class LevelInfo:
    price: float
    volume: float
    datetime: pd.Timestamp


@dataclass
class HighLowsIndicator(Indicator):
    # indicator config
    period: PositiveInt
    use_wicks: bool
    history_length: int

    def __init__(self, period: int, use_wicks: bool = True, history_length: int = 100):
        PyCondition.positive_int(period, "period")
        super().__init__(params=[period, use_wicks, history_length])

        self.period = period
        self.use_wicks = use_wicks
        self.history_length = history_length

        self.upper: deque[LevelInfo] = deque(maxlen=history_length)
        self.lower: deque[LevelInfo] = deque(maxlen=history_length)

        self.last_bars: deque[Bar] = deque(maxlen=period)

    def handle_bar(self, bar: Bar) -> None:
        high, low = maxmin_price(bar, use_wicks=self.use_wicks)

        # clear upper and lower if the new bar is outside of the current range
        while self.upper and self.upper[0].price < high:
            self.upper.popleft()
        while self.lower and self.lower[0].price > low:
            self.lower.popleft()

        # check if the new bar is a new high or low compared to the last bars
        is_higher = True
        is_lower = True
        for b in self.last_bars:
            hi, lo = maxmin_price(b, use_wicks=self.use_wicks)
            if hi > high:
                is_higher = False
            if lo < low:
                is_lower = False

        if is_higher:
            self.upper.appendleft(
                LevelInfo(price=high, volume=bar.volume, datetime=unix_nanos_to_dt(bar.ts_event))
            )

        if is_lower:
            self.lower.appendleft(
                LevelInfo(price=low, volume=bar.volume, datetime=unix_nanos_to_dt(bar.ts_event))
            )

        self.last_bars.appendleft(bar)

    def get_upper_by_price(self, price: float) -> float | None:
        for upper in self.upper:
            if upper.price > price:
                return upper.price
        return None

    def get_lower_by_price(self, price: float) -> float | None:
        for lower in self.lower:
            if lower.price < price:
                return lower.price
        return None

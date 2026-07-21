import math
from collections import deque
from dataclasses import dataclass

from nautilus_trader.common.config import PositiveInt
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.indicators import Indicator
from nautilus_trader.model.data import Bar

from src.helpers.bar import maxmin_price


@dataclass
class ChocIndicator(Indicator):
    # indicator config
    period: PositiveInt
    use_wicks: bool
    min_bos_perc_diff: float = 0

    def __init__(self, period: int, use_wicks: bool = True, min_bos_perc_diff: float = 0):
        PyCondition.positive_int(period, "period")
        super().__init__(params=[period, use_wicks])

        self.period = period
        self.use_wicks = use_wicks
        self.min_bos_perc_diff = min_bos_perc_diff

        self.direction: int = 0
        self.empty_price_diff_before_bos: float = 0.0002

        # globex data
        self.globex_peak: float | None = None
        self.globex_peak_duration: int | None = None

        self.globex_h: float = -math.inf
        self.globex_h_duration: int = -1

        self.globex_l: float = math.inf
        self.globex_l_duration: int = -1

        # BoS data
        self.bos: deque[float] = deque(maxlen=100)
        self.bos_duration: deque[int] = deque(maxlen=100)
        self.bos_triggered: bool = False

        # CHoC data
        self.choc: float | None = None
        self.choc_duration: int | None = None
        self.choc_triggered: bool = False
        self.choc_triggered_duration: int | None = None
        self.choc_triggered_price: float | None = None

        self._bars: deque[Bar] = deque(maxlen=10000)
        self._exists_empty_price_before_bos: bool = False

    @property
    def global_diff(self) -> float:
        return (
            self.globex_h - self.globex_l
            if self.globex_h != -math.inf and self.globex_l != math.inf
            else 0
        )

    def handle_bar(self, bar: Bar) -> None:
        self.__update_counters()
        self._bars.appendleft(bar)
        # only trigger choc once per bar, reset on next bar
        if self.choc_triggered:
            self.choc_triggered = False
        if self.bos_triggered:
            self.bos_triggered = False
        if self.direction > 0:
            self.__handle_buyer_bos(bar)
        elif self.direction < 0:
            self.__handle_seller_bos(bar)
        else:
            # init first time
            high, low = maxmin_price(bar, use_wicks=self.use_wicks)
            self.direction = 1 if bar.open < bar.close else -1
            self.globex_h = high
            self.globex_l = low
            self.globex_h_duration = 0
            self.globex_l_duration = 0
            self.choc = self.globex_l if self.direction > 0 else self.globex_h
            self.choc_duration = 0

    def _reset(self) -> None:
        self._set_initialized(False)
        self.direction = 0
        self.globex_peak = None
        self.globex_peak_duration = None
        self.globex_h = -math.inf
        self.globex_h_duration = -1
        self.globex_l = math.inf
        self.globex_l_duration = -1
        self.bos.clear()
        self.bos_duration.clear()
        self.bos_triggered = False
        self.choc = None
        self.choc_duration = None
        self.choc_triggered = False
        self.choc_triggered_duration = None
        self.choc_triggered_price = None
        self._bars.clear()
        self._exists_empty_price_before_bos = False

    def update_state_from_indicator(self, other: ChocIndicator) -> None:
        # globex data
        self.globex_h = other.globex_h
        self.globex_l = other.globex_l
        self.globex_h_duration = other.globex_h_duration
        self.globex_l_duration = other.globex_l_duration
        # bos data
        self.bos = other.bos.copy()
        self.bos_duration = other.bos_duration.copy()
        # choc
        self.choc = other.choc
        self.choc_duration = other.choc_duration
        self.direction = other.direction

    def __update_counters(self):
        self.globex_h_duration += 1
        self.globex_l_duration += 1
        for i in range(len(self.bos_duration)):
            self.bos_duration[i] = self.bos_duration[i] + 1
        self.choc_duration = self.choc_duration + 1 if self.choc_duration is not None else None
        self.choc_triggered_duration = (
            self.choc_triggered_duration + 1 if self.choc_triggered_duration is not None else None
        )
        self.globex_peak_duration = (
            self.globex_peak_duration + 1 if self.globex_peak_duration is not None else None
        )

    def __handle_buyer_bos(self, bar: Bar):
        high, low = maxmin_price(bar, use_wicks=self.use_wicks)
        global_diff = self.global_diff

        # Check if there is an empty price before BOS (always take account wicks)
        if not self._exists_empty_price_before_bos and bar.high < (
            self.globex_h * (1 - self.empty_price_diff_before_bos)
        ):
            self._exists_empty_price_before_bos = True

        # Check if the bar closes above the globex high, indicating a potential BoS
        max_globex_price = self.globex_h + global_diff * self.min_bos_perc_diff
        if bar.close > max_globex_price:
            if self.globex_h_duration > self.period and self._exists_empty_price_before_bos:
                # triggered new bos
                self._set_initialized(True)
                self.bos_triggered = True
                self.choc = self.globex_l
                self.choc_duration = self.globex_l_duration
                self.bos.appendleft(self.globex_h)
                self.bos_duration.appendleft(self.globex_h_duration)
                self.globex_h = bar.close
                self.globex_l = bar.close
                self.globex_h_duration = -1  # start on next candle
                self.globex_l_duration = -1  # start on next candle
                self._exists_empty_price_before_bos = False
            else:
                # restart
                self.globex_h = high
                self.globex_h_duration = 0
                self.globex_l = math.inf
                self.globex_l_duration = 0
                self._exists_empty_price_before_bos = False
                self._bars.clear()
                self._bars.appendleft(bar)
        # Check if the bar closes below the choc level, indicating a potential CHoC
        elif bar.close < (self.choc - global_diff * self.min_bos_perc_diff):
            self.choc_triggered = True
            self.choc_triggered_price = self.choc
            self.choc_triggered_duration = self.choc_duration
            self.globex_peak = self.globex_h
            self.globex_peak_duration = self.globex_h_duration
            indicator = ChocIndicator(
                period=self.period,
                use_wicks=self.use_wicks,
                min_bos_perc_diff=self.min_bos_perc_diff,
            )
            first_bar = self._bars[self.globex_h_duration]
            start = (
                self.globex_h_duration
                if first_bar.open > first_bar.close
                else self.globex_h_duration - 1
            )
            for i in range(start, -1, -1):
                b = self._bars[i]
                indicator.handle_bar(b)
            self.update_state_from_indicator(indicator)
        elif low < self.globex_l:
            self.globex_l = low
            self.globex_l_duration = 0

    def __handle_seller_bos(self, bar: Bar):
        high, low = maxmin_price(bar, use_wicks=self.use_wicks)
        global_diff = self.global_diff

        # Check if there is an empty price before BOS (always take account wicks)
        if not self._exists_empty_price_before_bos and bar.low > (
            self.globex_l * (1 + self.empty_price_diff_before_bos)
        ):
            self._exists_empty_price_before_bos = True

        # Check if the bar closes below the globex low, indicating a potential BoS
        min_globex_price = self.globex_l - global_diff * self.min_bos_perc_diff
        if bar.close < min_globex_price:
            if self.globex_l_duration > self.period and self._exists_empty_price_before_bos:
                # triggered new bos
                self.bos_triggered = True
                self.choc = self.globex_h
                self.choc_duration = self.globex_h_duration
                self.bos.appendleft(self.globex_l)
                self.bos_duration.appendleft(self.globex_l_duration)
                self.globex_h = bar.close
                self.globex_l = bar.close
                self.globex_h_duration = -1  # start on next candle
                self.globex_l_duration = -1  # start on next candle
                self._exists_empty_price_before_bos = False
            else:
                # restart
                self.globex_h = -math.inf
                self.globex_h_duration = 0
                self.globex_l = low
                self.globex_l_duration = 0
                self._exists_empty_price_before_bos = False
                self._bars.clear()
                self._bars.appendleft(bar)
        # Check if the bar closes above the choc level, indicating a potential CHoC
        elif self.choc and bar.close > (self.choc + global_diff * self.min_bos_perc_diff):
            self.choc_triggered = True
            self.choc_triggered_price = self.choc
            self.choc_triggered_duration = self.choc_duration
            self.globex_peak = self.globex_l
            self.globex_peak_duration = self.globex_l_duration
            indicator = ChocIndicator(
                period=self.period,
                use_wicks=self.use_wicks,
                min_bos_perc_diff=self.min_bos_perc_diff,
            )
            first_bar = self._bars[self.globex_l_duration]
            start = (
                self.globex_l_duration
                if first_bar.open < first_bar.close
                else self.globex_l_duration - 1
            )
            for i in range(start, -1, -1):
                b = self._bars[i]
                indicator.handle_bar(b)
            self.update_state_from_indicator(indicator)
        else:
            if high > self.globex_h:
                self.globex_h = high
                self.globex_h_duration = -1

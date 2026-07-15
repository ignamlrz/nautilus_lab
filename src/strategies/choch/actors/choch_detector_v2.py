import math
from collections import deque
from dataclasses import dataclass
from dataclasses import field

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.core import UUID4
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from src.helpers.bar import maxmin_price
from src.strategies.choch.enums import BosEvent
from src.strategies.choch.events import BosLine
from src.strategies.choch.events import BosPerfectPattern
from src.strategies.choch.events import BosWithoutMssPattern
from src.strategies.choch.events import ChocLine


@dataclass
class BosIndicator:
    # indicator config
    period: PositiveInt
    use_wicks: bool
    direction: int = 0
    empty_price_diff_before_bos: float = 0.0002

    # globex data
    globex_peak: float | None = None
    globex_peak_duration: int | None = None

    globex_h: float = -math.inf
    globex_h_duration: int = -1

    globex_l: float = math.inf
    globex_l_duration: int = -1

    # BoS data
    bos: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    bos_duration: deque[int] = field(default_factory=lambda: deque(maxlen=100))
    bos_triggered: bool = False

    # CHoC data
    choc: float | None = None
    choc_duration: int | None = None
    choc_triggered: bool = False
    choc_triggered_duration: int | None = None
    choc_triggered_price: float | None = None

    _bars: deque[Bar] = field(default_factory=lambda: deque(maxlen=1000))
    _exists_empty_price_before_bos: bool = False

    @classmethod
    def create(cls, period: PositiveInt, use_wicks: bool = True) -> BosIndicator:
        return cls(period=period, use_wicks=use_wicks)

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

    def reset(self) -> None:
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

    def update_state_from_indicator(self, other: BosIndicator) -> None:
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

    def __handle_buyer_bos(self, bar: Bar) -> BosEvent:
        high, low = maxmin_price(bar, use_wicks=self.use_wicks)

        # Check if there is an empty price before BOS (always take account wicks)
        if not self._exists_empty_price_before_bos and bar.high < (
            self.globex_h * (1 - self.empty_price_diff_before_bos)
        ):
            self._exists_empty_price_before_bos = True

        # Check if the bar closes above the globex high, indicating a potential BoS
        if bar.close > self.globex_h:
            if self.globex_h_duration > self.period and self._exists_empty_price_before_bos:
                # triggered new bos
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
        elif bar.close < self.choc:
            self.choc_triggered = True
            self.choc_triggered_price = self.choc
            self.choc_triggered_duration = self.choc_duration
            self.globex_peak = self.globex_h
            self.globex_peak_duration = self.globex_h_duration
            indicator = BosIndicator.create(period=self.period, use_wicks=self.use_wicks)
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

    def __handle_seller_bos(self, bar: Bar) -> BosEvent:
        high, low = maxmin_price(bar, use_wicks=self.use_wicks)

        # Check if there is an empty price before BOS (always take account wicks)
        if not self._exists_empty_price_before_bos and bar.low > (
            self.globex_l * (1 + self.empty_price_diff_before_bos)
        ):
            self._exists_empty_price_before_bos = True

        # Check if the bar closes below the globex low, indicating a potential BoS
        if bar.close < self.globex_l:
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
        elif self.choc and bar.close > self.choc:
            self.choc_triggered = True
            self.choc_triggered_price = self.choc
            self.choc_triggered_duration = self.choc_duration
            self.globex_peak = self.globex_l
            self.globex_peak_duration = self.globex_l_duration
            indicator = BosIndicator.create(period=self.period, use_wicks=self.use_wicks)
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


class ChangeOfCharacterDetectorV2Config(ActorConfig, frozen=True):
    """
    Configuration for ``ChangeOfCharacterDetector`` instances.
    """

    instrument_ids: list[InstrumentId]
    bar_type_spec: str = "1-MINUTE-LAST-EXTERNAL"
    client_id: ClientId | None = None
    log_data: bool = True
    use_wicks: bool = False
    bos_period: PositiveInt = 2
    max_box_duration: PositiveInt = 100


class ChangeOfCharacterDetectorV2(Actor):
    config: ChangeOfCharacterDetectorV2Config

    def __init__(self, config: ChangeOfCharacterDetectorV2Config) -> None:
        super().__init__(config)

        self._bos: dict[InstrumentId, BosIndicator] = {}
        self._is_historical = False

    def on_start(self) -> None:
        client_id = self.config.client_id
        requests_start = self.clock.utc_now() - pd.Timedelta(minutes=1440 * 3)

        if "INTERNAL" in self.config.bar_type_spec:
            aggregated_bar_types = set()
            for instrument_id in self.config.instrument_ids or []:
                bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
                self._bos[instrument_id] = BosIndicator(
                    period=self.config.bos_period, use_wicks=self.config.use_wicks
                )
                aggregated_bar_types.add(bar_type)

            self.request_aggregated_bars(
                bar_types=list(aggregated_bar_types),
                start=requests_start,
                client_id=client_id,
                update_subscriptions=True,
                callback=self.on_start_finished,
            )
        else:
            uuids: tuple[UUID4] = ()
            for instrument_id in self.config.instrument_ids or []:
                bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
                self._bos[instrument_id] = BosIndicator(
                    period=self.config.bos_period, use_wicks=self.config.use_wicks
                )

                uuid = UUID4()
                self.request_bars(
                    bar_type=bar_type,
                    start=requests_start,
                    client_id=client_id,
                    request_id=uuid,
                    join_request=True,
                )
                uuids += (uuid,)

            if uuids:
                self.request_join(
                    request_ids=uuids,
                    start=requests_start,
                    client_id=client_id,
                    callback=self.on_start_finished,
                )

    def on_start_finished(self, uuid: UUID4) -> None:
        for instrument_id in self.config.instrument_ids or []:
            bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
            self.subscribe_bars(bar_type=bar_type, client_id=self.config.client_id)

    def on_stop(self) -> None:
        client_id = self.config.client_id

        for instrument_id in self.config.instrument_ids or []:
            bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
            self.unsubscribe_bars(bar_type=bar_type, client_id=client_id)

    def on_historical_data(self, data):
        if isinstance(data, Bar):
            self.on_bar(data)

    def on_bar(self, bar: Bar) -> None:
        instrument_id = bar.bar_type.instrument_id
        if instrument_id not in self._bos:
            return
        bos = self._bos.get(instrument_id)
        bos.handle_bar(bar)

        if bos.choc_triggered:
            bar_prev = self.cache.bar(bar.bar_type, bos.choc_triggered_duration)
            data = ChocLine(
                instrument_id=instrument_id,
                open_datetime=bar_prev.ts_event,
                close_datetime=bar.ts_event,
                price=bos.choc_triggered_price,
                color="#C3DB38",
            )
            self.publish_data(DataType(ChocLine), data)

            if bos.bos_duration:
                bar_mss = self.cache.bar(bar.bar_type, bos.bos_duration[-1])
                data = BosLine(
                    instrument_id=instrument_id,
                    open_datetime=bar_mss.ts_event,
                    close_datetime=bar.ts_event,
                    price=bos.bos[-1],
                    color="#E933DA",
                )
                self.publish_data(DataType(BosLine), data)

        if bos.bos_triggered:
            bar_prev = self.cache.bar(bar.bar_type, bos.bos_duration[0])
            data = BosLine(
                instrument_id=instrument_id,
                open_datetime=bar_prev.ts_event,
                close_datetime=bar.ts_event,
                price=bos.bos[0],
                color="#0EB624" if bos.direction > 0 else "#E23030",
            )
            self.publish_data(DataType(BosLine), data)

            if len(bos.bos) == 1 and bos.choc_triggered_price:
                data = BosWithoutMssPattern(
                    instrument_id=instrument_id,
                    datetime=bar.ts_event,
                    color="#BFF347" if bos.direction > 0 else "#BFF34777",
                )
                self.publish_data(DataType(BosWithoutMssPattern), data)

            if len(bos.bos) == 2:
                mss = bos.bos[1]
                bos1 = bos.bos[0]
                choc = bos.choc_triggered_price
                if not choc:
                    return
                if (
                    bos.direction > 0
                    and mss < choc
                    and choc < bos1
                    or bos.direction < 0
                    and mss > choc
                    and choc > bos1
                ):
                    data = BosPerfectPattern(
                        instrument_id=instrument_id,
                        datetime=bar.ts_event,
                        color="#6892EC" if bos.direction > 0 else "#6892EC77",
                    )
                    self.publish_data(DataType(BosPerfectPattern), data)

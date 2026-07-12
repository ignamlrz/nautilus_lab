import math
from dataclasses import dataclass

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core import UUID4
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from src.strategies.choch.enums import BosEvent
from src.strategies.choch.events import ChangeOfCharacterConfirmationData
from src.strategies.choch.events import SwingData


@dataclass
class BreakOfStructureData:
    # config
    bar_type: BarType
    period: PositiveInt
    order_side: OrderSide = OrderSide.NO_ORDER_SIDE
    use_wicks: bool = False
    # boss info
    mss: float | None = None
    mss_duration: int | None = None
    choc: float = 0
    choc_duration: int = 0
    bos: float = 0
    bos_duration: int = 0
    # temp info
    high_price: float = -math.inf
    high_duration: int = 0
    low_price: float = math.inf
    low_duration: int = 0
    duration: int = 0
    # choch
    choc_triggered: bool = False
    # markets
    market_without_update: int = 0
    # conditions
    recently_created_bos: bool = False
    exists_empty_price_on_bos: bool = False

    @property
    def length(self) -> float:
        return abs(self.high_price - self.low_price)

    @classmethod
    def empty(
        cls, bar_type: BarType, period: PositiveInt, use_wicks: bool = True
    ) -> BreakOfStructureData:
        return cls(bar_type=bar_type, period=period, use_wicks=use_wicks)

    def handle_bar(self, bar: Bar) -> BosEvent:
        event = BosEvent.NONE
        if self.order_side == OrderSide.BUY:
            event = self.__handle_buyer_bos(bar)
        elif self.order_side == OrderSide.SELL:
            event = self.__handle_seller_bos(bar)
        # update first time
        elif self.order_side == OrderSide.NO_ORDER_SIDE:
            event = self._init_first_time(bar)
        self.duration += 1
        self.high_duration += 1
        self.low_duration += 1
        self.choc_duration += 1
        self.bos_duration += 1
        self.mss_duration = self.mss_duration + 1 if self.mss_duration is not None else None
        return event

    def __handle_buyer_bos(self, bar: Bar) -> BosEvent:
        high, low = self.__high_low_prices(bar)
        if bar.high < self.high_price:
            self.exists_empty_price_on_bos = True
        if bar.close > self.high_price:
            if self.recently_created_bos and self.high_duration < 2:
                self.__restore(high, low)
            event = BosEvent.NONE
            if self.high_duration >= self.period and self.exists_empty_price_on_bos:
                self.recently_created_bos = True
                self.bos = self.high_price
                self.bos_duration = self.high_duration
                self.choc = self.low_price
                self.choc_duration = self.low_duration
                if self.mss is None:
                    self.mss = self.bos
                    self.mss_duration = self.bos_duration
                    event = BosEvent.MSS_DETECTED
                else:
                    event = BosEvent.BOS_DETECTED if self.bos_duration > 0 else BosEvent.NONE
                self.__restore(high, low)
            else:
                self.high_price = high
                self.high_duration = -1
            return event
        elif bar.close < self.choc and not self.choc_triggered:
            self.choc_triggered = True
            return BosEvent.CHOCH_DETECTED
        else:
            if low < self.low_price:
                self.low_price = low
                self.low_duration = -1
            return BosEvent.NONE

    def __handle_seller_bos(self, bar: Bar) -> BosEvent:
        high, low = self.__high_low_prices(bar)
        if bar.low > self.low_price:
            self.exists_empty_price_on_bos = True
        if bar.close < self.low_price:
            if self.recently_created_bos and self.low_duration < 2:
                self.__restore(high, low)
            event = BosEvent.NONE
            if self.low_duration >= self.period and self.exists_empty_price_on_bos:
                self.bos = self.low_price
                self.bos_duration = self.low_duration
                self.choc = self.high_price
                self.choc_duration = self.high_duration
                if self.mss is None:
                    self.mss = self.bos
                    self.mss_duration = self.bos_duration
                    event = BosEvent.MSS_DETECTED
                else:
                    event = BosEvent.BOS_DETECTED if self.bos_duration > 0 else BosEvent.NONE
                self.__restore(high, low)
            else:
                self.low_price = low
                self.low_duration = -1
            return event
        elif bar.close > self.choc and not self.choc_triggered:
            self.choc_triggered = True
            return BosEvent.CHOCH_DETECTED
        else:
            if high > self.high_price:
                self.high_price = high
                self.high_duration = -1
            return BosEvent.NONE

    def _init_first_time(self, bar: Bar) -> None:
        if self.order_side == OrderSide.NO_ORDER_SIDE:
            is_positive = bar.close > bar.open
            self.order_side = OrderSide.BUY if is_positive else OrderSide.SELL
        high, low = self.__high_low_prices(bar)
        self.__restore(high, low)
        self.bos = self.high_price if self.order_side == OrderSide.BUY else self.low_price
        self.choc = self.low_price if self.order_side == OrderSide.BUY else self.high_price
        self.bos_duration = -1
        self.choc_duration = -1
        return BosEvent.NONE

    def __restore(self, high_price, low_price) -> None:
        self.high_price = high_price
        self.high_duration = -1
        self.low_price = low_price
        self.low_duration = -1
        self.duration = -1
        self.choc_triggered = False
        self.market_without_update = 0
        self.exists_empty_price_on_bos = False

    def __high_low_prices(self, bar: Bar) -> tuple[float, float]:
        if self.use_wicks:
            return bar.high, bar.low
        else:
            if bar.close > bar.open:
                return bar.close, bar.open
            else:
                return bar.open, bar.close


class ChangeOfCharacterDetectorConfig(ActorConfig, frozen=True):
    """
    Configuration for ``ChangeOfCharacterDetector`` instances.
    """

    instrument_ids: list[InstrumentId]
    bar_type_spec: str = "1-MINUTE-LAST-EXTERNAL"
    client_id: ClientId | None = None
    log_data: bool = True
    use_wicks: bool = False
    bos_period: PositiveInt = 3
    max_box_duration: PositiveInt = 100


class ChangeOfCharacterDetector(Actor):
    config: ChangeOfCharacterDetectorConfig

    def __init__(self, config: ChangeOfCharacterDetectorConfig) -> None:
        super().__init__(config)

        self._bos: dict[InstrumentId, BreakOfStructureData] = {}
        self._temp_choch_bos: dict[InstrumentId, BreakOfStructureData] = {}
        self._is_historical = False

        self._subscribe_bars_uuid_map: dict[UUID4, BarType] = {}

    def on_start(self) -> None:
        client_id = self.config.client_id
        requests_start = self.clock.utc_now() - pd.Timedelta(minutes=1440)

        self.subscribe_data(DataType(SwingData), client_id=client_id)

        for instrument_id in self.config.instrument_ids or []:
            bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
            self._bos.setdefault(
                instrument_id,
                BreakOfStructureData.empty(
                    bar_type=bar_type,
                    period=self.config.bos_period,
                    use_wicks=self.config.use_wicks,
                ),
            )
            uuid = UUID4()
            self._subscribe_bars_uuid_map[uuid] = bar_type
            self.request_bars(
                bar_type=bar_type,
                start=requests_start,
                client_id=client_id,
                callback=self.request_bars_finished,
                request_id=uuid,
            )

    def request_bars_finished(self, uuid: UUID4) -> None:
        bar_type = self._subscribe_bars_uuid_map.pop(uuid, None)
        if bar_type is not None:
            self.subscribe_bars(bar_type=bar_type, client_id=self.config.client_id)

    def on_stop(self) -> None:
        client_id = self.config.client_id

        for instrument_id in self.config.instrument_ids or []:
            bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
            self.unsubscribe_bars(bar_type=bar_type, client_id=client_id)

    def on_swing_data(self, data: SwingData) -> None:
        instrument_id = data.instrument_id
        if instrument_id not in self._bos:
            return
        bos = self._bos[instrument_id]
        bos.market_without_update += 1
        if bos.bos_duration < self.config.max_box_duration:
            return
        # restore bos to the last known high and low prices
        start = self.config.max_box_duration

        self._temp_choch_bos.pop(instrument_id, None)
        self._bos[instrument_id] = BreakOfStructureData.empty(
            bar_type=bos.bar_type,
            period=self.config.bos_period,
            use_wicks=self.config.use_wicks,
        )
        for i in range(start, -1, -1):
            bar = self.cache.bar(bar_type=bos.bar_type, index=i)
            self.on_bar(bar)
        start -= 1

    def on_bar(self, bar: Bar) -> None:
        bar_type = bar.bar_type
        instrument_id = bar_type.instrument_id

        bos = self._bos.get(instrument_id)
        bos_event = bos.handle_bar(bar)

        if bos_event == BosEvent.CHOCH_DETECTED:
            self.on_choch_detected(bar)
        elif instrument_id in self._temp_choch_bos:
            if bos_event in [BosEvent.BOS_DETECTED, BosEvent.MSS_DETECTED]:
                self._temp_choch_bos.pop(instrument_id)
                bos.choc_triggered = False
            else:
                temp_choch_bos = self._temp_choch_bos[instrument_id]
                temp_choch_bos_event = temp_choch_bos.handle_bar(bar)

                if temp_choch_bos_event == BosEvent.MSS_DETECTED:
                    self._bos[instrument_id] = temp_choch_bos
                    self._temp_choch_bos.pop(instrument_id)
                    temp_choch_bos.choc_triggered = False
                    duration = (
                        bos.low_duration
                        if temp_choch_bos.order_side == OrderSide.BUY
                        else bos.high_duration
                    )
                    _bar = self.cache.bar(bar_type=bar_type, index=duration)
                    if temp_choch_bos.order_side == OrderSide.SELL and _bar.close > _bar.open:
                        duration -= 1
                    if temp_choch_bos.order_side == OrderSide.BUY and _bar.close < _bar.open:
                        duration -= 1

                    mss = self.calc_mss(bar_type, temp_choch_bos.order_side, duration)
                    data = ChangeOfCharacterConfirmationData(
                        instrument_id=instrument_id,
                        bar_type=bar_type,
                        order_side=temp_choch_bos.order_side,
                        global_peak_price=bos.high_price
                        if temp_choch_bos.order_side == OrderSide.SELL
                        else bos.low_price,
                        # bos info
                        bos_price=temp_choch_bos.bos,
                        bos_duration=temp_choch_bos.bos_duration,
                        # choch info
                        choc_price=bos.choc,
                        choc_duration=bos.choc_duration,
                        # mss info
                        mss_price=mss.mss if mss and mss.mss != temp_choch_bos.bos else None,
                        mss_duration=mss.mss_duration
                        if mss and mss.mss != temp_choch_bos.bos
                        else None,
                        # ts event
                        ts_init=self.cache.bar(
                            bar_type=bar_type, index=temp_choch_bos.bos_duration
                        ).ts_event,
                        ts_event=self.clock.timestamp_ns(),
                        label="Change of Character Confirmation",
                    )
                    if not self._is_historical:
                        self.publish_data(DataType(ChangeOfCharacterConfirmationData), data)
                        self.log.info(data.__repr__(), color=LogColor.CYAN)

    def on_historical_data(self, data):
        if isinstance(data, Bar):
            self._is_historical = True
            self.on_bar(data)
            self._is_historical = False
        if isinstance(data, SwingData):
            self.on_swing_data(data)

    def on_data(self, data):
        if isinstance(data, SwingData):
            self.on_swing_data(data)

    def calc_mss(
        self, bar_type: BarType, order_side: OrderSide, duration: int
    ) -> BreakOfStructureData:
        bos_1 = BreakOfStructureData.empty(
            bar_type=bar_type, period=self.config.bos_period, use_wicks=self.config.use_wicks
        )
        bos_2 = None
        for i in range(duration, -1, -1):
            bar = self.cache.bar(bar_type=bar_type, index=i)
            event_1 = bos_1.handle_bar(bar)
            if event_1 == BosEvent.CHOCH_DETECTED:
                bos_2 = BreakOfStructureData.empty(
                    bar_type=bar_type,
                    period=self.config.bos_period,
                    use_wicks=self.config.use_wicks,
                )
                bos_2.handle_bar(bar)
            elif bos_2 is not None:
                if event_1 in [BosEvent.BOS_DETECTED, BosEvent.MSS_DETECTED]:
                    bos_1.choc_triggered = False
                    bos_2 = None
                else:
                    event_2 = bos_2.handle_bar(bar)
                    if event_2 == BosEvent.MSS_DETECTED:
                        bos_1 = bos_2
                        bos_2 = None
                        bos_1.choc_triggered = False

        return bos_1 if bos_1.order_side == order_side else bos_2

    def on_choch_detected(self, bar: Bar) -> None:
        new_bos = BreakOfStructureData.empty(
            bar_type=bar.bar_type, period=self.config.bos_period, use_wicks=self.config.use_wicks
        )
        new_bos.handle_bar(bar)
        self._temp_choch_bos[bar.bar_type.instrument_id] = new_bos

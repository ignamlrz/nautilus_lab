import math
from dataclasses import dataclass

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from .enums import BosEvent


@customdataclass
class ChangeOfCharacterConfirmationData(Data):
    instrument_id: InstrumentId
    bar_type: str
    order_side: str
    # bos info
    bos_price: int
    bos_duration: int
    # temp info
    high_price: float
    low_price: float
    length: float


@dataclass
class BreakOfStructureData:
    # config
    bar_type: BarType
    period: PositiveInt = 5
    order_side: OrderSide = OrderSide.NO_ORDER_SIDE
    use_wicks: bool = True
    # boss info
    bos: float | None = None
    bos_duration: int = 0
    # temp info
    high_price: float = -math.inf
    low_price: float = math.inf
    duration: int = 0
    # choch
    choch_triggered: bool = False

    @property
    def length(self) -> float:
        return abs(self.high_price - self.low_price)

    @classmethod
    def empty(cls, bar_type: BarType, use_wicks: bool = True) -> BreakOfStructureData:
        return cls(bar_type=bar_type, use_wicks=use_wicks)

    def handle_bar(self, bar: Bar) -> BosEvent:
        self.duration += 1
        if self.order_side == OrderSide.BUY:
            return self.__handle_buyer_bos(bar)
        elif self.order_side == OrderSide.SELL:
            return self.__handle_seller_bos(bar)
        # update first time
        elif self.order_side == OrderSide.NO_ORDER_SIDE:
            return self.__init_first_time(bar)

    def __handle_buyer_bos(self, bar: Bar) -> BosEvent:
        high, low = self.__high_low_prices(bar)
        if bar.close > self.high_price:
            event = BosEvent.NONE
            if self.duration >= self.period:
                event = BosEvent.BOS_DETECTED if self.bos_duration > 0 else BosEvent.NONE
                self.bos = self.high_price
                self.bos_duration = self.duration
            self.__restore(high, low)
            return event
        elif bar.close < self.bos and not self.choch_triggered:
            self.choch_triggered = True
            return BosEvent.CHOCH_DETECTED
        else:
            self.low_price = min(self.low_price, low)
            return BosEvent.NONE

    def __handle_seller_bos(self, bar: Bar) -> BosEvent:
        high, low = self.__high_low_prices(bar)
        if bar.close < self.low_price:
            event = BosEvent.NONE
            if self.duration >= self.period:
                event = BosEvent.BOS_DETECTED if self.bos_duration > 0 else BosEvent.NONE
                self.bos = self.low_price
                self.bos_duration = self.duration
            self.__restore(high, low)
            return event
        elif bar.close > self.bos and not self.choch_triggered:
            self.choch_triggered = True
            return BosEvent.CHOCH_DETECTED
        else:
            self.high_price = max(self.high_price, high)
            return BosEvent.NONE

    def __init_first_time(self, bar: Bar) -> None:
        is_positive = bar.close > bar.open
        self.order_side = OrderSide.BUY if is_positive else OrderSide.SELL
        high, low = self.__high_low_prices(bar)
        self.__restore(high, low)
        self.bos = self.high_price if is_positive else self.low_price
        return BosEvent.NONE

    def __restore(self, high_price, low_price) -> None:
        self.high_price = high_price
        self.low_price = low_price
        self.duration = 0
        self.choch_triggered = False

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
    use_wicks: bool = True


class ChangeOfCharacterDetector(Actor):
    config: ChangeOfCharacterDetectorConfig

    def __init__(self, config: ChangeOfCharacterDetectorConfig) -> None:
        super().__init__(config)

        self._bos: dict[InstrumentId, BreakOfStructureData] = {}
        self._temp_choch_bos: dict[InstrumentId, BreakOfStructureData] = {}

    def on_start(self) -> None:
        client_id = self.config.client_id
        requests_start = self.clock.utc_now() - pd.Timedelta(minutes=1440)

        for instrument_id in self.config.instrument_ids or []:
            bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
            self.request_bars(
                bar_type=bar_type,
                start=requests_start,
                client_id=client_id,
                callback=lambda _: self.subscribe_bars(
                    bar_type=bar_type,
                    client_id=client_id,
                ),
            )
            self._bos.setdefault(
                instrument_id,
                BreakOfStructureData.empty(bar_type=bar_type, use_wicks=self.config.use_wicks),
            )

    def on_bar(self, bar: Bar) -> None:
        bar_type = bar.bar_type
        instrument_id = bar_type.instrument_id

        bos = self._bos.get(instrument_id)
        bos_event = bos.handle_bar(bar)

        if bos_event == BosEvent.CHOCH_DETECTED:
            self.on_choch_detected(bar)
        elif instrument_id in self._temp_choch_bos:
            if bos_event == BosEvent.BOS_DETECTED:
                self._temp_choch_bos.pop(instrument_id)
            else:
                temp_choch_bos = self._temp_choch_bos[instrument_id]
                temp_choch_bos_event = temp_choch_bos.handle_bar(bar)

                if temp_choch_bos_event == BosEvent.BOS_DETECTED:
                    self._bos[instrument_id] = temp_choch_bos
                    self._temp_choch_bos.pop(instrument_id)
                    self.log.info(
                        f"Change of Character confirmed for {instrument_id}: "
                        f"Order Side: {temp_choch_bos.order_side.name}, "
                        f"Diff: {temp_choch_bos.length / bar.close:.2%}, "
                        f"BoS: {temp_choch_bos.bos}, "
                        f"CHoCH: {bos.high_price if temp_choch_bos.order_side == OrderSide.BUY else bos.low_price}, "
                        f"Duration: {temp_choch_bos.bos_duration}",
                        color=LogColor.GREEN
                        if temp_choch_bos.order_side == OrderSide.BUY
                        else LogColor.RED,
                    )
                    data = ChangeOfCharacterConfirmationData(
                        instrument_id=instrument_id,
                        bar_type=bar_type,
                        order_side=temp_choch_bos.order_side,
                        # bos info
                        bos_price=temp_choch_bos.bos,
                        bos_duration=temp_choch_bos.bos_duration,
                        # temp info
                        high_price=temp_choch_bos.high_price,
                        low_price=temp_choch_bos.low_price,
                        length=temp_choch_bos.length,
                    )
                    self.publish_data(DataType(ChangeOfCharacterConfirmationData), data)

        # If a change of character is detected, create a ChangeOfCharacterData instance
        # and store it in self._bos or perform any other necessary actions.

    def on_choch_detected(self, bar: Bar) -> None:
        new_bos = BreakOfStructureData.empty(bar_type=bar.bar_type, use_wicks=self.config.use_wicks)
        new_bos.handle_bar(bar)
        self._temp_choch_bos[bar.bar_type.instrument_id] = new_bos

    def on_stop(self) -> None:
        client_id = self.config.client_id

        for instrument_id in self.config.instrument_ids or []:
            bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
            self.unsubscribe_bars(bar_type=bar_type, client_id=client_id)

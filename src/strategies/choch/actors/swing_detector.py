import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.common.enums import LogColor
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.core import UUID4
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.indicators import Swings
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from src.helpers.market_hours import MarketHours
from src.helpers.market_hours import upcoming
from src.strategies.choch.enums import Market
from src.strategies.choch.events import OpenMarketData
from src.strategies.choch.events import SwingData


@dataclass
class MarketInfo:
    hours: MarketHours
    min_diff: float
    operable: bool


@dataclass
class MarketData:
    market_info: MarketInfo
    next_epoch: pd.Timestamp
    use_wicks: bool
    # current session
    session_high_price: float
    session_low_price: float
    session_high_duration: int = 0
    session_low_duration: int = 0
    session_bar: int = 0
    active: bool = False
    # after market close
    break_above: bool = False
    break_above_duration: int = math.inf
    break_above_market: Market | None = None
    break_above_triggered: bool = False

    break_below: bool = False
    break_below_duration: int = math.inf
    break_below_market: Market | None = None
    break_below_triggered: bool = False

    @classmethod
    def create(cls, market_info: MarketInfo, bar: Bar, use_wicks: bool = False) -> MarketData:
        next_epoch = market_info.hours.next_open(unix_nanos_to_dt(bar.ts_event)).tz_convert("UTC")
        return cls(
            market_info=market_info,
            next_epoch=next_epoch,
            use_wicks=use_wicks,
            session_high_price=bar.high,
            session_low_price=bar.low,
            active=True,
        )

    def handle_bar_from_active_market(self, bar: Bar) -> None:
        self.session_bar += 1
        high, low = self.__high_low_prices(bar)
        if high > self.session_high_price:
            self.session_high_price = high
            self.session_high_duration = 0
        else:
            self.session_high_duration += 1
        if low < self.session_low_price:
            self.session_low_price = low
            self.session_low_duration = 0
        else:
            self.session_low_duration += 1

    def handle_bar_from_closed_market(self, bar: Bar, market: Market) -> None:
        self.session_bar += 1
        high, low = self.__high_low_prices(bar)
        self.session_low_duration += 1
        self.session_high_duration += 1
        if self.break_below:
            self.break_below_duration += 1
        elif low < self.session_low_price:
            self.break_below = True
            self.break_below_duration = 0
            self.break_below_market = market

        if self.break_above:
            self.break_above_duration += 1
        elif high >= self.session_high_price:
            self.break_above = True
            self.break_above_duration = 0
            self.break_above_market = market

    def __high_low_prices(self, bar: Bar) -> tuple[float, float]:
        if self.use_wicks:
            return bar.high, bar.low
        else:
            if bar.close > bar.open:
                return bar.close, bar.open
            else:
                return bar.open, bar.close


MARKETS = {
    Market.SSE_SZSE: MarketInfo(
        hours=MarketHours.continuous("Asia/Hong_Kong", 8, 0, 15, 0, name="SSE/SZSE"),
        min_diff=0.004,
        operable=True,
    ),
    Market.LSE: MarketInfo(
        hours=MarketHours.continuous("Europe/London", 8, 0, 15, 0, name="LSE"),
        min_diff=0.004,
        operable=True,
    ),
    Market.PRE_NYSE: MarketInfo(
        hours=MarketHours.continuous("America/New_York", 8, 0, 9, 30, name="PRE_NYSE"),
        min_diff=0.002,
        operable=False,
    ),
    Market.NYSE: MarketInfo(
        hours=MarketHours.continuous("America/New_York", 9, 30, 16, 0, name="NYSE"),
        min_diff=0.006,
        operable=True,
    ),
    Market.POST_NYSE: MarketInfo(
        hours=MarketHours.continuous("America/New_York", 16, 0, 20, 0, name="POST_NYSE"),
        min_diff=0.004,
        operable=True,
    ),
}


class SwingDetectorConfig(ActorConfig, frozen=True):
    """
    Configuration for ``SwingDetector`` instances.
    """

    instrument_ids: list[InstrumentId]
    bar_type_spec: str = "1-MINUTE-LAST-EXTERNAL"
    client_id: ClientId | None = None
    log_data: bool = True
    use_wicks: bool = True
    fast_period: PositiveInt = 30
    slow_period: PositiveInt = 60


class SwingDetector(Actor):
    """
    An actor for detecting swings in the market.

    Parameters
    ----------
    config : SwingDetectorConfig
        The configuration for the instance.

    """

    config: SwingDetectorConfig

    def __init__(self, config: SwingDetectorConfig) -> None:
        super().__init__(config)

        self._swings: dict[InstrumentId, Swings] = {}
        self._closed_market_data: dict[InstrumentId, dict[Market, MarketData]] = {}
        self._open_market_data: dict[InstrumentId, tuple[Market, MarketData]] = {}

        # sort markets by their opening time
        self._sort_markets: list[str] = []
        self._current_market: Market

        self._subscribe_bars_uuid_map: dict[UUID4, BarType] = {}

    def update_market_opening(self, date: datetime) -> None:
        upcoming_markets = upcoming([v.hours for v in MARKETS.values()], date)
        if upcoming_markets:
            upcoming_markets = [(Market(v.name), t) for v, t in upcoming_markets]
            if self._current_market == upcoming_markets[-1][0]:
                return
            self._upcoming_markets = upcoming_markets
            self._current_market = self._upcoming_markets[-1][0]

            for instrument_id in self._open_market_data:
                market, data = self._open_market_data[instrument_id]
                data.active = False
                self._closed_market_data.setdefault(instrument_id, {})[market] = data

    def on_market_opening_time_event(self, event: TimeEvent) -> None:
        self.update_market_opening(unix_nanos_to_dt(event.ts_event))
        _, next_open_time = self._upcoming_markets[0]
        self.clock.set_time_alert(
            name="SwingDetector:market_opening",
            alert_time=next_open_time,
            callback=self.on_market_opening_time_event,
        )
        open_market_data = OpenMarketData(
            market=self._current_market.name,
            min_diff=MARKETS[self._current_market].min_diff,
            operable=MARKETS[self._current_market].operable,
        )
        self.publish_data(DataType(OpenMarketData), open_market_data)

    def on_start(self) -> None:
        client_id = self.config.client_id
        requests_start = self.clock.utc_now() - pd.Timedelta(minutes=1440)

        upcoming_markets = upcoming([v.hours for v in MARKETS.values()], self.clock.utc_now())
        self._upcoming_markets = [(Market(v.name), t) for v, t in upcoming_markets]
        if not self._upcoming_markets:
            raise ValueError(
                "No upcoming markets found. Please check the market hours configuration."
            )
        self._current_market = self._upcoming_markets[-1][0]
        self.clock.set_time_alert(
            name="SwingDetector:market_opening",
            alert_time=self._upcoming_markets[0][1],
            callback=self.on_market_opening_time_event,
        )

        for instrument_id in self.config.instrument_ids or []:
            bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
            self._swings[instrument_id] = swings_fast = Swings(period=self.config.fast_period)
            self.register_indicator_for_bars(bar_type, swings_fast)

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

    def on_bar(self, bar: Bar) -> None:
        if "1-MINUTE" not in str(bar.bar_type.spec):
            return
        self.update_market_data(bar)
        swing = self._swings[bar.bar_type.instrument_id]
        if swing.initialized and swing.changed:
            market_rebased, market_rebased_data = self.most_recent_rebased_market(
                bar.bar_type.instrument_id
            )
            market, market_data = self._open_market_data.get(
                bar.bar_type.instrument_id, (None, None)
            )
            if not market or not market_rebased or not MARKETS[market].operable:
                return

            rebased_above_this_market = (
                market_rebased_data.break_above
                and market_rebased_data.break_above_market == market
                and not market_rebased_data.break_above_triggered
            )
            rebased_below_this_market = (
                market_rebased_data.break_below
                and market_rebased_data.break_below_market == market
                and not market_rebased_data.break_below_triggered
            )
            diff = market_data.session_high_price - market_data.session_low_price
            diff_perp = diff / bar.close
            if diff_perp < MARKETS[market].min_diff:
                return
            if (
                rebased_above_this_market or market_data.session_high_duration < swing.period
            ) and swing.direction == -1:
                if self.config.log_data:
                    if rebased_above_this_market:
                        self.log.info(
                            f"Swings confirmed on 1m: ⬇️ (#{swing.duration} bars) | Market rebased {market_rebased.name} on market {market.name} | Diff: [{diff_perp:.2%}]",
                            LogColor.RED,
                        )
                    else:
                        self.log.info(
                            f"Swings confirmed on 1m: ⬇️ (#{swing.duration} bars) | New high on market {market.name} | Diff: [{diff_perp:.2%}]",
                            LogColor.RED,
                        )
                market_rebased_data.break_above_triggered = True
                swing_data = SwingData(
                    instrument_id=bar.bar_type.instrument_id,
                    bar_type=bar.bar_type,
                    order_side=OrderSide.SELL,
                    high_price=swing.high_price,
                    low_price=swing.low_price,
                    duration=swing.duration,
                )
                self.publish_data(DataType(SwingData), swing_data)
            elif (
                rebased_below_this_market or market_data.session_low_duration < swing.period
            ) and swing.direction == 1:
                if self.config.log_data:
                    if rebased_below_this_market:
                        self.log.info(
                            f"Swings confirmed on 1m: ⬆️ (#{swing.duration} bars) | Market rebased {market_rebased.name} on market {market.name} | Diff: [{diff_perp:.2%}]",
                            LogColor.GREEN,
                        )
                    else:
                        self.log.info(
                            f"Swings confirmed on 1m: ⬆️ (#{swing.duration} bars) | New low on market {market.name} | Diff: [{diff_perp:.2%}]",
                            LogColor.GREEN,
                        )
                market_rebased_data.break_below_triggered = True
                swing_data = SwingData(
                    instrument_id=bar.bar_type.instrument_id,
                    bar_type=bar.bar_type,
                    order_side=OrderSide.BUY,
                    high_price=swing.high_price,
                    low_price=swing.low_price,
                    duration=swing.duration,
                )
                self.publish_data(DataType(SwingData), swing_data)

    def on_historical_data(self, data: Any) -> None:
        """
        Actions to be performed when the actor is running and receives historical data.
        """
        if isinstance(data, Bar):
            self.update_market_opening(unix_nanos_to_dt(data.ts_event))
            self.update_market_data(data)
            self.on_bar(data)
            if "BTCUSDT" in str(data.bar_type):
                swings = self._swings[data.bar_type.instrument_id]
                if swings.changed:
                    datetime = (
                        swings.low_datetime if swings.direction == -1 else swings.high_datetime
                    )
                    self.log.info(
                        "Datetime: "
                        + str(datetime)
                        + " | Swing1m: "
                        + str(swings.direction)
                        + " ("
                        + str(swings.length)
                        + ")",
                        LogColor.CYAN,
                    )

    def update_market_data(self, bar: Bar) -> None:
        """
        Actions to be performed when the actor is running and receives a bar.
        """
        current_market = self._current_market
        if (
            bar.bar_type.instrument_id not in self._open_market_data
            or self._open_market_data[bar.bar_type.instrument_id][0] != current_market
        ):
            market_data = MarketData.create(
                market_info=MARKETS[current_market], bar=bar, use_wicks=self.config.use_wicks
            )
            self._open_market_data[bar.bar_type.instrument_id] = (current_market, market_data)
        else:
            self._open_market_data[bar.bar_type.instrument_id][1].handle_bar_from_active_market(bar)

        for data in self._closed_market_data.get(bar.bar_type.instrument_id, {}).values():
            data.handle_bar_from_closed_market(bar, current_market)

    def most_recent_rebased_market(
        self, instrument_id: InstrumentId
    ) -> tuple[Market, MarketData] | tuple[None, None]:
        """
        Returns the most recent rebased market for the given instrument ID.

        Parameters
        ----------
        instrument_id : InstrumentId
            The instrument ID to check.

        Returns
        -------
        tuple[Market, MarketData] | tuple[None, None]
            A tuple containing the most recent rebased market and its corresponding MarketData,
            or (None, None) if no rebased market is found.
        """
        closed_markets = self._closed_market_data.get(instrument_id, {})
        if not closed_markets:
            return None, None

        # Sort markets by their break_above_duration and break_below_duration
        def sort_key(item):
            _, data = item
            if data.break_above and data.break_below:
                return min(data.break_above_duration, data.break_below_duration)
            elif data.break_above:
                return data.break_above_duration
            elif data.break_below:
                return data.break_below_duration
            else:
                return math.inf

        sorted_markets = sorted(
            closed_markets.items(),
            key=sort_key,
        )

        for market, data in sorted_markets:
            if data.break_above or data.break_below:
                return market, data

        return None, None

import math
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.common.enums import LogColor
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.core import UUID4
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.indicators import Swings
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from src.helpers.market_hours import MarketHours
from src.helpers.market_hours import open_now
from src.helpers.market_hours import upcoming
from src.strategies.choch.enums import Market
from src.strategies.choch.events import ClosedMarketData
from src.strategies.choch.events import OpenMarketData
from src.strategies.choch.events import SwingData


@dataclass
class MarketInfo:
    hours: MarketHours
    min_diff: float
    operable: bool
    max_diff: float = 1.0
    color: str = "#3051E2"


@dataclass
class MarketData:
    market_info: MarketInfo
    open_datetime: pd.Timestamp
    close_datetime: pd.Timestamp
    next_epoch: pd.Timestamp
    use_wicks: bool
    # current session
    session_high_price: float
    session_low_price: float
    session_high_duration: int = 0
    session_low_duration: int = 0
    session_bar: int = 0
    active: bool = False
    markets_breaked_above: list[Market] = field(default_factory=list)
    markets_breaked_below: list[Market] = field(default_factory=list)
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
        upcoming_markets = upcoming(
            [v.hours for v in MARKETS.values()], unix_nanos_to_dt(bar.ts_event)
        )
        return cls(
            market_info=market_info,
            open_datetime=unix_nanos_to_dt(bar.ts_event),
            close_datetime=upcoming_markets[0][1].tz_convert("UTC"),
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
    Market.ASIA: MarketInfo(
        hours=MarketHours.continuous(
            "Asia/Hong_Kong", 8, 0, 15, 0, name=Market.ASIA.name, use_weekends=True
        ),
        min_diff=0.002,
        operable=True,
        max_diff=0.1,
        color="#CBE45C",
    ),
    Market.LONDON: MarketInfo(
        hours=MarketHours.continuous(
            "Europe/London", 8, 0, 14, 0, name=Market.LONDON.name, use_weekends=True
        ),
        min_diff=0.002,
        operable=True,
        max_diff=0.1,
        color="#4EBE54",
    ),
    Market.EEUU_PRE: MarketInfo(
        hours=MarketHours.continuous(
            "America/New_York", 8, 30, 9, 30, name=Market.EEUU_PRE.name, use_weekends=True
        ),
        min_diff=0.002,
        operable=False,
        max_diff=0.1,
        color="#E23030",
    ),
    Market.EEUU: MarketInfo(
        hours=MarketHours.continuous(
            "America/New_York", 9, 30, 16, 0, name=Market.EEUU.name, use_weekends=True
        ),
        min_diff=0.002,
        operable=True,
        max_diff=0.1,
        color="#2BD0DB",
    ),
    Market.EEUU_POST: MarketInfo(
        hours=MarketHours.continuous(
            "America/New_York", 16, 0, 20, 0, name=Market.EEUU_POST.name, use_weekends=True
        ),
        min_diff=0.002,
        operable=True,
        max_diff=0.1,
        color="#F5A623",
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
    period: PositiveInt = 60


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
        self._current_market: Market | None = None
        self._next_market_opening: pd.Timestamp = unix_nanos_to_dt(0)

    def on_start(self) -> None:
        client_id = self.config.client_id
        requests_start = self.clock.utc_now() - pd.Timedelta(minutes=1440 * 3)

        self._current_market: Market | None = None

        uuids: tuple[UUID4] = ()
        for instrument_id in self.config.instrument_ids or []:
            bar_type = BarType.from_str(f"{instrument_id.value}-{self.config.bar_type_spec}")
            self._swings[instrument_id] = swings = Swings(period=self.config.period)
            self.register_indicator_for_bars(bar_type, swings)

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
            next_open_time = self.next_market_opening(self.clock.utc_now())[1]
        self.clock.set_time_alert(
            name="SwingDetector:market_opening",
            alert_time=next_open_time,
            callback=self.on_market_opening_time_event,
        )

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
            if diff_perp < MARKETS[market].min_diff or diff_perp > MARKETS[market].max_diff:
                return

            # rebased above this market
            if market_data.session_high_duration < swing.period and swing.direction == -1:
                if rebased_above_this_market:
                    market_rebased_data.break_above_triggered = True
                    text = f"Swings confirmed on 1m: ⬇️ (#{swing.duration} bars) | {market.name} breaks above {market_rebased.name} | Diff: [{diff_perp:.2%}]"
                elif market_data.markets_breaked_above:
                    text = f"Swings confirmed on 1m: ⬇️ (#{swing.duration} bars) | New high on {market.name} | Diff: [{diff_perp:.2%}]"
                else:
                    return
                for md in market_data.markets_breaked_above:
                    # if md.session_high_price > swing.low_price:
                    if rebased_above_this_market:
                        if self.config.log_data:
                            self.log.info(text, LogColor.RED)
                        swing_data = SwingData(
                            instrument_id=bar.bar_type.instrument_id,
                            bar_type=bar.bar_type,
                            order_side=OrderSide.SELL,
                            high_price=swing.high_price,
                            low_price=swing.low_price,
                            tested_price=md.session_high_price,
                            duration=swing.duration,
                            label=f"{text} | Broken Price: {md.session_high_price}",
                            ts_init=self.clock.timestamp_ns(),
                            ts_event=self.clock.timestamp_ns(),
                        )
                        self.publish_data(DataType(SwingData), swing_data)
                        break
            # rebased below this market
            if market_data.session_low_duration < swing.period and swing.direction == 1:
                if rebased_below_this_market:
                    market_rebased_data.break_below_triggered = True
                    text = f"Swings confirmed on 1m: ⬆️ (#{swing.duration} bars) | {market.name} breaks below {market_rebased.name} | Diff: [{diff_perp:.2%}]"
                elif market_data.markets_breaked_below:
                    text = f"Swings confirmed on 1m: ⬆️ (#{swing.duration} bars) | New low on {market.name} | Diff: [{diff_perp:.2%}]"
                else:
                    return
                for md in market_data.markets_breaked_below:
                    # if md.session_low_price < swing.high_price:
                    if rebased_below_this_market:
                        if self.config.log_data:
                            self.log.info(text, LogColor.GREEN)
                        swing_data = SwingData(
                            instrument_id=bar.bar_type.instrument_id,
                            bar_type=bar.bar_type,
                            order_side=OrderSide.BUY,
                            high_price=swing.high_price,
                            low_price=swing.low_price,
                            tested_price=md.session_low_price,
                            duration=swing.duration,
                            label=f"{text} | Broken Price: {md.session_low_price}",
                            ts_init=self.clock.timestamp_ns(),
                            ts_event=self.clock.timestamp_ns(),
                        )
                        self.publish_data(DataType(SwingData), swing_data)
                        break

    def on_historical_data(self, data: Any) -> None:
        """
        Actions to be performed when the actor is running and receives historical data.
        """
        if isinstance(data, Bar):
            self.update_market_opening(unix_nanos_to_dt(data.ts_event))
            self.update_market_data(data)
            self.on_bar(data)

    def on_market_opening_time_event(self, event: TimeEvent) -> None:
        self.update_market_opening(unix_nanos_to_dt(event.ts_event))
        _, next_open_time = self.next_market_opening(self.clock.utc_now())
        self.clock.set_time_alert(
            name="SwingDetector:market_opening",
            alert_time=next_open_time,
            callback=self.on_market_opening_time_event,
        )
        open_market_data = OpenMarketData(
            market=self._current_market.name,
            min_diff=MARKETS[self._current_market].min_diff,
            operable=MARKETS[self._current_market].operable,
            label=f"Market {self._current_market.name} is now open. Operable: {MARKETS[self._current_market].operable}",
            open_datetime=event.ts_event,
            close_datetime=dt_to_unix_nanos(next_open_time),
            ts_init=self.clock.timestamp_ns(),
            ts_event=self.clock.timestamp_ns(),
        )
        self.publish_data(DataType(OpenMarketData), open_market_data)

    def next_market_opening(self, date) -> tuple[Market, pd.Timestamp] | tuple[None, None]:
        """
        Returns the next market opening time.

        Returns
        -------
        tuple[Market, pd.Timestamp]
            A tuple containing the next market and its opening time.
        """
        result = upcoming([v.hours for v in MARKETS.values()], date)
        if result:
            next_open_market, next_open_time = result[0]
            return Market[next_open_market.name], next_open_time.tz_convert("UTC")
        return None, None

    def update_market_opening(self, date: datetime) -> None:
        open_markets = open_now([v.hours for v in MARKETS.values()], date)
        if open_markets and date > self._next_market_opening:
            open_market = sorted(
                open_markets, key=lambda v: v._session_endpoints(date)[0][0].tz_convert("UTC")
            )[-1]
            if self._current_market == Market[open_market.name]:
                return

            next_market, next_open_time = self.next_market_opening(date)
            self._next_market_opening = next_open_time
            self.log.info(
                f"Closed {self._current_market.name if self._current_market else 'N/A'} | Open {open_market.name} | Date {date}",
                LogColor.CYAN,
            )

            # change
            for instrument_id in self.config.instrument_ids or []:
                if instrument_id not in self._open_market_data:
                    continue
                closed_market, data = self._open_market_data[instrument_id]
                data.active = False
                self._closed_market_data.setdefault(instrument_id, {})[closed_market] = data
                closed_market_data = ClosedMarketData(
                    instrument_id=instrument_id,
                    market=closed_market.name,
                    high_price=data.session_high_price,
                    low_price=data.session_low_price,
                    operable=data.market_info.operable,
                    open_datetime=dt_to_unix_nanos(data.open_datetime),
                    close_datetime=dt_to_unix_nanos(date),
                    color=MARKETS[closed_market].color,
                    ts_init=self.clock.timestamp_ns(),
                    ts_event=self.clock.timestamp_ns(),
                )
                self.publish_data(DataType(ClosedMarketData), closed_market_data)

            upcoming_markets = upcoming([v.hours for v in MARKETS.values()], date)
            upcoming_markets = [(Market[v.name], t.tz_convert("UTC")) for v, t in upcoming_markets]
            self._upcoming_markets = upcoming_markets
            self._current_market = Market[open_market.name]

    def update_market_data(self, bar: Bar) -> None:
        """
        Actions to be performed when the actor is running and receives a bar.
        """
        current_market = self._current_market
        if not current_market:
            return
        if (
            bar.bar_type.instrument_id not in self._open_market_data
            or self._open_market_data[bar.bar_type.instrument_id][0] != current_market
        ):
            market_data = MarketData.create(
                market_info=MARKETS[current_market], bar=bar, use_wicks=self.config.use_wicks
            )
            self._open_market_data[bar.bar_type.instrument_id] = (current_market, market_data)
        else:
            market_data = self._open_market_data[bar.bar_type.instrument_id][1]
            market_data.handle_bar_from_active_market(bar)

        for data in self._closed_market_data.get(bar.bar_type.instrument_id, {}).values():
            data.handle_bar_from_closed_market(bar, current_market)
            if data.break_above_duration == 0:
                market_data.markets_breaked_above.append(data)
                self.log.info(
                    f"Market {current_market.name} rebased top on market {data.market_info.hours.name}",
                    LogColor.CYAN,
                )
            if data.break_below_duration == 0:
                self.log.info(
                    f"Market {current_market.name} rebased bottom on market {data.market_info.hours.name}",
                    LogColor.CYAN,
                )
                market_data.markets_breaked_below.append(data)

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

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
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.indicators import Swings
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from src.helpers.market_hours import MarketHours
from src.helpers.market_hours import upcoming
from src.strategies.choch.enums import Market


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
    active: bool = False
    # after market close
    break_above: bool = False
    break_below: bool = False
    break_above_duration: int = math.inf
    break_below_duration: int = math.inf

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

    def handle_bar(self, bar: Bar) -> None:
        high, low = self.__high_low_prices(bar)
        if self.active:
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
        else:
            self.session_low_duration += 1
            self.session_high_duration += 1
            if self.break_below:
                self.break_below_duration += 1
            elif low < self.session_low_price:
                self.break_below = True
                self.break_below_duration = 0

            if self.break_above:
                self.break_above_duration += 1
            elif high >= self.session_high_price:
                self.break_above = True
                self.break_above_duration = 0

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

        self._swings_fast: dict[InstrumentId, Swings] = {}
        self._swings_slow: dict[InstrumentId, Swings] = {}
        self._closed_market_data: dict[InstrumentId, dict[Market, MarketData]] = {}
        self._open_market_data: dict[InstrumentId, tuple[Market, MarketData]] = {}

        # sort markets by their opening time
        self._sort_markets: list[str] = []
        self._current_market: Market

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
            self._swings_fast[instrument_id] = swings_fast = Swings(period=self.config.fast_period)
            self._swings_slow[instrument_id] = swings_slow = Swings(period=self.config.slow_period)
            self.register_indicator_for_bars(bar_type, swings_fast)
            self.register_indicator_for_bars(bar_type, swings_slow)
            self.request_bars(
                bar_type=bar_type,
                start=requests_start,
                client_id=client_id,
                callback=lambda _: self.subscribe_bars(
                    bar_type=bar_type,
                    client_id=client_id,
                ),
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
        swing30m = self._swings_fast[bar.bar_type.instrument_id]
        swing1h = self._swings_slow[bar.bar_type.instrument_id]

        # if swing1h.changed or swing30m.changed:
        #     market_rebased, data_rebased = self.most_recently_market_closed_rebased(
        #         bar.bar_type.instrument_id
        #     )
        #     market_open, data_open = self.most_recently_market_open(bar.bar_type.instrument_id)
        #     if market_rebased and data_rebased and market_open and data_open:
        #         last_high_bar = self.cache.bar(bar.bar_type, data_open.session_high_duration)
        #         last_low_bar = self.cache.bar(bar.bar_type, data_open.session_low_duration)
        #         diff = last_high_bar.high - last_low_bar.low
        #         diff_perp = diff / bar.close
        #         num_bars_sessions = (
        #             int(
        #                 (self.clock.utc_now() - data_open.start_date).value
        #                 / pd.Timedelta(minutes=1).value
        #             )
        #             - 1
        #         )
        #         bars_since_rebased = min(num_bars_sessions, 150)
        #         if diff_perp > MARKETS[market_open]["min_diff"]:
        #             if (
        #                 data_rebased.break_above_duration < data_rebased.break_below_duration
        #                 and data_rebased.break_above_duration <= bars_since_rebased
        #                 and (
        #                     (
        #                         swing1h.direction == -1
        #                         and swing1h.changed
        #                         and swing1h.since_high == data_rebased.break_above_duration
        #                     )
        #                     or (
        #                         swing30m.direction == -1
        #                         and swing30m.changed
        #                         and swing30m.since_high == data_rebased.break_above_duration
        #                     )
        #                 )
        #             ):
        #                 ratios_str = ", ".join([f"{r:.2%}" for r in ratios])
        #                 self._notify_signal(
        #                     f"CHoCH confirmed on 1m: ⬇️ (#{swing1h.since_low} bars) | Market rebased: {market_rebased} | OB: [{ratios_str}]",
        #                     bar.bar_type.instrument_id,
        #                     ratios=ratios,
        #                 )
        #             elif (
        #                 data_rebased.break_below_duration < data_rebased.break_above_duration
        #                 and data_rebased.break_below_duration <= bars_since_rebased
        #                 and (
        #                     (swing1h.direction == 1 and swing1h.changed)
        #                     or (swing30m.direction == 1 and swing30m.changed)
        #                 )
        #             ):
        #                 ratios_str = ", ".join([f"{r:.2%}" for r in ratios])
        #                 self._notify_signal(
        #                     f"CHoCH confirmed on 1m: ⬆️ (#{swing1h.since_high} bars) | Market rebased {market_rebased} | OB: [{ratios_str}]",
        #                     bar.bar_type.instrument_id,
        #                     ratios=ratios,
        #                 )

    def on_historical_data(self, data: Any) -> None:
        """
        Actions to be performed when the actor is running and receives historical data.
        """
        if isinstance(data, Bar):
            self.update_market_opening(unix_nanos_to_dt(data.ts_event))
            self.update_market_data(data)
            if "BTCUSDT" in str(data.bar_type):
                swing30m = self._swings_fast[data.bar_type.instrument_id]
                swing1h = self._swings_slow[data.bar_type.instrument_id]
                if swing30m.changed:
                    datetime = (
                        swing30m.low_datetime
                        if swing30m.direction == -1
                        else swing30m.high_datetime
                    )
                    self.log.info(
                        "Datetime: "
                        + str(datetime)
                        + " | Swing1m: "
                        + str(swing30m.direction)
                        + " ("
                        + str(swing30m.length)
                        + ")",
                        LogColor.CYAN,
                    )
                if swing1h.changed:
                    datetime = (
                        swing1h.low_datetime if swing1h.direction == -1 else swing1h.high_datetime
                    )
                    self.log.info(
                        "Datetime: "
                        + str(datetime)
                        + " | Swing1h: "
                        + str(swing1h.direction)
                        + " ("
                        + str(swing1h.length)
                        + ")",
                        LogColor.YELLOW,
                    )
            if "1-MINUTE" not in str(data.bar_type.spec):
                return

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
            self._open_market_data[bar.bar_type.instrument_id][1].handle_bar(bar)

        for data in self._closed_market_data.get(bar.bar_type.instrument_id, {}).values():
            data.handle_bar(bar)

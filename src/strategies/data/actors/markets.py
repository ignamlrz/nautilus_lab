import math
from bisect import bisect_left
from bisect import bisect_right
from collections import OrderedDict
from collections import deque
from dataclasses import dataclass
from dataclasses import field

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from src.helpers.bar import maxmin_price
from src.strategies.data.events import ClosedMarketData
from src.strategies.data.events import HistoricalBarData
from src.strategies.data.events import LiveBarData
from src.strategies.data.events import MarketBreakAboveData
from src.strategies.data.events import MarketBreakBelowData
from src.strategies.data.events import NewSessionHighData
from src.strategies.data.events import NewSessionLowData


BLACKOUT_WINDOW = "BLACKOUT_WINDOW"


class MarketInfoActorConfig(ActorConfig, frozen=True):
    """
    Configuration for ``MarketInfoActor`` instances.
    """

    tz: str
    open_time_hour: int
    open_time_minute: int
    color: str = "#3A74A3"

    def start_abs_minutes(self) -> int:
        """Minutes since midnight"""
        return self.open_time_hour * 60 + self.open_time_minute

    def open_time_utc(self, date: pd.Timestamp) -> pd.Timestamp:
        """Return the open time in UTC for a given date."""
        timedelta = pd.Timedelta(minutes=self.start_abs_minutes())
        midnight = date.tz_convert(self.tz).normalize()
        return (midnight + timedelta).tz_convert("UTC")


class MarketBlackoutWindowConfig(ActorConfig, frozen=True):
    """Temporal blackout window configuration for a market."""

    start_weekday: int  # 0 = lunes ... 6 = domingo
    start_hour: int
    start_minute: int
    end_weekday: int  # inclusive
    end_hour: int
    end_minute: int
    tz: str = "America/New_York"
    color: str = "#1F1F1F"  # gris oscuro por defecto para diferenciar del color del market

    def start_abs_minutes(self) -> int:
        """Minutos desde el lunes 00:00 — para comparar entre días."""
        return self.start_weekday * 1440 + self.start_hour * 60 + self.start_minute

    def end_abs_minutes(self) -> int:
        return self.end_weekday * 1440 + self.end_hour * 60 + self.end_minute

    def open_time_utc(self, date: pd.Timestamp) -> pd.Timestamp:
        """Return the open time in UTC for a given date."""
        timedelta = pd.Timedelta(minutes=self.start_abs_minutes())
        midnight = date.tz_convert(self.tz).normalize()
        monday_midnight = midnight - pd.Timedelta(days=midnight.weekday())
        return (monday_midnight + timedelta).tz_convert("UTC")

    def close_time_utc(self, date: pd.Timestamp) -> pd.Timestamp:
        """Return the close time in UTC for a given date."""
        timedelta = pd.Timedelta(minutes=self.end_abs_minutes())
        midnight = date.tz_convert(self.tz).normalize()
        monday_midnight = midnight - pd.Timedelta(days=midnight.weekday())
        return (monday_midnight + timedelta).tz_convert("UTC")


class MarketsActorConfig(ActorConfig, frozen=True):
    """
    Configuration for ``MarketsActor`` instances.
    """

    markets: dict[str, MarketInfoActorConfig]
    bar_type_historical: str
    bar_type_live: str
    blackout_window: MarketBlackoutWindowConfig | None = None
    markets_data_history: PositiveInt = 10
    use_wicks: bool = True
    client_id: ClientId | None = None
    log_session_changed: bool = False
    log_session_high_low: bool = False
    log_break_above_below: bool = False
    log_broken_both_above_below: bool = False


@dataclass
class MarketData:
    name: str
    open_datetime: pd.Timestamp
    close_datetime: pd.Timestamp
    use_wicks: bool
    # current session
    active: bool
    session_high_price: float = -math.inf
    session_high_datetime: int = 0
    changed_high: bool = False
    session_low_price: float = math.inf
    session_low_datetime: int = 0
    changed_low: bool = False
    markets_breaked_above: list[MarketData] = field(default_factory=list)
    markets_breaked_below: list[MarketData] = field(default_factory=list)
    # after market close
    break_above: bool = False
    break_above_datetime: int = -math.inf
    break_above_market: str | None = None
    break_above_triggered: bool = False

    break_below: bool = False
    break_below_datetime: int = math.inf
    break_below_market: str | None = None
    break_below_triggered: bool = False

    @classmethod
    def create(
        cls, market, open_datetime, close_datetime, use_wicks: bool = False, active: bool = False
    ) -> MarketData:
        return cls(
            name=market,
            open_datetime=open_datetime,
            close_datetime=close_datetime,
            use_wicks=use_wicks,
            active=active,
        )

    def handle_bar_from_active_market(self, bar: Bar) -> None:
        high, low = self._maxmin_price(bar)
        self.changed_high = False
        self.changed_low = False
        # check high
        if high > self.session_high_price:
            self.session_high_price = high
            self.session_high_datetime = bar.ts_event
            self.changed_high = True
        # check low
        if low < self.session_low_price:
            self.session_low_price = low
            self.session_low_datetime = bar.ts_event
            self.changed_low = True

    def handle_bar_from_closed_market(self, bar: Bar, market: str) -> None:
        high, low = self._maxmin_price(bar)
        both_breaked = False

        # check break below
        if not self.break_below and low < self.session_low_price:
            self.break_below = True
            self.break_below_datetime = bar.ts_event
            self.break_below_market = market
            if self.break_above:
                both_breaked = True

        # check break above
        if not self.break_above and high >= self.session_high_price:
            self.break_above = True
            self.break_above_datetime = bar.ts_event
            self.break_above_market = market
            if self.break_below:
                both_breaked = True

        return both_breaked

    def _maxmin_price(self, bar: Bar) -> tuple[float, float]:
        return maxmin_price(bar=bar, use_wicks=self.use_wicks)


class MarketsActor(Actor):
    config: MarketsActorConfig

    def __init__(self, config: MarketsActorConfig) -> None:
        super().__init__(config)
        self._map_on_data = {
            HistoricalBarData: self.on_historical_bar,
            LiveBarData: self.on_bar,
        }
        self._next_epoch_recalculate_market: dict[InstrumentId, int] = {}
        self._current_market: dict[InstrumentId, str | None] = {}
        self._markets_open: dict[InstrumentId, OrderedDict[int, str]] = {}
        self._markets_data_history: dict[InstrumentId, deque[MarketData]] = {}

    def on_start(self) -> None:
        client_id = self.config.client_id

        self.subscribe_data(DataType(HistoricalBarData), client_id=client_id)
        self.subscribe_data(DataType(LiveBarData), client_id=client_id)

    def on_stop(self) -> None:
        client_id = self.config.client_id

        self.unsubscribe_data(DataType(HistoricalBarData), client_id=client_id)
        self.unsubscribe_data(DataType(LiveBarData), client_id=client_id)

    def on_historical_data(self, data) -> None:
        self._map_on_data.get(type(data), lambda x: None)(data)

    def on_data(self, data) -> None:
        self._map_on_data.get(type(data), lambda x: None)(data)

    def on_historical_bar(self, data: HistoricalBarData) -> None:
        if self.config.bar_type_historical not in str(data.bar_type):
            return

        if data.instrument_id not in self._markets_data_history:
            self._next_epoch_recalculate_market[data.instrument_id] = 0
            self._current_market[data.instrument_id] = None
            self._markets_open[data.instrument_id] = OrderedDict()
            self._markets_data_history[data.instrument_id] = deque(
                maxlen=self.config.markets_data_history
            )
        bar = self.cache.bar(data.bar_type)
        self._process_bar(bar)

    def on_bar(self, data: LiveBarData) -> None:
        if self.config.bar_type_live not in str(data.bar_type):
            return
        if data.instrument_id not in self._markets_data_history:
            self._next_epoch_recalculate_market[data.instrument_id] = 0
            self._current_market[data.instrument_id] = None
            self._markets_open[data.instrument_id] = OrderedDict()
            self._markets_data_history[data.instrument_id] = deque(
                maxlen=self.config.markets_data_history
            )
        bar = self.cache.bar(data.bar_type)
        self._process_bar(bar)

    def _calc_markets_open(self, timestamp: int) -> None:
        """Return the markets that are open today."""
        date = unix_nanos_to_dt(timestamp)
        yesterday = date - pd.Timedelta(days=1)
        markets_open: dict[int, str] = {}

        blackout_window = self.config.blackout_window
        min_blackout_start = pd.Timestamp.max.tz_localize("UTC")
        max_blackout_end = pd.Timestamp.min.tz_localize("UTC")
        if blackout_window:
            min_blackout_start = blackout_window.open_time_utc(date)
            max_blackout_end = blackout_window.close_time_utc(date)
            markets_open[dt_to_unix_nanos(min_blackout_start)] = BLACKOUT_WINDOW

        for market, config in self.config.markets.items():
            open_time = config.open_time_utc(date)
            if open_time < min_blackout_start or open_time >= max_blackout_end:
                markets_open[dt_to_unix_nanos(open_time)] = market
            open_time_yesterday = config.open_time_utc(yesterday)
            if open_time_yesterday < min_blackout_start or open_time_yesterday >= max_blackout_end:
                markets_open[dt_to_unix_nanos(open_time_yesterday)] = market

        return OrderedDict(sorted(markets_open.items()))

    def _process_bar(
        self,
        bar: Bar,
    ) -> str | None:
        """
        Devuelve el mercado activo en `now`.

        Usa bisect_right sobre las claves (open_times) ya ordenadas del
        OrderedDict: el mercado activo es el de la mayor open_time <= now.
        """
        instrument_id = bar.bar_type.instrument_id
        if bar.ts_event > self._next_epoch_recalculate_market[instrument_id]:
            markets_open = self._calc_markets_open(bar.ts_event)
            self._markets_open[instrument_id] = markets_open
            self._next_epoch_recalculate_market[instrument_id] = list(markets_open.keys())[-2]

        ts_open, current_market = self._find_current_market(
            self._markets_open[instrument_id], bar.ts_event
        )
        if current_market != self._current_market[instrument_id]:
            # current_market changed, create new market
            self._current_market[instrument_id] = current_market
            if self.config.log_session_changed:
                self.log.info(
                    f"{instrument_id} -> Current market changed to: {self._current_market[instrument_id]} @ {unix_nanos_to_dt(bar.ts_event)}"
                )
            next_market_epoch = self._find_next_market_epoch(
                self._markets_open[instrument_id], ts_open
            )
            market_data = MarketData.create(
                market=current_market,
                open_datetime=unix_nanos_to_dt(ts_open),
                close_datetime=unix_nanos_to_dt(next_market_epoch) if next_market_epoch else None,
                use_wicks=self.config.use_wicks,
                active=False,
            )
            market_data.active = True
            if self._markets_data_history[instrument_id]:
                self._markets_data_history[instrument_id][0].active = False
                self._publish_closed_market_data(
                    instrument_id,
                    self._markets_data_history[instrument_id][0],
                    close_time=bar.ts_event,
                )
            self._markets_data_history[instrument_id].appendleft(market_data)
            market_data.handle_bar_from_active_market(bar)
        else:
            # update current market data
            market_data = self._markets_data_history[instrument_id][0]
            market_data.handle_bar_from_active_market(bar)
            if market_data.changed_high:
                self._publish_new_session_high_data(instrument_id, market_data)
            if market_data.changed_low:
                self._publish_new_session_low_data(instrument_id, market_data)

        to_remove = []
        for i in range(1, len(self._markets_data_history[instrument_id])):
            closed_market_data = self._markets_data_history[instrument_id][i]
            both_breaked = closed_market_data.handle_bar_from_closed_market(
                bar, market=current_market
            )
            if closed_market_data.break_above and not closed_market_data.break_above_triggered:
                closed_market_data.break_above_triggered = True
                market_data.markets_breaked_above.append(closed_market_data)
                self._publish_market_break_above_data(
                    instrument_id, market_data, closed_market_data
                )
            if closed_market_data.break_below and not closed_market_data.break_below_triggered:
                closed_market_data.break_below_triggered = True
                market_data.markets_breaked_below.append(closed_market_data)
                self._publish_market_break_below_data(
                    instrument_id, market_data, closed_market_data
                )
            if both_breaked:
                to_remove.append(i)
        for i in reversed(to_remove):
            removed_market = self._markets_data_history[instrument_id][i]
            if self.config.log_broken_both_above_below:
                self.log.info(
                    f"Market {removed_market.name} has broken both above and below, removing from history."
                )
            del self._markets_data_history[instrument_id][i]

    def _find_current_market(
        self, markets_open: OrderedDict[int, str], timestamp: int
    ) -> str | None:
        """Return the previous market that opened before the given timestamp."""
        keys = list(markets_open.keys())
        i = bisect_left(keys, timestamp)
        if i == 0:
            return None
        ts_open = keys[i - 1]
        return ts_open, markets_open[ts_open]

    def _find_next_market_epoch(
        self, markets_open: OrderedDict[int, str], timestamp: int
    ) -> int | None:
        """Return the next market epoch that will open after the given timestamp."""
        keys = list(markets_open.keys())
        i = bisect_right(keys, timestamp)
        if i == len(keys):
            return None
        return keys[i]

    def _find_next_market(self, markets_open: OrderedDict[int, str], timestamp: int) -> str | None:
        """Return the next market that will open after the given timestamp."""
        next_epoch = self._find_next_market_epoch(markets_open, timestamp)
        if next_epoch is None:
            return None
        return markets_open[next_epoch]

    def _publish_closed_market_data(
        self, instrument_id: InstrumentId, market_data: MarketData, close_time: int | None = None
    ) -> None:
        data = ClosedMarketData(
            instrument_id=instrument_id,
            market=market_data.name,
            high_price=market_data.session_high_price,
            low_price=market_data.session_low_price,
            open_datetime=dt_to_unix_nanos(market_data.open_datetime),
            close_datetime=dt_to_unix_nanos(market_data.close_datetime)
            if market_data.close_datetime
            else close_time,
            color=self.config.markets[market_data.name].color
            if market_data.name in self.config.markets
            else "#3051E2",
        )
        self.publish_data(DataType(ClosedMarketData), data)

    def _publish_market_break_above_data(
        self, instrument_id: InstrumentId, market_data: MarketData, closed_market_data: MarketData
    ) -> None:
        data = MarketBreakAboveData(
            instrument_id=instrument_id,
            market=market_data.name,
            session_high_price=market_data.session_high_price,
            session_low_price=market_data.session_low_price,
            markets_rebased_on_session=",".join(
                [m.name for m in market_data.markets_breaked_above]
            ),
            price_market_rebased=closed_market_data.session_high_price,
            ts_market_rebased=dt_to_unix_nanos(closed_market_data.open_datetime),
            ts_init=dt_to_unix_nanos(market_data.open_datetime),
            ts_event=dt_to_unix_nanos(market_data.session_high_datetime),
        )
        self.publish_data(DataType(MarketBreakAboveData), data)
        if self.config.log_break_above_below:
            self.log.info(
                f"{instrument_id} -> Market {market_data.name} broke above market {closed_market_data.name} with price {closed_market_data.session_high_price} @ {unix_nanos_to_dt(closed_market_data.break_above_datetime)}"
            )

    def _publish_market_break_below_data(
        self, instrument_id: InstrumentId, market_data: MarketData, closed_market_data: MarketData
    ) -> None:
        data = MarketBreakBelowData(
            instrument_id=instrument_id,
            market=market_data.name,
            session_high_price=market_data.session_high_price,
            session_low_price=market_data.session_low_price,
            markets_rebased_on_session=",".join(
                [m.name for m in market_data.markets_breaked_below]
            ),
            price_market_rebased=closed_market_data.session_low_price,
            ts_market_rebased=dt_to_unix_nanos(closed_market_data.open_datetime),
            ts_init=dt_to_unix_nanos(market_data.open_datetime),
            ts_event=dt_to_unix_nanos(market_data.session_low_datetime),
        )
        self.publish_data(DataType(MarketBreakBelowData), data)
        if self.config.log_break_above_below:
            self.log.info(
                f"{instrument_id} -> Market {market_data.name} broke below market {closed_market_data.name} with price {closed_market_data.session_low_price} @ {unix_nanos_to_dt(closed_market_data.break_below_datetime)}"
            )

    def _publish_new_session_high_data(
        self, instrument_id: InstrumentId, market_data: MarketData
    ) -> None:
        data = NewSessionHighData(
            instrument_id=instrument_id,
            market=market_data.name,
            price=market_data.session_high_price,
            ts_init=dt_to_unix_nanos(market_data.open_datetime),
            ts_event=dt_to_unix_nanos(market_data.session_high_datetime),
        )
        self.publish_data(DataType(NewSessionHighData), data)

        if self.config.log_session_high_low:
            self.log.info(
                f"{instrument_id} -> New high price for market {data.market}: {data.price} @ {unix_nanos_to_dt(data.ts_event)}"
            )

    def _publish_new_session_low_data(
        self, instrument_id: InstrumentId, market_data: MarketData
    ) -> None:
        data = NewSessionLowData(
            instrument_id=instrument_id,
            market=market_data.name,
            price=market_data.session_low_price,
            ts_init=dt_to_unix_nanos(market_data.open_datetime),
            ts_event=dt_to_unix_nanos(market_data.session_low_datetime),
        )
        self.publish_data(DataType(NewSessionLowData), data)
        if self.config.log_session_high_low:
            self.log.info(
                f"{instrument_id} -> New low price for market {data.market}: {data.price} @ {unix_nanos_to_dt(data.ts_event)}"
            )

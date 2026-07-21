from dataclasses import dataclass
from enum import Enum
from enum import unique

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from src.indicators.choc.choc import ChocIndicator
from src.indicators.choc.events import BosLine
from src.indicators.choc.events import BosPerfectPattern
from src.indicators.choc.events import BosWithoutMssPattern
from src.indicators.choc.events import ChocLine
from src.strategies.data.events import ClosedMarketData
from src.strategies.data.events import HistoricalBarData
from src.strategies.data.events import LiveBarData
from src.strategies.data.events import MarketBreakAboveData
from src.strategies.data.events import MarketBreakBelowData
from src.strategies.swing_bos.events import RecursiveMarketBreakData


@unique
class Sesgo(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class MarketBreakInfo:
    instrument_id: InstrumentId
    market: str
    weight: float
    session_price_peak: float
    ts_init: int
    ts_event: int
    pending_delete: bool = False
    sesgo: Sesgo = Sesgo.NEUTRAL

    def to_event(self) -> RecursiveMarketBreakData:
        color = "#00000000"
        if self.sesgo == Sesgo.BULLISH:
            color = "#2F9147"
        elif self.sesgo == Sesgo.BEARISH:
            color = "#A03030"
        return RecursiveMarketBreakData(
            instrument_id=self.instrument_id,
            market=self.market,
            color=color,
            ts_init=self.ts_init,
            ts_event=self.ts_event,
        )


class BreakMarketDetectorConfig(ActorConfig, frozen=True):
    """
    Configuration for ``SwingDetector`` instances.
    """

    bar_type_timedelta: pd.Timedelta
    client_id: ClientId | None = None
    choc_period: PositiveInt = 3
    choc_market_close_reset_bars: PositiveInt = 10


class BreakMarketDetectorActor(Actor):
    """
    An actor for detecting swings in the market.

    Parameters
    ----------
    config : BreakMarketDetectorConfig
        The configuration for the instance.

    """

    config: BreakMarketDetectorConfig

    def __init__(self, config: BreakMarketDetectorConfig) -> None:
        super().__init__(config)

        self._map_on_data = {
            MarketBreakAboveData: self.on_market_break_above,
            MarketBreakBelowData: self.on_market_break_below,
            ClosedMarketData: self.on_closed_market,
            HistoricalBarData: self.on_bar_data,
            LiveBarData: self.on_bar_data,
        }
        self._market_info: dict[InstrumentId, MarketBreakInfo] = {}
        self._choc: dict[InstrumentId, tuple[BarType, ChocIndicator]] = {}

    def on_start(self) -> None:
        client_id = self.config.client_id

        self.subscribe_data(DataType(MarketBreakAboveData), client_id=client_id)
        self.subscribe_data(DataType(MarketBreakBelowData), client_id=client_id)
        self.subscribe_data(DataType(ClosedMarketData), client_id=client_id)
        self.subscribe_data(DataType(HistoricalBarData), client_id=client_id)
        self.subscribe_data(DataType(LiveBarData), client_id=client_id)

    def on_stop(self) -> None:
        client_id = self.config.client_id

        self.unsubscribe_data(DataType(MarketBreakAboveData), client_id=client_id)
        self.unsubscribe_data(DataType(MarketBreakBelowData), client_id=client_id)
        self.unsubscribe_data(DataType(ClosedMarketData), client_id=client_id)
        self.unsubscribe_data(DataType(HistoricalBarData), client_id=client_id)
        self.unsubscribe_data(DataType(LiveBarData), client_id=client_id)

    def on_historical_data(self, data) -> None:
        self._map_on_data.get(type(data), lambda x: None)(data)

    def on_data(self, data) -> None:
        self._map_on_data.get(type(data), lambda x: None)(data)

    def on_bar_data(self, data: HistoricalBarData | LiveBarData) -> None:
        if pd.Timedelta(minutes=1) != data.bar_type.spec.timedelta:
            return
        if data.instrument_id not in self._choc:
            self._choc[data.instrument_id] = (
                data.bar_type,
                ChocIndicator(period=self.config.choc_period, use_wicks=False, min_bos_perc_diff=0),
            )
        bar_type, choc_indicator = self._choc[data.instrument_id]
        bar = self.cache.bar(bar_type)
        choc_indicator.handle_bar(bar)

        if choc_indicator.choc_triggered and choc_indicator.choc_triggered_duration > 0:
            bar_prev = self.cache.bar(bar_type, choc_indicator.choc_triggered_duration)
            data = ChocLine(
                instrument_id=data.instrument_id,
                open_datetime=bar_prev.ts_event,
                close_datetime=bar.ts_event,
                price=choc_indicator.choc_triggered_price,
                color="#C3DB38",
            )
            self.publish_data(DataType(ChocLine), data)

            if choc_indicator.bos_duration and choc_indicator.bos_duration[0] > 0:
                bar_mss = self.cache.bar(bar_type, choc_indicator.bos_duration[-1])
                data = BosLine(
                    instrument_id=data.instrument_id,
                    open_datetime=bar_mss.ts_event,
                    close_datetime=bar.ts_event,
                    price=choc_indicator.bos[-1],
                    color="#E933DA",
                )
                self.publish_data(DataType(BosLine), data)

        if choc_indicator.bos_triggered and choc_indicator.bos_duration[0] > 0:
            bar_prev = self.cache.bar(bar_type, choc_indicator.bos_duration[0])
            data = BosLine(
                instrument_id=data.instrument_id,
                open_datetime=bar_prev.ts_event,
                close_datetime=bar.ts_event,
                price=choc_indicator.bos[0],
                color="#0EB624" if choc_indicator.direction > 0 else "#E23030",
            )
            self.publish_data(DataType(BosLine), data)

            if len(choc_indicator.bos) == 1 and choc_indicator.choc_triggered_price:
                data = BosWithoutMssPattern(
                    instrument_id=data.instrument_id,
                    datetime=bar.ts_event,
                    color="#BFF347" if choc_indicator.direction > 0 else "#BFF34777",
                )
                self.publish_data(DataType(BosWithoutMssPattern), data)

            if len(choc_indicator.bos) > 1:
                mss = choc_indicator.bos[1]
                bos1 = choc_indicator.bos[0]
                choc_triggered = choc_indicator.choc_triggered_price
                if not choc_triggered:
                    return
                if (
                    choc_indicator.direction > 0
                    and mss < choc_triggered
                    and choc_triggered < bos1
                    or choc_indicator.direction < 0
                    and mss > choc_triggered
                    and choc_triggered > bos1
                ):
                    data = BosPerfectPattern(
                        instrument_id=data.instrument_id,
                        datetime=bar.ts_event,
                        color="#6892EC" if choc_indicator.direction > 0 else "#6892EC77",
                    )
                    self.publish_data(DataType(BosPerfectPattern), data)

    def on_market_break_above(self, data: MarketBreakAboveData) -> None:
        if (
            data.instrument_id in self._market_info
            and self._market_info[data.instrument_id].sesgo == Sesgo.BULLISH
        ):
            market_info = self._market_info[data.instrument_id]
            market_info.ts_event = data.ts_event
            if data.session_high_price > market_info.session_price_peak:
                # if data.recursive_markets_breaked_on_session == 0:
                #     # mark to finalize
                #     market_info.pending_delete = True
                # else:
                market_info.weight = data.recursive_markets_breaked_on_session
                market_info.session_price_peak = data.session_high_price
                market_info.ts_event = data.ts_event
                market_info.pending_delete = False
        elif data.recursive_markets_breaked_on_session > 0:
            if data.instrument_id in self._market_info:
                if self._market_info[data.instrument_id].sesgo == Sesgo.BEARISH:
                    # finalize previous market break
                    self._market_info[data.instrument_id].ts_event = data.ts_event
                    data_to_publish = self._market_info[data.instrument_id].to_event()
                    self.publish_data(DataType(RecursiveMarketBreakData), data_to_publish)
                    self._market_info.pop(data.instrument_id)
                else:
                    return
            market_info = MarketBreakInfo(
                instrument_id=data.instrument_id,
                market=data.market,
                weight=data.recursive_markets_breaked_on_session,
                session_price_peak=data.session_high_price,
                ts_init=data.ts_event,
                ts_event=data.ts_event,
                sesgo=Sesgo.BULLISH,
            )
            self._market_info[data.instrument_id] = market_info

    def on_market_break_below(self, data: MarketBreakBelowData) -> None:
        if (
            data.instrument_id in self._market_info
            and self._market_info[data.instrument_id].sesgo == Sesgo.BEARISH
        ):
            market_info = self._market_info[data.instrument_id]
            market_info.ts_event = data.ts_event
            if data.session_low_price < market_info.session_price_peak:
                # if data.recursive_markets_breaked_on_session == 0:
                #     # mark to finalize
                #     market_info.pending_delete = True
                # else:
                market_info.weight = data.recursive_markets_breaked_on_session
                market_info.session_price_peak = data.session_low_price
                market_info.pending_delete = False
        elif data.recursive_markets_breaked_on_session > 0:
            if data.instrument_id in self._market_info:
                if self._market_info[data.instrument_id].sesgo == Sesgo.BULLISH:
                    # finalize previous market break
                    self._market_info[data.instrument_id].ts_event = data.ts_event
                    data_to_publish = self._market_info[data.instrument_id].to_event()
                    self.publish_data(DataType(RecursiveMarketBreakData), data_to_publish)
                    self._market_info.pop(data.instrument_id)
                else:
                    return
            market_info = MarketBreakInfo(
                instrument_id=data.instrument_id,
                market=data.market,
                weight=data.recursive_markets_breaked_on_session,
                session_price_peak=data.session_low_price,
                ts_init=data.ts_event,
                ts_event=data.ts_event,
                sesgo=Sesgo.BEARISH,
            )
            self._market_info[data.instrument_id] = market_info

    def on_closed_market(self, data: ClosedMarketData) -> None:
        # if data.instrument_id  in self._choc:
        #     bar_type, choc_indicator = self._choc[data.instrument_id]
        #     choc_indicator.reset()
        #     for i in range(min(self.cache.bar_count(bar_type), self.config.choc_market_close_reset_bars), -1, -1):
        #         bar = self.cache.bar(bar_type, i)
        #         choc_indicator.handle_bar(bar)
        if data.instrument_id in self._market_info:
            market_info = self._market_info[data.instrument_id]
            if market_info and market_info.pending_delete:
                data_to_publish = market_info.to_event()
                self.publish_data(DataType(RecursiveMarketBreakData), data_to_publish)
                self._market_info.pop(data.instrument_id)

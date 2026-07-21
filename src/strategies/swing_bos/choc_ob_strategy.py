import pandas as pd
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading import Strategy
from nautilus_trader.trading.config import StrategyConfig

from src.indicators.choc.choc import ChocIndicator
from src.indicators.choc.events import BosLine
from src.indicators.choc.events import BosPerfectPattern
from src.indicators.choc.events import BosWithoutMssPattern
from src.indicators.choc.events import ChocLine
from src.indicators.swings.highlows import HighLowsIndicator
from src.strategies.data.events import ClosedMarketData
from src.strategies.data.events import HistoricalBarData
from src.strategies.data.events import LiveBarData
from src.strategies.data.events import MarketBreakAboveData
from src.strategies.data.events import MarketBreakBelowData


class ChocOBStrategyConfig(StrategyConfig, frozen=True):
    """
    Configuration for ``SwingDetector`` instances.
    """

    timedelta_choc: pd.Timedelta
    timedelta_highlows: pd.Timedelta
    client_id: ClientId | None = None
    choc_period: PositiveInt = 3


class ChocOBStrategy(Strategy):
    """
    An actor for detecting swings in the market.

    Parameters
    ----------
    config : ChocOBStrategyConfig
        The configuration for the instance.

    """

    config: ChocOBStrategyConfig

    def __init__(self, config: ChocOBStrategyConfig) -> None:
        super().__init__(config)

        self._map_on_data = {
            MarketBreakAboveData: self.on_market_break_above,
            MarketBreakBelowData: self.on_market_break_below,
            ClosedMarketData: self.on_closed_market,
            HistoricalBarData: self.on_bar_data,
            LiveBarData: self.on_bar_data,
        }
        self._choc: dict[InstrumentId, tuple[BarType, ChocIndicator]] = {}
        self._highlows: dict[InstrumentId, HighLowsIndicator] = {}

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
        bar = self.cache.bar(data.bar_type)

        if self.config.timedelta_choc != data.bar_type.spec.timedelta:
            if self.config.timedelta_highlows == data.bar_type.spec.timedelta:
                if data.instrument_id not in self._highlows:
                    self._highlows[data.instrument_id] = HighLowsIndicator(
                        period=5, use_wicks=True, history_length=100
                    )
                high_lows = self._highlows[data.instrument_id]
                high_lows.handle_bar(bar)
            return
        if data.instrument_id not in self._choc:
            self._choc[data.instrument_id] = (
                data.bar_type,
                ChocIndicator(period=self.config.choc_period, use_wicks=False, min_bos_perc_diff=0),
            )

        bar_type, choc_indicator = self._choc[data.instrument_id]
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
        pass

    def on_market_break_below(self, data: MarketBreakBelowData) -> None:
        pass

    def on_closed_market(self, data: ClosedMarketData) -> None:
        pass

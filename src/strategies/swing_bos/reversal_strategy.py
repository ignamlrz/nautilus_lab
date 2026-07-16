import pandas as pd
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.indicators import Swings
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading import Strategy
from nautilus_trader.trading.config import StrategyConfig

from src.strategies.data.events import ClosedMarketData
from src.strategies.data.events import HistoricalBarData
from src.strategies.data.events import LiveBarData
from src.strategies.data.events import MarketBreakAboveData
from src.strategies.data.events import MarketBreakBelowData
from src.strategies.data.events import TelegramTextData
from src.strategies.swing_bos.events import SwingsData


class SwingBosReversalStrategyConfig(StrategyConfig, frozen=True):
    """
    Configuration for ``SwingDetector`` instances.
    """

    bar_type_timedelta: pd.Timedelta
    client_id: ClientId | None = None
    period: PositiveInt = 30
    log_data: bool = True


class SwingBosReversalStrategy(Strategy):
    """
    An actor for detecting swings in the market.

    Parameters
    ----------
    config : SwingBosReversalStrategyConfig
        The configuration for the instance.

    """

    config: SwingBosReversalStrategyConfig

    def __init__(self, config: SwingBosReversalStrategyConfig) -> None:
        super().__init__(config)

        self._map_on_data = {
            HistoricalBarData: self.on_historical_bar,
            LiveBarData: self.on_bar,
            MarketBreakAboveData: self.on_market_break_above,
            MarketBreakBelowData: self.on_market_break_below,
            ClosedMarketData: self.on_closed_market,
        }
        self._swings: dict[InstrumentId, Swings] = {}
        self._markets_broken: dict[
            InstrumentId, MarketBreakAboveData | MarketBreakBelowData | None
        ] = {}

    def on_start(self) -> None:
        client_id = self.config.client_id

        self.subscribe_data(DataType(HistoricalBarData), client_id=client_id)
        self.subscribe_data(DataType(LiveBarData), client_id=client_id)
        self.subscribe_data(DataType(MarketBreakAboveData), client_id=client_id)
        self.subscribe_data(DataType(MarketBreakBelowData), client_id=client_id)
        self.subscribe_data(DataType(ClosedMarketData), client_id=client_id)

    def on_stop(self) -> None:
        client_id = self.config.client_id

        self.unsubscribe_data(DataType(HistoricalBarData), client_id=client_id)
        self.unsubscribe_data(DataType(LiveBarData), client_id=client_id)
        self.unsubscribe_data(DataType(MarketBreakAboveData), client_id=client_id)
        self.unsubscribe_data(DataType(MarketBreakBelowData), client_id=client_id)
        self.unsubscribe_data(DataType(ClosedMarketData), client_id=client_id)

    def on_historical_data(self, data) -> None:
        self._map_on_data.get(type(data), lambda x: None)(data)

    def on_data(self, data) -> None:
        self._map_on_data.get(type(data), lambda x: None)(data)

    def on_historical_bar(self, data: HistoricalBarData) -> None:
        self._process_bar(data)

    def on_bar(self, data: LiveBarData) -> None:
        self._process_bar(data)

    def on_market_break_above(self, data: MarketBreakAboveData) -> None:
        self._markets_broken[data.instrument_id] = data

    def on_market_break_below(self, data: MarketBreakBelowData) -> None:
        self._markets_broken[data.instrument_id] = data

    def on_closed_market(self, data: ClosedMarketData) -> None:
        pass
        # self._markets_broken[data.instrument_id] = None

    def _process_bar(self, data: HistoricalBarData | LiveBarData) -> None:
        if self.config.bar_type_timedelta != data.bar_type.spec.timedelta:
            return

        if data.instrument_id not in self._swings:
            self._swings[data.instrument_id] = Swings(self.config.period)
            self._markets_broken[data.instrument_id] = None

        bar = self.cache.bar(data.bar_type)
        swings = self._swings[data.instrument_id]
        market_broken = self._markets_broken[data.instrument_id]
        swings.handle_bar(bar)

        if swings.initialized and swings.changed and market_broken:
            if swings.direction == -1 and isinstance(market_broken, MarketBreakAboveData):
                self._markets_broken[data.instrument_id] = None
                diff_ts = bar.ts_event - market_broken.ts_market_rebased
                text = f"Swing reversal confirmed: ⬇️ (#{swings.duration} bars) | Price: {market_broken.price_market_rebased} | Duration: {pd.Timedelta(diff_ts, unit='ns')}"
                swing_data = SwingsData(
                    instrument_id=bar.bar_type.instrument_id,
                    bar_type=bar.bar_type,
                    order_side=OrderSide.SELL,
                    high_price=market_broken.session_high_price,
                    low_price=market_broken.session_low_price,
                    tested_price=market_broken.price_market_rebased,
                    duration=swings.duration,
                    label=f"{text} | Broken Price: {market_broken.session_high_price}",
                    ts_init=bar.ts_event,
                    ts_event=bar.ts_event,
                )
                self.publish_data(DataType(SwingsData), swing_data)

                telegram_data = TelegramTextData(
                    instrument_id=data.instrument_id,
                    type="INFO",
                    label=f"{market_broken.market} breaks above: [{market_broken.markets_rebased_on_session}]",
                    text=text,
                    ts_init=bar.ts_event,
                    ts_event=bar.ts_event,
                )
                self.publish_data(DataType(TelegramTextData), telegram_data)

            elif swings.direction == 1 and isinstance(market_broken, MarketBreakBelowData):
                self._markets_broken[data.instrument_id] = None
                diff_ts = bar.ts_event - market_broken.ts_market_rebased
                text = f"Swing reversal confirmed: ⬆️ (#{swings.duration} bars) | Price: {market_broken.price_market_rebased} | Duration: {pd.Timedelta(diff_ts, unit='ns')}"
                swing_data = SwingsData(
                    instrument_id=bar.bar_type.instrument_id,
                    bar_type=bar.bar_type,
                    order_side=OrderSide.BUY,
                    high_price=market_broken.session_high_price,
                    low_price=market_broken.session_low_price,
                    tested_price=market_broken.price_market_rebased,
                    duration=swings.duration,
                    label=f"{text} | Broken Price: {market_broken.session_low_price}",
                    ts_init=bar.ts_event,
                    ts_event=bar.ts_event,
                )
                self.publish_data(DataType(SwingsData), swing_data)

                telegram_data = TelegramTextData(
                    instrument_id=data.instrument_id,
                    type="INFO",
                    label=f"{market_broken.market} breaks below: [{market_broken.markets_rebased_on_session}]",
                    text=text,
                    ts_init=bar.ts_event,
                    ts_event=bar.ts_event,
                )
                self.publish_data(DataType(TelegramTextData), telegram_data)

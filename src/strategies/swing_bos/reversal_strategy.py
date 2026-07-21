from dataclasses import dataclass

import pandas as pd
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.indicators import Swings
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading import Strategy
from nautilus_trader.trading.config import StrategyConfig

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
from src.strategies.data.events import TelegramTextData
from src.strategies.swing_bos.events import RecursiveMarketBreakData
from src.strategies.swing_bos.events import SwingsData


@dataclass
class MarketBreakInfo:
    recursive_weight: float
    session_peak: float
    ts_init: int
    ts_event: int
    pending_delete: bool = False

    def to_event(
        self, instrument_id: InstrumentId, market: str, color: str
    ) -> RecursiveMarketBreakData:
        return RecursiveMarketBreakData(
            instrument_id=instrument_id,
            market=market,
            color=color,
            ts_init=self.ts_init,
            ts_event=self.ts_event,
        )


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
        self._last_mba: dict[InstrumentId, MarketBreakInfo | None] = {}
        self._last_mbb: dict[InstrumentId, MarketBreakInfo | None] = {}
        self._choc: dict[InstrumentId, ChocIndicator] = {}

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
        if data.is_principal:
            mba = self._last_mba.get(data.instrument_id, None)
            if mba:
                if (
                    data.session_high_price > mba.session_peak
                    and data.recursive_markets_breaked_on_session < mba.recursive_weight
                ):
                    # finalizing mba
                    mba.ts_event = data.ts_event
                    mba.pending_delete = True
                elif data.recursive_markets_breaked_on_session > mba.recursive_weight:
                    self._last_mba[data.instrument_id] = MarketBreakInfo(
                        recursive_weight=data.recursive_markets_breaked_on_session,
                        session_peak=data.session_high_price,
                        ts_init=mba.ts_init,
                        ts_event=data.ts_event,
                    )
            elif data.recursive_markets_breaked_on_session > 0:
                mbb = self._last_mbb.get(data.instrument_id, None)
                if mbb:
                    if mbb.recursive_weight > data.recursive_markets_breaked_on_session:
                        return
                    else:
                        mbb.pending_delete = True
                self._last_mba[data.instrument_id] = MarketBreakInfo(
                    recursive_weight=data.recursive_markets_breaked_on_session,
                    session_peak=data.session_high_price,
                    ts_init=data.ts_event,
                    ts_event=data.ts_event,
                )
        self._markets_broken[data.instrument_id] = data

    def on_market_break_below(self, data: MarketBreakBelowData) -> None:
        if data.is_principal:
            mbb = self._last_mbb.get(data.instrument_id, None)
            if mbb:
                if (
                    data.session_low_price < mbb.session_peak
                    and data.recursive_markets_breaked_on_session < mbb.recursive_weight
                ):
                    # finalizing mbb
                    mbb.ts_event = data.ts_event
                    mbb.pending_delete = True
                elif data.recursive_markets_breaked_on_session > mbb.recursive_weight:
                    self._last_mbb[data.instrument_id] = MarketBreakInfo(
                        recursive_weight=data.recursive_markets_breaked_on_session,
                        session_peak=data.session_low_price,
                        ts_init=mbb.ts_init,
                        ts_event=data.ts_event,
                    )
            elif data.recursive_markets_breaked_on_session > 0:
                mba = self._last_mba.get(data.instrument_id, None)
                if mba:
                    if mba.recursive_weight > data.recursive_markets_breaked_on_session:
                        return
                    else:
                        mba.pending_delete = True
                self._last_mbb[data.instrument_id] = MarketBreakInfo(
                    recursive_weight=data.recursive_markets_breaked_on_session,
                    session_peak=data.session_low_price,
                    ts_init=data.ts_event,
                    ts_event=data.ts_event,
                )
        self._markets_broken[data.instrument_id] = data

    def on_closed_market(self, data: ClosedMarketData) -> None:
        if data.instrument_id in self._last_mba:
            mba = self._last_mba[data.instrument_id]
            if mba and mba.pending_delete:
                data_to_publish = mba.to_event(data.instrument_id, data.market, "#2F9147")
                self.publish_data(DataType(RecursiveMarketBreakData), data_to_publish)
                self._last_mba[data.instrument_id] = None
        if data.instrument_id in self._last_mbb:
            mbb = self._last_mbb[data.instrument_id]
            if mbb and mbb.pending_delete:
                data_to_publish = mbb.to_event(data.instrument_id, data.market, "#A03030")
                self.publish_data(DataType(RecursiveMarketBreakData), data_to_publish)
                self._last_mbb[data.instrument_id] = None
        pass
        # self._markets_broken[data.instrument_id] = None

    def _process_bar(self, data: HistoricalBarData | LiveBarData) -> None:
        if self.config.bar_type_timedelta != data.bar_type.spec.timedelta:
            return

        if data.instrument_id not in self._swings:
            self._swings[data.instrument_id] = Swings(self.config.period)
            self._markets_broken[data.instrument_id] = None
            self._last_mba[data.instrument_id] = None
            self._last_mbb[data.instrument_id] = None
            self._choc[data.instrument_id] = ChocIndicator(period=3, use_wicks=False)

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

        choc = self._choc.get(data.instrument_id)
        choc.handle_bar(bar)

        if choc.choc_triggered:
            bar_prev = self.cache.bar(bar.bar_type, choc.choc_triggered_duration)
            data = ChocLine(
                instrument_id=data.instrument_id,
                open_datetime=bar_prev.ts_event,
                close_datetime=bar.ts_event,
                price=choc.choc_triggered_price,
                color="#C3DB38",
            )
            self.publish_data(DataType(ChocLine), data)

            if choc.bos_duration:
                bar_mss = self.cache.bar(bar.bar_type, choc.bos_duration[-1])
                data = BosLine(
                    instrument_id=data.instrument_id,
                    open_datetime=bar_mss.ts_event,
                    close_datetime=bar.ts_event,
                    price=choc.bos[-1],
                    color="#E933DA",
                )
                self.publish_data(DataType(BosLine), data)

        if choc.bos_triggered:
            bar_prev = self.cache.bar(bar.bar_type, choc.bos_duration[0])
            data = BosLine(
                instrument_id=data.instrument_id,
                open_datetime=bar_prev.ts_event,
                close_datetime=bar.ts_event,
                price=choc.bos[0],
                color="#0EB624" if choc.direction > 0 else "#E23030",
            )
            self.publish_data(DataType(BosLine), data)

            if len(choc.bos) == 1 and choc.choc_triggered_price:
                data = BosWithoutMssPattern(
                    instrument_id=data.instrument_id,
                    datetime=bar.ts_event,
                    color="#BFF347" if choc.direction > 0 else "#BFF34777",
                )
                self.publish_data(DataType(BosWithoutMssPattern), data)

            if len(choc.bos) > 1:
                mss = choc.bos[1]
                bos1 = choc.bos[0]
                choc_triggered = choc.choc_triggered_price
                if not choc_triggered:
                    return
                if (
                    choc.direction > 0
                    and mss < choc_triggered
                    and choc_triggered < bos1
                    or choc.direction < 0
                    and mss > choc_triggered
                    and choc_triggered > bos1
                ):
                    data = BosPerfectPattern(
                        instrument_id=data.instrument_id,
                        datetime=bar.ts_event,
                        color="#6892EC" if choc.direction > 0 else "#6892EC77",
                    )
                    self.publish_data(DataType(BosPerfectPattern), data)

from typing import Any

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import InstrumentStatus
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import OrderBookDepth10
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument


class OrderBookSpoofingDetectorConfig(ActorConfig, frozen=True):
    """
    Configuration for ``OrderBookSpoofingDetector`` instances.
    """

    instrument_ids: list[InstrumentId]
    client_id: ClientId | None = None
    subscribe_book_deltas: bool = False
    subscribe_book_depth: bool = False
    subscribe_book_at_interval: bool = False
    subscribe_trades: bool = False
    subscribe_funding_rates: bool = False
    subscribe_instrument: bool = False
    subscribe_instrument_status: bool = False
    subscribe_instrument_close: bool = False
    subscribe_params: dict[str, Any] | None = None
    can_unsubscribe: bool = True
    request_instruments: bool = False
    request_book_snapshot: bool = False
    request_book_deltas: bool = False
    request_trades: bool = False
    request_funding_rates: bool = False
    request_params: dict[str, Any] | None = None
    requests_start_delta: pd.Timedelta | None = None
    book_type: BookType = BookType.L2_MBP
    book_depth: PositiveInt | None = None
    book_interval_ms: PositiveInt = 1000
    book_levels_to_print: PositiveInt = 10
    manage_book: bool = True
    use_pyo3_book: bool = False
    log_data: bool = True


class OrderBookSpoofingDetector(Actor):
    """
    An actor for detecting order book spoofing.

    Parameters
    ----------
    config : OrderBookSpoofingDetectorConfig
        The configuration for the instance.

    """

    def __init__(self, config: OrderBookSpoofingDetectorConfig) -> None:
        super().__init__(config)

        self._books: dict[InstrumentId, OrderBook] = {}

    def on_start(self) -> None:  # noqa: C901 (too complex)
        """
        Actions to be performed when the actor is started.
        """
        # Determine requests start
        requests_start_delta = self.config.requests_start_delta or pd.Timedelta(hours=1)
        requests_start = self.clock.utc_now() - requests_start_delta

        client_id = self.config.client_id

        if self.config.request_instruments:
            venues = set()

            for instrument_id in self.config.instrument_ids or []:
                venues.add(instrument_id.venue)

            for venue in venues:
                self.request_instruments(
                    venue=venue,
                    client_id=client_id,
                    params=self.config.request_params,
                )

        for instrument_id in self.config.instrument_ids or []:
            if self.config.subscribe_instrument:
                self.subscribe_instrument(instrument_id)

            if self.config.subscribe_book_deltas:
                self.subscribe_order_book_deltas(
                    instrument_id=instrument_id,
                    book_type=self.config.book_type,
                    client_id=client_id,
                    pyo3_conversion=self.config.use_pyo3_book,
                )

                if self.config.manage_book:
                    if self.config.use_pyo3_book:
                        self.setup_book_pyo3(instrument_id)
                    else:
                        self.setup_book(instrument_id)

            if self.config.subscribe_book_at_interval:
                self.subscribe_order_book_at_interval(
                    instrument_id=instrument_id,
                    book_type=self.config.book_type,
                    depth=self.config.book_depth or 0,
                    interval_ms=self.config.book_interval_ms,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_book_depth:
                self.subscribe_order_book_depth(
                    instrument_id=instrument_id,
                    book_type=self.config.book_type,
                    depth=self.config.book_depth or 10,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_trades:
                self.subscribe_trade_ticks(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_funding_rates:
                self.subscribe_funding_rates(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_instrument_status:
                self.subscribe_instrument_status(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_instrument_close:
                self.subscribe_instrument_close(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.request_book_snapshot:
                self.request_order_book_snapshot(
                    instrument_id=instrument_id,
                    limit=self.config.book_depth or 0,
                    client_id=client_id,
                    params=self.config.request_params,
                )

            if self.config.request_book_deltas:
                self.request_order_book_deltas(
                    instrument_id=instrument_id,
                    start=requests_start,
                    client_id=client_id,
                    params=self.config.request_params,
                )

            if self.config.request_trades:
                self.request_trade_ticks(
                    instrument_id=instrument_id,
                    start=requests_start,
                    client_id=client_id,
                    params=self.config.request_params,
                )

            if self.config.request_funding_rates:
                funding_start = self.clock.utc_now() - pd.Timedelta(days=7)
                self.request_funding_rates(
                    instrument_id=instrument_id,
                    start=funding_start,
                    client_id=client_id,
                    params=self.config.request_params,
                )

    def setup_book(self, instrument_id: InstrumentId) -> None:
        self._books[instrument_id] = OrderBook(instrument_id, self.config.book_type)

    def setup_book_pyo3(self, instrument_id: InstrumentId) -> None:
        book_type: nautilus_pyo3.BookType = nautilus_pyo3.BookType.L2_MBP
        pyo3_instrument_id = nautilus_pyo3.InstrumentId.from_str(instrument_id.value)
        self._books[pyo3_instrument_id] = nautilus_pyo3.OrderBook(pyo3_instrument_id, book_type)

    def on_stop(self) -> None:  # noqa: C901 (too complex)
        """
        Actions to be performed when the actor is stopped.
        """
        if not self.config.can_unsubscribe:
            return  # Unsubscribe not supported

        client_id = self.config.client_id

        for instrument_id in self.config.instrument_ids or []:
            if self.config.subscribe_instrument:
                self.unsubscribe_instrument(
                    instrument_id=instrument_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_book_deltas:
                self.unsubscribe_order_book_deltas(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_book_depth:
                self.unsubscribe_order_book_depth(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_book_at_interval:
                self.unsubscribe_order_book_at_interval(
                    instrument_id=instrument_id,
                    interval_ms=self.config.book_interval_ms,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_trades:
                self.unsubscribe_trade_ticks(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_funding_rates:
                self.unsubscribe_funding_rates(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_instrument_status:
                self.unsubscribe_instrument_status(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

            if self.config.subscribe_instrument_close:
                self.unsubscribe_instrument_close(
                    instrument_id=instrument_id,
                    client_id=client_id,
                    params=self.config.subscribe_params,
                )

    def on_historical_data(self, data: Any) -> None:
        """
        Actions to be performed when the actor is running and receives historical data.
        """
        if self.config.log_data:
            self.log.info("Historical " + repr(data), LogColor.CYAN)

    def on_instrument(self, instrument: Instrument) -> None:
        """
        Actions to be performed when the actor receives an instrument.
        """
        if self.config.log_data:
            self.log.info(repr(instrument), LogColor.CYAN)

    def on_instruments(self, instruments: list[Instrument]) -> None:
        """
        Actions to be performed when the actor receives multiple instruments.
        """
        if self.config.log_data:
            self.log.info(f"Received <Instrument[{len(instruments)}]> data", LogColor.CYAN)
            for instrument in instruments:
                self.log.info(repr(instrument), LogColor.CYAN)

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """
        Actions to be performed when the actor is running and receives order book
        deltas.
        """
        if self.config.manage_book:
            book = self._books[deltas.instrument_id]
            book.apply_deltas(deltas)

            if self.config.log_data:
                num_levels = self.config.book_levels_to_print
                self.log.info(
                    f"\n{book.instrument_id}\n{book.pprint(num_levels)}",
                    LogColor.CYAN,
                )
        elif self.config.log_data:
            self.log.info(repr(deltas), LogColor.CYAN)

    def on_order_book_depth(self, depth: OrderBookDepth10) -> None:
        """
        Actions to be performed when the actor is running and receives order book depth.
        """
        if self.config.log_data:
            self.log.info(repr(depth), LogColor.CYAN)

    def on_order_book(self, order_book: OrderBook) -> None:
        """
        Actions to be performed when an order book update is received.
        """
        if self.config.log_data:
            num_levels = self.config.book_levels_to_print
            self.log.info(
                f"\n{order_book.instrument_id}\n{order_book.pprint(num_levels)}",
                LogColor.CYAN,
            )

    def on_trade_tick(self, trade: TradeTick) -> None:
        """
        Actions to be performed when the actor is running and receives a trade.
        """
        if self.config.log_data:
            self.log.info(repr(trade), LogColor.CYAN)
        book = self.cache.order_book(trade.instrument_id)
        book_best_bid_size = book.best_bid_size()
        book_best_ask_size = book.best_ask_size()
        if not book_best_bid_size or not book_best_ask_size:
            return
        instrument = self.cache.instrument(book.instrument_id)
        if not instrument:
            return
        midpoint = book.midpoint()
        diff_price = midpoint * 0.00015
        if trade.aggressor_side == AggressorSide.BUYER:
            top_price = instrument.make_price(midpoint + diff_price)
            signal_size = book.get_quantity_for_price(top_price, OrderSide.BUY)
        else:
            bottom_price = instrument.make_price(midpoint - diff_price)
            signal_size = book.get_quantity_for_price(bottom_price, OrderSide.SELL)
        if trade.size > signal_size:
            self.log.info(
                f"{trade.instrument_id} -> [{trade.aggressor_side.name}]: {trade.size} @ {trade.price} | signal_size was {signal_size}",
                LogColor.NORMAL,
            )
            r1 = self.get_book_order_ratio(book, instrument, diff=0.015)
            r2 = self.get_book_order_ratio(book, instrument, diff=0.07)
            r3 = self.get_book_order_ratio(book, instrument, diff=0.33)
            if r1 is not None and r2 is not None and r3 is not None:
                if r1 < 0.475 and r2 < 0.475 and r3 < 0.475:
                    self.log.info(f"{trade.instrument_id} -> [Buy Signal Detected]", LogColor.GREEN)
                elif r1 > 0.525 and r2 > 0.525 and r3 > 0.525:
                    self.log.info(f"{trade.instrument_id} -> [Sell Signal Detected]", LogColor.RED)

    # generate method get the relation_qty given a float (for example 0.015) and make a log like this quantity given 0.015 - Botton Qty: 100 @ 100.0 | Top Qty: 200 @ 101.0
    def get_book_order_ratio(
        self, book: OrderBook, instrument: Instrument, diff: float = 0.01
    ) -> float | None:
        midpoint = book.midpoint()
        if not midpoint:
            return None
        diff_price = midpoint * diff
        botton_price = instrument.make_price(midpoint - diff_price)
        botton_qty = instrument.make_qty(book.get_quantity_for_price(botton_price, OrderSide.SELL))
        botton_avg_px = instrument.make_price(
            book.get_avg_px_for_quantity(botton_qty, OrderSide.SELL)
        )
        botton_money = botton_qty * botton_avg_px
        top_price = instrument.make_price(midpoint + diff_price)
        top_qty = instrument.make_qty(book.get_quantity_for_price(top_price, OrderSide.BUY))
        top_avg_px = instrument.make_price(book.get_avg_px_for_quantity(top_qty, OrderSide.BUY))
        top_money = top_qty * top_avg_px
        total_money = botton_money + top_money
        ratio_money = botton_money / total_money if total_money > 0 else None

        log_color = LogColor.NORMAL
        signal = None
        if ratio_money < 0.475:
            log_color = LogColor.GREEN
            signal = "LONG"
        elif ratio_money > 0.525:
            log_color = LogColor.RED
            signal = "SHORT"
        if signal:
            self.log.info(
                f"{book.instrument_id} -> [Book Order Ratio ({diff:.2%})] Ratio {ratio_money:.2%} | Signal {signal} | Botton {botton_money:,}$ | Top {top_money:,}$",
                log_color,
            )
        return ratio_money

    def on_instrument_status(self, data: InstrumentStatus) -> None:
        """
        Actions to be performed when the actor is running and receives an instrument
        status update.
        """
        if self.config.log_data:
            self.log.info(repr(data), LogColor.CYAN)

    def on_funding_rate(self, funding_rate: FundingRateUpdate) -> None:
        """
        Actions to be performed when the actor is running and receives a funding rate
        update.
        """
        if self.config.log_data:
            self.log.info(repr(funding_rate), LogColor.CYAN)

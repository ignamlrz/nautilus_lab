import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.core.data import Data
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import Swings
from nautilus_trader.indicators import VolumeWeightedAveragePrice
from nautilus_trader.indicators.averages import MovingAverageType
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import InstrumentStatus
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import OrderBookDepth10
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import InstrumentClass
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument


@customdataclass
class OrderBookLiquidityData(Data):
    instrument_id: InstrumentId
    label: str
    order_side: str
    ratios: str


@dataclass
class OpeningMarketData(Data):
    tz: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    next_epoch: pd.Timestamp
    high_price: float
    low_price: float
    rebased_top: bool = False
    rebased_low: bool = False
    rebased_top_length: int = math.inf
    rebased_low_length: int = math.inf
    last_high_length: int = 0
    last_low_length: int = 0
    active: bool = False

    @classmethod
    def create(cls, market_data: dict, bar: Bar):
        midnight = unix_nanos_to_dt(bar.ts_event).tz_convert(market_data["tz"]).normalize()
        midnight_next = (
            (unix_nanos_to_dt(bar.ts_event) + pd.Timedelta(days=1))
            .tz_convert(market_data["tz"])
            .normalize()
        )
        start_date = (midnight + market_data["start"]).tz_convert("UTC")
        end_date = (midnight + market_data["end"]).tz_convert("UTC")
        next_epoch = (midnight_next + market_data["start"]).tz_convert("UTC")
        bar_on_market = (bar.ts_event >= start_date.value) and (bar.ts_event < end_date.value)
        return cls(
            tz=market_data["tz"],
            start_date=start_date,
            end_date=end_date,
            next_epoch=next_epoch,
            high_price=bar.high if bar_on_market else -math.inf,
            low_price=bar.low if bar_on_market else math.inf,
        )


MINIMUM_PRICE = {
    (AssetClass.CRYPTOCURRENCY, InstrumentClass.SPOT): 25_000,
    (AssetClass.CRYPTOCURRENCY, InstrumentClass.SWAP): 20_000,
}

MARKETS = {
    "SSE/SZSE": {
        "tz": "Asia/Hong_Kong",
        "start": pd.Timedelta(hours=8, minutes=0),
        "end": pd.Timedelta(hours=15, minutes=0),
        "min_diff": 0.004,
    },
    "LSE": {
        "tz": "Europe/London",
        "start": pd.Timedelta(hours=8, minutes=0),
        "end": pd.Timedelta(hours=15, minutes=0),
        "min_diff": 0.004,
    },
    "NYSE": {
        "tz": "America/New_York",
        "start": pd.Timedelta(hours=9, minutes=30),
        "end": pd.Timedelta(hours=16, minutes=0),
        "min_diff": 0.004,
    },
    "POST_NYSE": {
        "tz": "America/New_York",
        "start": pd.Timedelta(hours=16, minutes=0),
        "end": pd.Timedelta(hours=20, minutes=0),
        "min_diff": 0.002,
    },
}


class OrderBookLiquidityDetectorConfig(ActorConfig, frozen=True):
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


class OrderBookLiquidityDetector(Actor):
    """
    An actor for detecting order book spoofing.

    Parameters
    ----------
    config : OrderBookSpoofingDetectorConfig
        The configuration for the instance.

    """

    def __init__(self, config: OrderBookLiquidityDetectorConfig) -> None:
        super().__init__(config)

        self._books: dict[InstrumentId, OrderBook] = {}
        self._atr: dict[InstrumentId, AverageTrueRange] = {}
        self._vwap: dict[InstrumentId, VolumeWeightedAveragePrice] = {}
        self._swings30m: dict[InstrumentId, Swings] = {}
        self._swings1h: dict[InstrumentId, Swings] = {}
        self._swings_greater: dict[InstrumentId, Swings] = {}
        self._opening_market_data: dict[InstrumentId, dict[str, OpeningMarketData]] = {}
        self._open_markets: dict[InstrumentId, list[str]] = {}

    def _notify_signal(self, label: str, instrument_id: InstrumentId, ratios: list[float]) -> None:
        order_side = (
            OrderSide.BUY
            if "BUY" in label
            else (OrderSide.SELL if "SELL" in label else OrderSide.NO_ORDER_SIDE)
        )
        ratios_str = ", ".join([f"{r:.2%}" for r in ratios])
        data = OrderBookLiquidityData(
            instrument_id=instrument_id,
            label=label,
            order_side=order_side,
            ratios=ratios_str,
            ts_event=self.clock.timestamp_ns(),
            ts_init=self.clock.timestamp_ns(),
        )
        self.publish_data(DataType(OrderBookLiquidityData), data)

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
            self.setup_indicators(instrument_id)

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

    def setup_indicators(self, instrument_id: InstrumentId) -> None:
        self._vwap[instrument_id] = vwap = VolumeWeightedAveragePrice()
        self._atr[instrument_id] = atr = AverageTrueRange(
            period=60, ma_type=MovingAverageType.WILDER
        )
        self._swings30m[instrument_id] = swings30m = Swings(period=30)
        self._swings1h[instrument_id] = swings1h = Swings(period=60)
        self._swings_greater[instrument_id] = swings_greater = Swings(period=240)
        bar_type = BarType.from_str(f"{instrument_id.value}-1-MINUTE-LAST-EXTERNAL")
        self.register_indicator_for_bars(bar_type, vwap)
        self.register_indicator_for_bars(bar_type, atr)
        self.register_indicator_for_bars(bar_type, swings30m)
        self.register_indicator_for_bars(bar_type, swings1h)
        self.register_indicator_for_bars(bar_type, swings_greater)
        self.request_bars(
            bar_type=bar_type,
            start=self.clock.utc_now() - pd.Timedelta(minutes=1440),
            client_id=self.config.client_id,
            callback=lambda _: self.subscribe_bars(
                bar_type=bar_type,
                client_id=self.config.client_id,
            ),
        )

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

    def create_opening_market_data(self, bar: Bar) -> None:
        """
        Create opening market data for the given instrument.
        """
        if bar.bar_type.instrument_id not in self._opening_market_data:
            self._opening_market_data[bar.bar_type.instrument_id] = {}
            self._open_markets[bar.bar_type.instrument_id] = []
        for market in MARKETS:
            if market not in self._opening_market_data[bar.bar_type.instrument_id]:
                self._opening_market_data[bar.bar_type.instrument_id][market] = (
                    OpeningMarketData.create(market_data=MARKETS[market], bar=bar)
                )

    def build_opening_market_data(self, bar: Bar) -> None:
        """
        Actions to be performed when the actor is running and receives a bar.
        """
        open_markets = []
        if bar.bar_type.instrument_id not in self._opening_market_data:
            self.create_opening_market_data(bar)
        else:
            for market in self.open_markets_sorted(bar.bar_type.instrument_id):
                opening_data = self._opening_market_data[bar.bar_type.instrument_id][market]
                if bar.ts_event >= opening_data.next_epoch.value:
                    opening_data = OpeningMarketData.create(market_data=MARKETS[market], bar=bar)
                    self._opening_market_data[bar.bar_type.instrument_id][market] = opening_data
                bar_on_market = (bar.ts_event >= opening_data.start_date.value) and (
                    bar.ts_event < opening_data.end_date.value
                )
                if bar_on_market:
                    opening_data.active = True
                    if bar.high > opening_data.high_price:
                        opening_data.high_price = bar.high
                        opening_data.last_high_length = 0
                    else:
                        opening_data.last_high_length += 1
                    if bar.low < opening_data.low_price:
                        opening_data.low_price = bar.low
                        opening_data.last_low_length = 0
                    else:
                        opening_data.last_low_length += 1
                    open_markets.append(market)
                else:
                    opening_data.active = False
                    opening_data.last_low_length += 1
                    opening_data.last_high_length += 1
                    if opening_data.rebased_low:
                        opening_data.rebased_low_length += 1
                    elif bar.low <= opening_data.low_price and not math.isinf(
                        opening_data.low_price
                    ):
                        opening_data.rebased_low = True
                        opening_data.rebased_low_length = 0

                    if opening_data.rebased_top:
                        opening_data.rebased_top_length += 1
                    elif bar.high >= opening_data.high_price and not math.isinf(
                        opening_data.high_price
                    ):
                        opening_data.rebased_top = True
                        opening_data.rebased_top_length = 0

            self._open_markets[bar.bar_type.instrument_id] = open_markets

    def on_bar(self, bar: Bar) -> None:
        if "1-MINUTE" not in str(bar.bar_type.spec):
            return
        self.build_opening_market_data(bar)
        swing30m = self._swings30m[bar.bar_type.instrument_id]
        swing1h = self._swings1h[bar.bar_type.instrument_id]
        book = self.cache.order_book(bar.bar_type.instrument_id)
        instrument = self.cache.instrument(bar.bar_type.instrument_id)
        r1, s1 = self.get_book_order_ratio(book, instrument, diff=0.015)
        r2, s2 = self.get_book_order_ratio(book, instrument, diff=0.07)
        r3, s3 = self.get_book_order_ratio(book, instrument, diff=0.33)

        ratios = [r1, r2, r3]
        if swing1h.changed or swing30m.changed:
            market_rebased, data_rebased = self.most_recently_market_closed_rebased(
                bar.bar_type.instrument_id
            )
            market_open, data_open = self.most_recently_market_open(bar.bar_type.instrument_id)
            if market_rebased and data_rebased and market_open and data_open:
                last_high_bar = self.cache.bar(bar.bar_type, data_open.last_high_length)
                last_low_bar = self.cache.bar(bar.bar_type, data_open.last_low_length)
                diff = last_high_bar.high - last_low_bar.low
                diff_perp = diff / bar.close
                num_bars_sessions = (
                    int(
                        (self.clock.utc_now() - data_open.start_date).value
                        / pd.Timedelta(minutes=1).value
                    )
                    - 1
                )
                bars_since_rebased = min(num_bars_sessions, 150)
                if diff_perp > MARKETS[market_open]["min_diff"]:
                    if (
                        data_rebased.rebased_top_length < data_rebased.rebased_low_length
                        and data_rebased.rebased_top_length < bars_since_rebased
                        and (
                            (
                                swing1h.direction == -1
                                and swing1h.changed
                                and swing1h.since_high == data_rebased.rebased_top_length
                            )
                            or (
                                swing30m.direction == -1
                                and swing30m.changed
                                and swing30m.since_high == data_rebased.rebased_top_length
                            )
                        )
                    ):
                        ratios_str = ", ".join([f"{r:.2%}" for r in ratios])
                        self._notify_signal(
                            f"CHoCH confirmed on 1m: ⬇️ (#{swing1h.since_low} bars) | Market rebased: {market_rebased} | OB: [{ratios_str}]",
                            bar.bar_type.instrument_id,
                            ratios=ratios,
                        )
                    elif (
                        data_rebased.rebased_low_length < data_rebased.rebased_top_length
                        and data_rebased.rebased_low_length < bars_since_rebased
                        and (
                            (
                                swing1h.direction == 1
                                and swing1h.changed
                                and swing1h.since_low == data_rebased.rebased_low_length
                            )
                            or (
                                swing30m.direction == 1
                                and swing30m.changed
                                and swing30m.since_low == data_rebased.rebased_low_length
                            )
                        )
                    ):
                        ratios_str = ", ".join([f"{r:.2%}" for r in ratios])
                        self._notify_signal(
                            f"CHoCH confirmed on 1m: ⬆️ (#{swing1h.since_high} bars) | Market rebased {market_rebased} | OB: [{ratios_str}]",
                            bar.bar_type.instrument_id,
                            ratios=ratios,
                        )
        atr = self._atr[bar.bar_type.instrument_id].value

        final_decision_order = OrderSide.NO_ORDER_SIDE
        final_decision_label = ""
        for m in self.open_markets_sorted(bar.bar_type.instrument_id):
            opening_data = self._opening_market_data[bar.bar_type.instrument_id][m]
            if opening_data.active:
                recently_top = (
                    opening_data.last_high_length >= 0 and opening_data.last_high_length < 60
                )
                recently_bottom = (
                    opening_data.last_low_length >= 0 and opening_data.last_low_length < 60
                )
            else:
                recently_top = opening_data.rebased_top and opening_data.rebased_top_length < 60
                recently_bottom = opening_data.rebased_low and opening_data.rebased_low_length < 60

            if recently_bottom and (
                (swing1h.direction == 1 and swing1h.duration < 15)
                or (swing30m.direction == 1 and swing30m.duration < 15)
            ):
                amplitude = bar.high - bar.low
                high_wick_amplitude = bar.high - bar.close
                body_amplitude = bar.open - bar.close if bar.open > bar.close else Decimal("0")
                if amplitude.as_double() > atr * 1.3 and body_amplitude > high_wick_amplitude:
                    final_decision_label = (
                        f"Liquidity Collected on {m} confirmed with swing trading - [BUY]"
                    )
                    final_decision_order = OrderSide.BUY

            if recently_top and (
                (swing1h.direction == -1 and swing1h.duration < 15)
                or (swing30m.direction == -1 and swing30m.duration < 15)
            ):
                amplitude = bar.high - bar.low
                low_wick_amplitude = bar.close - bar.low
                body_amplitude = bar.close - bar.open if bar.close > bar.open else Decimal("0")
                if amplitude.as_double() > atr * 1.3 and body_amplitude > low_wick_amplitude:
                    final_decision_label = (
                        f"Liquidity Collected on {m} confirmed with swing trading - [SELL]"
                    )
                    final_decision_order = OrderSide.SELL

        if final_decision_order == OrderSide.NO_ORDER_SIDE:
            return
        self.log.info(
            f"{bar.bar_type.instrument_id} -> {final_decision_label} ({r1:.2%}, {r2:.2%}, {r3:.2%})",
            LogColor.RED if final_decision_order == OrderSide.SELL else LogColor.GREEN,
        )
        self._notify_signal(final_decision_label, instrument.id, [r1, r2, r3])

    def on_historical_data(self, data: Any) -> None:
        """
        Actions to be performed when the actor is running and receives historical data.
        """
        if isinstance(data, Bar):
            if "BTCUSDT" in str(data.bar_type):
                swing30m = self._swings30m[data.bar_type.instrument_id]
                swing1h = self._swings1h[data.bar_type.instrument_id]
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
            self.build_opening_market_data(data)
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
        open_markets = self._open_markets.get(trade.instrument_id, [])
        if not open_markets:
            return
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
        minimum_size = instrument.make_qty(
            MINIMUM_PRICE[(instrument.asset_class, instrument.instrument_class)] / midpoint
        )
        bar_type = BarType.from_str(f"{trade.instrument_id}-1-MINUTE-LAST-EXTERNAL")
        last_cached_bar = self.cache.bar(bar_type)
        if not last_cached_bar:
            return
        volume15s_size = instrument.make_qty(last_cached_bar.volume / 4)
        if trade.aggressor_side == AggressorSide.BUYER:
            top_price = instrument.make_price(midpoint + diff_price)
            ob_size = instrument.make_qty(book.get_quantity_for_price(top_price, OrderSide.BUY))
        else:
            bottom_price = instrument.make_price(midpoint - diff_price)
            ob_size = instrument.make_qty(book.get_quantity_for_price(bottom_price, OrderSide.SELL))
        signal_size = min(volume15s_size, ob_size)
        # ignore lower trades
        if trade.size < minimum_size or trade.size < signal_size:
            return
        r1, s1 = self.get_book_order_ratio(book, instrument, diff=0.015)
        r2, s2 = self.get_book_order_ratio(book, instrument, diff=0.07)
        r3, s3 = self.get_book_order_ratio(book, instrument, diff=0.33)
        vwap = self._vwap[trade.instrument_id].value
        atr = self._atr[trade.instrument_id].value

        markets_rebased = set()
        multiple_markets = len(open_markets) > 1
        market_rebased_order_side = OrderSide.NO_ORDER_SIDE
        final_decision_order = OrderSide.NO_ORDER_SIDE
        final_decision_label = ""
        for market in self.open_markets_sorted(trade.instrument_id):
            opening_data = self._opening_market_data[trade.instrument_id][market]
            if market in open_markets:
                top_length = opening_data.last_high_length
                low_length = opening_data.last_low_length
                recently_rebased_top = (
                    opening_data.last_high_length > 0 and opening_data.last_high_length < 5
                )
                recently_rebased_low = (
                    opening_data.last_low_length > 0 and opening_data.last_low_length < 5
                )
            else:
                top_length = opening_data.rebased_top_length
                low_length = opening_data.rebased_low_length
                recently_rebased_top = (
                    opening_data.rebased_top and top_length > 0 and top_length < 5
                )
                recently_rebased_low = (
                    opening_data.rebased_low and low_length > 0 and low_length < 5
                )
            # both, betther not trade
            if recently_rebased_top and recently_rebased_low:
                markets_rebased.add(market)
                continue

            if recently_rebased_top:
                markets_rebased.add(market)
                next_bar = self.cache.bar(bar_type, top_length)
                bar = self.cache.bar(bar_type, top_length + 1)
                if (next_bar.high - next_bar.low) > (bar.high - bar.low):
                    bar = next_bar
                    next_bar = self.cache.bar(bar_type, top_length - 1)

                amplitude = bar.high - bar.low
                diff_body = bar.close - bar.open if bar.close > bar.open else Decimal("0")
                upper_wick = bar.high - bar.close
                wick_is_bigger = (amplitude.as_double() > atr * 2.2) and upper_wick > diff_body
                # reversal pattern
                if wick_is_bigger and (bar.close > next_bar.close or diff_body == Decimal("0")):
                    market_rebased_order_side = OrderSide.SELL

            if recently_rebased_low:
                markets_rebased.add(market)
                next_bar = self.cache.bar(bar_type, low_length)
                bar = self.cache.bar(bar_type, top_length + 1)
                if (next_bar.high - next_bar.low) > (bar.high - bar.low):
                    bar = next_bar
                    next_bar = self.cache.bar(bar_type, low_length - 1)

                amplitude = bar.high - bar.low
                diff_body = bar.open - bar.close if bar.close < bar.open else Decimal("0")
                lower_wick = bar.close - bar.low
                wick_is_bigger = (amplitude.as_double() > atr * 2.2) and lower_wick > diff_body
                # reversal pattern
                if wick_is_bigger and (bar.close < next_bar.close or diff_body == Decimal("0")):
                    market_rebased_order_side = OrderSide.BUY

            if market_rebased_order_side != OrderSide.NO_ORDER_SIDE:
                break

        if market_rebased_order_side == OrderSide.NO_ORDER_SIDE:
            return

        swing30m = self._swings30m[trade.instrument_id]
        swing1h = self._swings1h[trade.instrument_id]
        # its rare see all BUY, maybe FOMO
        if market_rebased_order_side == OrderSide.BUY:
            if (
                trade.aggressor_side == AggressorSide.SELLER
                and s1 == OrderSide.SELL
                and s2 == OrderSide.SELL
                and s3 == OrderSide.SELL
                and (swing30m.direction == 1 or swing1h.direction == 1 or multiple_markets)
            ):
                final_decision_order = OrderSide.BUY
                final_decision_label = (
                    f"Liquidity Collected on {', '.join(markets_rebased)} - [BUY]"
                )
            if trade.aggressor_side == AggressorSide.SELLER and (
                (s1 != OrderSide.SELL and s2 != OrderSide.BUY and s3 == OrderSide.BUY)
                or (s1 == OrderSide.BUY and s2 == OrderSide.NO_ORDER_SIDE and s3 != OrderSide.SELL)
                and (swing30m.direction == 1 or swing1h.direction == 1)
                and not multiple_markets
            ):
                final_decision_order = OrderSide.BUY
                final_decision_label = f"Continue trend on {', '.join(markets_rebased)} - [BUY]"

        # its rare see all BUY, maybe FOMO
        if market_rebased_order_side == OrderSide.SELL:
            if (
                trade.aggressor_side == AggressorSide.BUYER
                and s1 == OrderSide.BUY
                and s2 == OrderSide.BUY
                and s3 == OrderSide.BUY
                and (swing30m.direction == -1 or swing1h.direction == -1 or multiple_markets)
            ):
                final_decision_order = OrderSide.SELL
                final_decision_label = (
                    f"Liquidity Collected on {', '.join(markets_rebased)} - [SELL]"
                )
            if trade.aggressor_side == AggressorSide.BUYER and (
                (s1 != OrderSide.BUY and s2 != OrderSide.SELL and s3 == OrderSide.SELL)
                or (s1 == OrderSide.SELL and s2 == OrderSide.NO_ORDER_SIDE and s3 != OrderSide.BUY)
                and (swing30m.direction == -1 or swing1h.direction == -1)
                and not multiple_markets
            ):
                final_decision_order = OrderSide.SELL
                final_decision_label = f"Continue trend on {', '.join(markets_rebased)} - [SELL]"

        if final_decision_order != OrderSide.NO_ORDER_SIDE:
            self.log.info(
                f"{trade.instrument_id} -> {final_decision_label} ({r1:.2%}, {r2:.2%}, {r3:.2%})",
                LogColor.RED if final_decision_order == OrderSide.SELL else LogColor.GREEN,
            )
            self._notify_signal(final_decision_label, trade.instrument_id, [r1, r2, r3])

        self.log.info(
            f"{trade.instrument_id} -> [{trade.aggressor_side.name}]: "
            f"{trade.size} @ {trade.price} (> {signal_size * midpoint:.2f}$) "
            f"({s1.name}, {s2.name}, {s3.name}) | "
            f"VWAP: {vwap} | "
            f"ATR: {atr} | "
            f"Swing30m: {swing30m.direction} ({swing30m.length}) | "
            f"Swing1h: {swing1h.direction} ({swing1h.length}) | ",
            LogColor.NORMAL,
        )

    # generate method get the relation_qty given a float (for example 0.015) and make a log like this quantity given 0.015 - Botton Qty: 100 @ 100.0 | Top Qty: 200 @ 101.0
    def get_book_order_ratio(
        self, book: OrderBook, instrument: Instrument, diff: float = 0.01
    ) -> tuple[float | None, OrderSide]:
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

        signal = OrderSide.NO_ORDER_SIDE
        if ratio_money < 0.475:
            signal = OrderSide.BUY
        elif ratio_money > 0.525:
            signal = OrderSide.SELL
        return ratio_money, signal

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

    def open_markets_sorted(self, instrument_id: InstrumentId) -> list[str]:
        """
        Returns the list of opening markets for the given instrument.
        """
        markets = self._opening_market_data.get(instrument_id, {})
        open_markets = self._open_markets.get(instrument_id, [])
        return [m for m in markets if m not in open_markets] + [
            m for m in markets if m in open_markets
        ]

    def most_recently_market_closed_rebased(
        self, instrument_id: InstrumentId
    ) -> tuple[str | None, OpeningMarketData | None]:
        """
        Returns the list of markets that have most recently closed and rebased for the given instrument.
        """
        markets = self._opening_market_data.get(instrument_id, {})
        most_recently_data = most_recently_market = None
        min_distance = math.inf
        for market in markets:
            opening_data = markets[market]
            if opening_data.active:
                continue
            if opening_data.rebased_top and opening_data.rebased_top_length < min_distance:
                min_distance = opening_data.rebased_top_length
                most_recently_data = opening_data
                most_recently_market = market
            elif opening_data.rebased_low and opening_data.rebased_low_length < min_distance:
                min_distance = opening_data.rebased_low_length
                most_recently_data = opening_data
                most_recently_market = market
        return most_recently_market, most_recently_data

    def most_recently_market_open(
        self, instrument_id: InstrumentId
    ) -> tuple[str | None, OpeningMarketData | None]:
        """
        Returns the list of markets that have most recently opened for the given instrument.
        """
        markets = self._opening_market_data.get(instrument_id, {})
        most_recently_data = most_recently_market = None
        most_recent = unix_nanos_to_dt(0)
        for market in markets:
            opening_data = markets[market]
            if not opening_data.active:
                continue
            if opening_data.start_date > most_recent:
                most_recent = opening_data.start_date
                most_recently_data = opening_data
                most_recently_market = market
        return most_recently_market, most_recently_data

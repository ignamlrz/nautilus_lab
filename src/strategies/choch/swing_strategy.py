from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core import UUID4
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.enums import order_side_from_str
from nautilus_trader.model.events.position import PositionClosed
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import PositionId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.trading import Strategy
from nautilus_trader.trading.config import StrategyConfig
from pydantic import PositiveInt

from src.notifications import SyncTelegramBridge
from src.notifications import TelegramNotifier
from src.strategies.choch.events import ChangeOfCharacterConfirmationData
from src.strategies.choch.events import OpenMarketData
from src.strategies.choch.events import SwingData


class ChangeOfCharacterStrategyConfig(StrategyConfig):
    client_id: ClientId | None = None
    signal_expiration: pd.Timedelta = pd.Timedelta(hours=2)
    signal_peak_rolling_window_period: PositiveInt = 2
    fib_negotiation: float = 0.382
    fib_limit_order: float = 0.618
    fib_market_order: float = 0.95
    fib_stop_loss: float = 1.3
    fib_take_profit: float = -3.1


@dataclass
class InstrumentState:
    market: str
    order_side: OrderSide
    bar_type: BarType
    # date state
    date_start: pd.Timestamp
    date_expiration: pd.Timestamp
    # signal state
    signal_low_price: Price
    signal_high_price: Price
    # price state
    price_negotiation: Price
    price_limit: Price
    price_market: Price
    price_stop_loss: Price
    price_take_profit: Price

    def update_from_state(self, state: InstrumentState):
        if self.order_side != state.order_side:
            raise ValueError(
                "Cannot update InstrumentState with different order side. "
                f"Current: {self.order_side}, New: {state.order_side}"
            )
        # update market
        self.market = state.market

        is_buy_with_lower_price_sl = (
            self.order_side == OrderSide.BUY and state.price_stop_loss < self.price_stop_loss
        )
        is_sell_with_higher_price_sl = (
            self.order_side == OrderSide.SELL and state.price_stop_loss > self.price_stop_loss
        )
        if is_buy_with_lower_price_sl or is_sell_with_higher_price_sl:
            # update dates
            self.date_start = state.date_start
            self.date_expiration = state.date_expiration
            # update signal prices
            self.signal_low_price = state.signal_low_price
            self.signal_high_price = state.signal_high_price
            # update prices
            self.price_negotiation = state.price_negotiation
            self.price_limit = state.price_limit
            self.price_market = state.price_market
            self.price_stop_loss = state.price_stop_loss
            self.price_take_profit = state.price_take_profit
            return True
        return False

    def update_from_bar(self, bar: Bar) -> None:
        if self.order_side == OrderSide.BUY:
            if bar.high > self.signal_high_price:
                self.price_stop_loss = bar.low
        elif self.order_side == OrderSide.SELL:
            if bar.high > self.price_stop_loss:
                self.price_stop_loss = bar.high


class ChangeOfCharacterStrategy(Strategy):
    config: ChangeOfCharacterStrategyConfig

    def __init__(self, config: ChangeOfCharacterStrategyConfig):
        super().__init__(config)
        self._telegram: SyncTelegramBridge | None = None
        self._instrument_state: dict[InstrumentId, InstrumentState] = {}
        self._swing_signal: dict[InstrumentId, SwingData] = {}
        self._choc_confirmation: dict[InstrumentId, ChangeOfCharacterConfirmationData] = {}

    def on_start(self):
        # Initialize the telegram bridge if running in live mode
        if isinstance(self.clock, LiveClock):
            self._telegram = SyncTelegramBridge(TelegramNotifier.from_env())

        # subscribe to swing data and change of character confirmation data
        self.subscribe_data(DataType(SwingData), client_id=self.config.client_id)
        # self.subscribe_data(
        #     DataType(ChangeOfCharacterConfirmationData), client_id=self.config.client_id
        # )
        # self.subscribe_data(DataType(OpenMarketData), client_id=self.config.client_id)

    def on_stop(self):
        # Close the telegram bridge first; idempotent.
        if self._telegram is not None:
            self._telegram.close()
            self._telegram = None

        for instrument_id in self._instrument_state:
            self._cancel_signal_for_instrument(instrument_id)

        # unsubscribe to swing data and change of character confirmation data
        self.unsubscribe_data(DataType(SwingData), client_id=self.config.client_id)
        # self.unsubscribe_data(
        #     DataType(ChangeOfCharacterConfirmationData), client_id=self.config.client_id
        # )
        # self.unsubscribe_data(DataType(OpenMarketData), client_id=self.config.client_id)

    def on_bar(self, bar: Bar):
        instrument_id = bar.bar_type.instrument_id
        if instrument_id not in self._swing_signal and instrument_id not in self._choc_confirmation:
            return
        # check for possible entry
        data = self._swing_signal[instrument_id]
        instrument = self.cache.instrument(instrument_id)
        if not instrument:
            self.log.warning(f"Instrument not found: {instrument_id}")
            return
        balance_free = self.portfolio.account(instrument.id.venue).balance_free(
            currency=instrument.quote_currency
        )
        if not balance_free or balance_free.as_decimal() <= 0:
            self.log.warning(f"It has not free balance on venue: {instrument.id.venue}")
            return
        exposure = self.portfolio.net_exposure(instrument.id)
        if exposure.as_decimal() != 0:
            for p in self.cache.positions_open(instrument_id=instrument.id):
                if p.side == PositionSide.LONG:
                    self.close_position(p)
                    self._notify_telegram(
                        f"Closing LONG position on instrument {instrument.id} due to SELL signal on order book ratio",
                        instrument.id,
                        text="",
                    )
                elif p.side == PositionSide.SHORT:
                    self.close_position(p)
                    self._notify_telegram(
                        f"Closing SHORT position on instrument {instrument.id} due to BUY signal on order book ratio",
                        instrument.id,
                        text="",
                    )
                    self.buy(instrument, balance_free, label=data.label)
                    self.sell(instrument, balance_free, label=data.label)
            self.log.warning(
                f"It has exposure on instrument: {instrument.id} with exposure: {exposure}"
            )
            return
        self.log.info(
            f"{instrument.id} -> Trying create {data.order_side.name} order with free balance: {balance_free}"
        )

    def on_data(self, data):
        if isinstance(data, SwingData):
            self.on_swing_data(data)

    def on_swing_data(self, data: SwingData):
        if data.order_side == OrderSide.NO_ORDER_SIDE:
            self._notify_telegram("ℹ️ INFO", data.instrument_id, data.label)
            return

        self._swing_signal[data.instrument_id] = data

        self._notify_telegram(data.label, data.instrument_id, text="")

        self._operate_on_swing(data)

        def callback(event: TimeEvent):
            if data.instrument_id in self._swing_signal:
                self._swing_signal.pop(data.instrument_id)
                self._notify_telegram(
                    "⚠️ WARNING",
                    data.instrument_id,
                    text="Signal expired after 4 hours.",
                )
                self.unsubscribe_bars(bar_type=data.bar_type, client_id=self.config.client_id)

        self.clock.set_time_alert(
            name=f"OrderbookStrategy:{data.instrument_id}",
            alert_time=self.clock.utc_now() + pd.Timedelta(hours=4),
            callback=callback,
            override=True,
        )

    def on_change_of_character_confirmation_data(self, data: ChangeOfCharacterConfirmationData):
        self._choc_confirmation[data.instrument_id] = data
        self._on_change_of_character_data(data)

    def on_open_market_data(self, data: OpenMarketData):
        pass

    def buy(self, instrument: Instrument, balance_free: Money, label: str):
        state = self._instrument_state[instrument.id]
        entry_price = instrument.make_price(state.price_limit)
        sl_price = instrument.make_price(state.price_stop_loss)
        tp_price = instrument.make_price(state.price_take_profit)
        quantity = instrument.make_qty((balance_free * 0.5) / entry_price)
        order_list = self.order_factory.bracket(
            instrument_id=instrument.id,
            order_side=OrderSide.BUY,
            quantity=quantity,
            # Entry order
            entry_order_type=OrderType.LIMIT,
            entry_price=entry_price,
            # Take-profit order
            tp_price=tp_price,
            # Stop-loss order
            sl_trigger_price=sl_price,
        )
        self.submit_order_list(
            order_list, position_id=PositionId(f"{instrument.id}:SWING:{UUID4()}")
        )
        text = f"● Entry Price: <code>{entry_price}</code> @ Size: <code>{quantity}</code>\n"
        text += f"● Stop-Loss Price: <code>{sl_price}</code>\n"
        text += f"● Take-Profit Price: <code>{tp_price}</code>"
        self._notify_telegram(f"{label} - Created", instrument.id, text)
        self._swing_signal.pop(instrument.id, None)
        return order_list

    def sell(self, instrument: Instrument, balance_free: Money, label: str):
        state = self._instrument_state[instrument.id]
        entry_price = instrument.make_price(state.price_limit)
        sl_price = instrument.make_price(state.price_stop_loss)
        tp_price = instrument.make_price(state.price_take_profit)
        quantity = instrument.make_qty((balance_free * 0.1) / entry_price)
        order_list = self.order_factory.bracket(
            instrument_id=instrument.id,
            order_side=OrderSide.SELL,
            quantity=quantity,
            # Entry order
            entry_order_type=OrderType.LIMIT,
            entry_price=entry_price,
            # Take-profit order
            tp_price=tp_price,
            # Stop-loss order
            sl_trigger_price=sl_price,
        )
        # write on bullets entry price @ quantity, sl price, tp price
        self.submit_order_list(
            order_list, position_id=PositionId(f"{instrument.id}:SWING:{UUID4()}")
        )
        text = f"● Entry Price: <code>{entry_price}</code> @ Size: <code>{quantity}</code>\n"
        text += f"● Stop-Loss Price: <code>{sl_price}</code>\n"
        text += f"● Take-Profit Price: <code>{tp_price}</code>"
        self._notify_telegram(f"{label} - Created", instrument.id, text)
        self._swing_signal.pop(instrument.id, None)
        return order_list

    def _operate_on_swing(self, data: SwingData) -> None:
        self._choc_confirmation[data.instrument_id] = data
        instrument_id = data.instrument_id
        instrument = self.cache.instrument(instrument_id)
        date_start = self.clock.utc_now()
        date_expiration = date_start + self.config.signal_expiration
        high_price = instrument.make_price(data.high_price)
        low_price = instrument.make_price(data.low_price)
        if data.order_side == OrderSide.BUY:
            price_start = low_price
            price_end = high_price
        elif data.order_side == OrderSide.SELL:
            price_start = high_price
            price_end = low_price
        else:
            self.log.warning(
                f"Received ChangeOfCharacterData with no order side for instrument: {instrument_id}. Ignoring."
            )
            return

        price_negotiation = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_negotiation
        )
        price_limit = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_limit_order
        )
        price_market = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_market_order
        )
        price_stop_loss = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_stop_loss
        )
        price_take_profit = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_take_profit
        )
        state = InstrumentState(
            market="",
            order_side=data.order_side,
            bar_type=BarType.from_str(str(data.bar_type)),
            # date state
            date_start=date_start,
            date_expiration=date_expiration,
            # signal state
            signal_low_price=low_price,
            signal_high_price=high_price,
            # price state
            price_negotiation=price_negotiation,
            price_limit=price_limit,
            price_market=price_market,
            price_stop_loss=price_stop_loss,
            price_take_profit=price_take_profit,
        )
        self._instrument_state[instrument_id] = state

        balance_free = self.portfolio.account(instrument.id.venue).balance_free(
            currency=instrument.quote_currency
        )
        if not balance_free or balance_free.as_decimal() <= 0:
            self.log.warning(f"It has not free balance on venue: {instrument.id.venue}")
            return
        exposure = self.portfolio.net_exposure(instrument.id)
        if exposure.as_decimal() != 0:
            self.log.warning(
                f"It has exposure on instrument: {instrument.id} with exposure: {exposure}"
            )
            if data.order_side == OrderSide.SELL:
                self.close_all_positions(
                    instrument_id=instrument.id,
                    client_id=self.config.client_id,
                    position_side=PositionSide.LONG,
                )
                self._notify_telegram(
                    f"Closing LONG position on instrument {instrument.id} due to SELL signal on swing",
                    instrument.id,
                    text="",
                )
            elif data.order_side == OrderSide.BUY:
                self.close_all_positions(
                    instrument_id=instrument.id,
                    client_id=self.config.client_id,
                    position_side=PositionSide.SHORT,
                )
                self._notify_telegram(
                    f"Closing SHORT position on instrument {instrument.id} due to BUY signal on swing",
                    instrument.id,
                    text="",
                )

            self.cancel_all_orders(
                instrument_id=instrument.id,
                client_id=self.config.client_id,
                order_side=data.order_side,
            )
            self._notify_telegram(
                f"Cancelling {data.order_side.name} order on instrument {instrument.id} due to new swing signal",
                instrument.id,
                text="",
            )
        self.log.info(
            f"{instrument.id} -> Trying create {data.order_side.name} order with free balance: {balance_free}"
        )
        if data.order_side == OrderSide.BUY:
            self.buy(instrument, balance_free, label=data.label)
        elif data.order_side == OrderSide.SELL:
            self.sell(instrument, balance_free, label=data.label)

    def on_position_closed(self, event: PositionClosed):
        self._notify_telegram(
            f"Position closed ({event.position_id}) on instrument {event.instrument_id} with side {event.side.name} and quantity {event.quantity}",
            event.instrument_id,
            text=f"● PnL: {event.realized_pnl}\n● Avg Open: {event.avg_px_open}\n● Avg Close: {event.avg_px_close}",
        )

    def _on_change_of_character_data(self, data: ChangeOfCharacterConfirmationData) -> None:
        instrument_id = data.instrument_id
        if instrument_id not in self._swing_signal:
            self.log.warning(
                f"Received ChangeOfCharacterData for instrument: {instrument_id} with no previous swing signal. Ignoring."
            )
            return
        swing_signal = self._swing_signal[instrument_id]
        if swing_signal.order_side != data.order_side:
            self.log.warning(
                f"Received ChangeOfCharacterData for instrument: {instrument_id} with different order side than previous swing signal. Ignoring."
            )
            return
        self._choc_confirmation[data.instrument_id] = data
        instrument = self.cache.instrument(instrument_id)
        date_start = self.clock.utc_now()
        date_expiration = date_start + self.config.signal_expiration
        high_price = instrument.make_price(data.high_price)
        low_price = instrument.make_price(data.low_price)
        if data.order_side == OrderSide.BUY:
            price_start = low_price
            price_end = high_price
        elif data.order_side == OrderSide.SELL:
            price_start = high_price
            price_end = low_price
        else:
            self.log.warning(
                f"Received ChangeOfCharacterData with no order side for instrument: {instrument_id}. Ignoring."
            )
            return

        price_negotiation = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_negotiation
        )
        price_limit = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_limit_order
        )
        price_market = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_market_order
        )
        price_stop_loss = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_stop_loss
        )
        price_take_profit = self._calc_retracement_fibonacci_price(
            instrument, price_start, price_end, self.config.fib_take_profit
        )
        state = InstrumentState(
            market=data.market,
            order_side=order_side_from_str(str(data.order_side)),
            bar_type=BarType.from_str(str(data.bar_type)),
            # date state
            date_start=date_start,
            date_expiration=date_expiration,
            # signal state
            signal_low_price=low_price,
            signal_high_price=high_price,
            # price state
            price_negotiation=price_negotiation,
            price_limit=price_limit,
            price_market=price_market,
            price_stop_loss=price_stop_loss,
            price_take_profit=price_take_profit,
        )

        set_time_alert = True
        if instrument_id in self._instrument_state:
            prev_state = self._instrument_state[instrument_id]
            if prev_state.order_side != state.order_side:
                self.log.info(
                    f"Received ChangeOfCharacterData for instrument: {instrument_id} with different order side. Unsubscribing bars and removing previous state."
                )
                self._cancel_signal_for_instrument(instrument_id)
                self._instrument_state[instrument_id] = state
            else:
                self.log.info(
                    f"Received ChangeOfCharacterData for instrument: {instrument_id} with same order side. Updating state."
                )
                orders_open = self.cache.orders_open(
                    instrument_id=instrument_id, side=state.order_side
                )
                if not orders_open:
                    set_time_alert = prev_state.update_from_state(state)
                else:
                    set_time_alert = False
        else:
            self.log.info(
                f"Received ChangeOfCharacterData for instrument: {instrument_id} with no previous state. Setting new state."
            )
            self._instrument_state[instrument_id] = state

        if set_time_alert:
            self.clock.set_time_alert(
                name=f"{data.instrument_id}:ChangeOfCharacterStrategy:SignalExpiration",
                alert_time=self.clock.utc_now() + self.config.signal_expiration,
                callback=self._cancel_signal_time_event,
                override=True,
            )

    def _calc_retracement_fibonacci_price(
        self,
        instrument: Instrument,
        price_start: Price | Decimal,
        price_end: Price | Decimal,
        fib: float,
    ) -> Price:
        diff = price_end - price_start
        price = price_end.as_decimal() - diff * Decimal(fib)
        return instrument.make_price(price)

    def _notify_telegram(self, label: str, instrument_id: InstrumentId, text: str) -> None:
        emoji = ""
        match label:
            case s if "BUY" in s:
                emoji = "🟢"
            case s if "SELL" in s:
                emoji = "🔴"
            case s if "INFO" in s:
                emoji = "ℹ️"
            case s if "WARNING" in s:
                emoji = "⚠️"
        text_telegram = f"<code>{instrument_id.venue}:{instrument_id.symbol}</code> - {emoji}\n<b>{label}</b>\n\n{text}"
        if not self._telegram:
            self.log.info(f"{label}{f'\n{text}' if text else ''}", LogColor.YELLOW)
            return
        self._telegram.send(text_telegram)

    def _cancel_signal_time_event(self, event: TimeEvent) -> None:
        instrument_id = Instrument.id(event.name.split(":")[0])
        self._cancel_signal_for_instrument(instrument_id)

    def _cancel_signal_for_instrument(self, instrument_id: InstrumentId) -> None:
        if instrument_id not in self._instrument_state:
            return
        state = self._instrument_state[instrument_id]
        position_side = (
            PositionSide.LONG if state.order_side == OrderSide.BUY else PositionSide.SHORT
        )
        positions_open = self.cache.positions_open(instrument_id=instrument_id, side=position_side)
        if not positions_open:
            orders_open = self.cache.orders_open(instrument_id=instrument_id, side=state.order_side)
            if orders_open:
                self.log.info(
                    f"Signal expired for instrument: {instrument_id} with open orders. Canceling orders (# {len(orders_open)}) and unsubscribing bars."
                )
                self.cancel_orders(orders_open, client_id=self.config.client_id)
            else:
                self.log.info(
                    f"Signal expired for instrument: {instrument_id} with no open orders."
                )
            self.unsubscribe_bars(state.bar_type, client_id=self.config.client_id)

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.common.enums import LogColor
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.enums import order_side_from_str
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.trading import Strategy
from nautilus_trader.trading.config import StrategyConfig
from pydantic import PositiveInt

from src.notifications import SyncTelegramBridge
from src.notifications import TelegramNotifier
from src.strategies.choch.events import ChangeOfCharacterConfirmationData


class ChangeOfCharacterStrategyConfig(StrategyConfig):
    client_id: ClientId | None = None
    signal_expiration: pd.Timedelta = pd.Timedelta(hours=1)
    signal_peak_rolling_window_period: PositiveInt = 2
    fib_negotiation: float = 0.382
    fib_limit_order: float = 0.5
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

    def update_from_bar(self, bar: Bar, instrument: Instrument) -> None:
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
        self._state: dict[InstrumentId, InstrumentState] = {}

    def on_start(self):
        # Initialize the telegram bridge if running in live mode
        if isinstance(self.clock, LiveClock):
            self._telegram = SyncTelegramBridge(TelegramNotifier.from_env())

        # subscribe to CHoCH data
        self.subscribe_data(DataType(ChangeOfCharacterData), client_id=self.config.client_id)
        self.submit_order

    def on_stop(self):
        # Close the telegram bridge first; idempotent.
        if self._telegram is not None:
            self._telegram.close()
            self._telegram = None

        for instrument_id in self._state.keys():
            self._cancel_signal_for_instrument(instrument_id)

        # unsubscribe to CHoCH data
        self.unsubscribe_data(DataType(ChangeOfCharacterData), client_id=self.config.client_id)

    def on_bar(self, bar: Bar):
        instrument_id = bar.bar_type.instrument_id
        if instrument_id not in self._ob_liquidity:
            return
        if bar.bar_type.spec.timedelta == pd.Timedelta(minutes=1):
            self.update_rsi(bar)
            self._ema_history[instrument_id].appendleft(self._ema[instrument_id].value)
        data = self._ob_liquidity[instrument_id]
        rsi = self._rsi[instrument_id]
        ema = self._ema[instrument_id]
        if not rsi.initialized or not ema.initialized:
            return
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
        ob = self.cache.order_book(instrument.id)
        r1, s1 = self.get_book_order_ratio(ob, instrument, 0.015)
        r3, s3 = self.get_book_order_ratio(ob, instrument, 0.33)
        if exposure.as_decimal() != 0:
            for p in self.cache.positions_open(instrument_id=instrument.id):
                if p.side == PositionSide.LONG and s3 == OrderSide.SELL:
                    self.close_position(p)
                    self.log.info(
                        f"Closing LONG position on instrument: {instrument.id} due to SELL signal from order book ratio: {r3:.2%}"
                    )
                    self._notify_telegram(
                        f"Closing LONG position on instrument {instrument.id} due to SELL signal on order book ratio",
                        instrument.id,
                        text=f"Order Book Ratio: {r3:.2%}",
                    )
                elif p.side == PositionSide.SHORT and s3 == OrderSide.BUY:
                    self.close_position(p)
                    self.log.info(
                        f"Closing SHORT position on instrument: {instrument.id} due to BUY signal from order book ratio: {r3:.2%}"
                    )
                    self._notify_telegram(
                        f"Closing SHORT position on instrument {instrument.id} due to BUY signal on order book ratio",
                        instrument.id,
                        text=f"Order Book Ratio: {r3:.2%}",
                    )
            self.log.warning(
                f"It has exposure on instrument: {instrument.id} with exposure: {exposure}"
            )
            return
        self.log.info(
            f"{instrument.id} -> Trying create {data.order_side.name} order with free balance: {balance_free} | OB Signal: [{s1.name}, {s3.name}] | RSI: {rsi.value:.2f} | EMA: {ema.value:.2f}"
        )
        if (
            data.order_side == OrderSide.BUY
            and s3 == OrderSide.BUY
            and s1 != OrderSide.SELL
            and self.cache.bar(bar.bar_type, 1).close < self._ema_history[instrument_id][1]
            and bar.close > ema.value
            and rsi.value < 0.5
        ):
            for i in range(5, 14):
                bar0 = self.cache.bar(bar.bar_type, i)
                bar1 = self.cache.bar(bar.bar_type, i + 1)
                ema0 = self._ema_history[instrument_id][i]
                ema1 = self._ema_history[instrument_id][i + 1]
                if bar0.low > ema0 and bar1.low > ema1:
                    self.buy(instrument, balance_free, label=data.label)
                    break
        elif (
            data.order_side == OrderSide.SELL
            and s3 == OrderSide.SELL
            and s1 != OrderSide.BUY
            and self.cache.bar(bar.bar_type, 1).close > self._ema_history[instrument_id][1]
            and bar.close < ema.value
            and rsi.value > 0.5
        ):
            for i in range(5, 14):
                bar0 = self.cache.bar(bar.bar_type, i)
                bar1 = self.cache.bar(bar.bar_type, i + 1)
                ema0 = self._ema_history[instrument_id][i]
                ema1 = self._ema_history[instrument_id][i + 1]
                if bar0.high < ema0 and bar1.high < ema1:
                    self.sell(instrument, balance_free, label=data.label)
                    break

    def on_data(self, data):
        if isinstance(data, ChangeOfCharacterData):
            # check if has indicators initialized
            if data.instrument_id not in self._rsi or data.instrument_id not in self._ema:
                self.setup_indicators(data.instrument_id)

            if data.order_side == OrderSide.NO_ORDER_SIDE:
                self._notify_telegram("ℹ️ INFO", data.instrument_id, data.label)
                return

            if not self.prev_bars_was_below_ema(data):
                self.log.info(
                    f"Skipping signal: {data.label} for instrument: {data.instrument_id} because previous bars were not below/above EMA"
                )
                text = f"Skipping signal: {data.label} for instrument: {data.instrument_id} because previous bars were not below/above EMA"
                self._notify_telegram(data.label, data.instrument_id, text=text)
                return
            self._ob_liquidity[data.instrument_id] = data
            self.log.info(
                f"Received signal: {data.label} for instrument: {data.instrument_id} with ratios: {data.ratios}"
            )
            self._notify_telegram(data.label, data.instrument_id, text=f"Ratios: {data.ratios}")
            self.clock.set_time_alert(
                name=f"OrderbookStrategy:{data.instrument_id}",
                alert_time=self.clock.utc_now() + pd.Timedelta(minutes=10, seconds=10),
                callback=lambda e: self._ob_liquidity.pop(data.instrument_id, None),
                override=True,
            )

    def buy(self, instrument: Instrument, balance_free: Money, label: str):
        bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
        entry_price = instrument.make_price(self._ema[instrument.id].value)
        sl_price = entry_price
        for i in range(15):
            bar = self.cache.bar(bar_type, i)
            sl_price = min(sl_price, bar.low)
        sl_price = instrument.make_price(entry_price - (entry_price - sl_price) * Decimal("1.2"))
        diff = entry_price - sl_price
        min_tp_price = entry_price * (1 + (instrument.taker_fee * Decimal("2.5")))
        tp_price = instrument.make_price(max(min_tp_price, entry_price + diff * 2))
        quantity = instrument.make_qty((balance_free * 0.1) / entry_price)
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
        self.submit_order_list(order_list)
        text = f"● Entry Price: <code>{entry_price}</code> @ Size: <code>{quantity}</code>\n"
        text += f"● Stop-Loss Price: <code>{sl_price}</code>\n"
        text += f"● Take-Profit Price: <code>{tp_price}</code>\n"
        self.log.info(
            f"Created bracket order for instrument: {instrument.id} with entry price: {entry_price}, stop-loss price: {sl_price}, take-profit price: {tp_price}, quantity: {quantity}",
            LogColor.GREEN,
        )
        self._notify_telegram(f"{label} - Created", instrument.id, text)
        self._ob_liquidity.pop(instrument.id, None)
        return order_list

    def sell(self, instrument: Instrument, balance_free: Money, label: str):
        bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
        entry_price = instrument.make_price(self._ema[instrument.id].value)
        sl_price = entry_price
        for i in range(15):
            bar = self.cache.bar(bar_type, i)
            sl_price = max(sl_price, bar.high)
        sl_price = instrument.make_price(entry_price + (sl_price - entry_price) * Decimal("1.2"))
        diff = sl_price - entry_price
        min_tp_price = entry_price * (1 - (instrument.taker_fee * Decimal("2.5")))
        tp_price = instrument.make_price(min(min_tp_price, entry_price - diff * 2))
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
        self.submit_order_list(order_list)
        text = f"● Entry Price: <code>{entry_price}</code> @ Size: <code>{quantity}</code>\n"
        text += f"● Stop-Loss Price: <code>{sl_price}</code>\n"
        text += f"● Take-Profit Price: <code>{tp_price}</code>\n"
        self._notify_telegram(f"{label} - Created", instrument.id, text)
        self.log.info(
            f"Created bracket order for instrument: {instrument.id} with entry price: {entry_price}, stop-loss price: {sl_price}, take-profit price: {tp_price}, quantity: {quantity}",
            LogColor.GREEN,
        )
        self._ob_liquidity.pop(instrument.id, None)
        return order_list

    def _on_change_of_character_data(self, data: ChangeOfCharacterConfirmationData) -> None:
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
        if instrument_id in self._state:
            prev_state = self._state[instrument_id]
            if prev_state.order_side != state.order_side:
                self.log.info(
                    f"Received ChangeOfCharacterData for instrument: {instrument_id} with different order side. Unsubscribing bars and removing previous state."
                )
                self._cancel_signal_for_instrument(instrument_id)
                self._state[instrument_id] = state
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
            self._state[instrument_id] = state

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
        text = f"<code>{instrument_id.venue}:{instrument_id.symbol}</code> - {emoji}\n<b>{label}</b>\n\n{text}"
        if not self._telegram:
            self.log.info(text, LogColor.YELLOW)
            return
        self._telegram.send(text)

    def _cancel_signal_time_event(self, event: TimeEvent) -> None:
        instrument_id = Instrument.id(event.name.split(":")[0])
        self._cancel_signal_for_instrument(instrument_id)

    def _cancel_signal_for_instrument(self, instrument_id: InstrumentId) -> None:
        if instrument_id not in self._state:
            return
        state = self._state[instrument_id]
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
            self._state.pop(instrument_id)

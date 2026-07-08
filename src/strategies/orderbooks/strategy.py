from decimal import Decimal

import pandas as pd
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.common.enums import LogColor
from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.indicators.averages import ExponentialMovingAverage
from nautilus_trader.indicators.averages import MovingAverageType
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.trading import Strategy
from nautilus_trader.trading.config import StrategyConfig

from src.notifications import SyncTelegramBridge
from src.notifications import TelegramNotifier

from .detector import OrderBookLiquidityData


class OrderbookStrategyConfig(StrategyConfig):
    client_id: ClientId | None = None


class OrderbookStrategy(Strategy):
    def __init__(self, config=None):
        super().__init__(config)
        self._telegram = SyncTelegramBridge(TelegramNotifier.from_env())
        self._telegram_notified: set[str] = set()

        self._rsi: dict[InstrumentId, RelativeStrengthIndex] = {}
        self._ema: dict[InstrumentId, ExponentialMovingAverage] = {}
        self._ob_liquidity: dict[InstrumentId, OrderBookLiquidityData] = {}

    def on_start(self):
        self.subscribe_data(DataType(OrderBookLiquidityData), client_id=self.config.client_id)

    def on_stop(self):
        # Close the telegram bridge first; idempotent.
        if self._telegram is not None:
            self._telegram.close()
            self._telegram = None

        self.unsubscribe_data(DataType(OrderBookLiquidityData), client_id=self.config.client_id)

        for instrument_id in self._rsi:
            bar_type = BarType.from_str(f"{instrument_id.value}-1-MINUTE-LAST-EXTERNAL")
            self.unsubscribe_bars(bar_type=bar_type, client_id=self.config.client_id)

    def setup_indicators(self, instrument_id: InstrumentId) -> None:
        self._rsi[instrument_id] = rsi = RelativeStrengthIndex(
            period=14, ma_type=MovingAverageType.WILDER
        )
        self._ema[instrument_id] = ema = ExponentialMovingAverage(period=15)
        bar_type = BarType.from_str(f"{instrument_id.value}-1-MINUTE-LAST-EXTERNAL")
        self.register_indicator_for_bars(bar_type, rsi)
        self.register_indicator_for_bars(bar_type, ema)
        self.request_bars(
            bar_type=bar_type,
            start=self.clock.utc_now() - pd.Timedelta(minutes=1440),
            client_id=self.config.client_id,
        )
        self.subscribe_bars(
            bar_type=bar_type,
            client_id=self.config.client_id,
        )

    def on_bar(self, bar: Bar):
        instrument_id = bar.bar_type.instrument_id
        if instrument_id not in self._ob_liquidity:
            return
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
            and self.cache.bar(bar.bar_type, 1).close < ema.value
            and bar.close > ema.value
            and rsi.value < 0.5
        ):
            self.buy(instrument, balance_free, label=data.label)
        elif (
            data.order_side == OrderSide.SELL
            and s3 == OrderSide.SELL
            and s1 != OrderSide.BUY
            and self.cache.bar(bar.bar_type, 1).close > ema.value
            and bar.close < ema.value
            and rsi.value > 0.5
        ):
            self.sell(instrument, balance_free, label=data.label)

    def on_data(self, data):
        if isinstance(data, OrderBookLiquidityData):
            # check if has indicators initialized
            if data.instrument_id not in self._rsi or data.instrument_id not in self._ema:
                self.setup_indicators(data.instrument_id)

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

    def _notify_telegram(self, label: str, instrument_id: InstrumentId, text: str) -> None:
        key = f"{instrument_id}:{label}"
        if self._telegram is None or key in self._telegram_notified:
            return
        self._telegram_notified.add(key)
        emoji = ""
        if "BUY" in label:
            emoji = "🟢"
        elif "SELL" in label:
            emoji = "🔴"
        text = f"{emoji} <b>{label}</b>\n<code>{instrument_id}</code>\n{text}"
        self._telegram.send(text)

        def clear_signal(e: TimeEvent):
            self._telegram_notified.discard(e.name)

        self.clock.set_time_alert(
            name=key,
            alert_time=self.clock.utc_now() + pd.Timedelta(minutes=5),
            callback=clear_signal,
        )

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

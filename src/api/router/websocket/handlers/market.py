from datetime import timedelta
from typing import TYPE_CHECKING

from aiohttp import web
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.model.identifiers import InstrumentId

from src.api.models.market import Ticker24hrDTO
from src.api.router.base import BaseRouter


if TYPE_CHECKING:
    from src.api.router.websocket.handler import WebsocketHandler


class MinitickerWebsocketHandler(BaseRouter):
    def __init__(self, app: web.Application, handler: WebsocketHandler):
        super().__init__(app)
        self.handler = handler
        self._subs: dict[str, InstrumentId] = {}

    async def subscribe(self, topic: str, client: web.WebSocketResponse):
        if topic in self._subs or not self.is_live:
            return
        instrument_id, _ = tuple(topic.split("@"))
        instrument_id = InstrumentId.from_str(instrument_id.upper())
        self.clock.set_timer(
            topic, interval=timedelta(seconds=1), callback=self.on_miniticker_timer
        )
        self._subs[topic] = instrument_id
        await self.handler.subscribe(
            f"{str(instrument_id).lower()}@tradeTick", client, source=self.__class__.__name__
        )

    async def unsubscribe(self, topic: str, client: web.WebSocketResponse):
        if topic not in self._subs or not self.is_live:
            return

        await self.handler.unsubscribe(
            f"{str(self._subs[topic]).lower()}@tradeTick", client, source=self.__class__.__name__
        )
        self.clock.cancel_timer(topic)
        self._subs.pop(topic)

    def on_miniticker_timer(self, event: TimeEvent):
        topic = event.name
        if topic not in self._subs:
            self.clock.cancel_timer(topic)
        instrument_id = self._subs[topic]
        try:
            ticker24hrs = self._calculate_24hr_ticker(instrument_id)
        except web.HTTPNotFound:
            return
        if not ticker24hrs:
            return
        message = {
            "stream": f"{str(instrument_id).lower()}@miniTicker",
            "data": {
                "e": "miniTicker",
                "E": event.ts_event // 1_000_000,
                "s": str(instrument_id),
                "c": str(ticker24hrs.last_price),
                "o": str(ticker24hrs.last_price - ticker24hrs.price_change),
            },
        }
        self.handler.publish(topic, message)

    def _calculate_24hr_ticker(self, instrument_id: InstrumentId):
        """
        Calculate 24-hour ticker statistics for a given instrument.
        """
        end_time = self.clock.timestamp_ns()
        start_time = end_time - int(timedelta(hours=24).total_seconds()) * 10**9
        bar_spec = self.timeframe_to_bar_spec("1m")
        bars = self.bars(
            instrument_id=instrument_id, bar_spec=bar_spec, start=start_time, end=end_time
        )
        if not bars:
            return None
        open_price = bars[0].open
        close_price = bars[-1].close

        trade_tick = self.cache.trade_tick(instrument_id)
        if trade_tick:
            close_price = trade_tick.price

        high_price = max(b.high for b in bars)
        low_price = min(b.low for b in bars)
        volume = sum(b.volume for b in bars)
        price_change = close_price - open_price
        return Ticker24hrDTO(
            id=str(instrument_id),
            symbol=str(instrument_id),
            last_price=close_price.as_decimal(),
            price_change=price_change.as_decimal(),
            price_change_percent=price_change / open_price,
            high_price=high_price.as_decimal(),
            low_price=low_price.as_decimal(),
            volume=volume,
        )

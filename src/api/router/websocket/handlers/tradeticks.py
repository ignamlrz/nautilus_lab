from typing import TYPE_CHECKING

from aiohttp import web
from nautilus_trader.model.identifiers import InstrumentId

from src.api.router.base import BaseRouter


if TYPE_CHECKING:
    from src.api.router.websocket.handler import WebsocketHandler


class TradeTicksWebsocketHandler(BaseRouter):
    def __init__(self, app: web.Application, handler: WebsocketHandler):
        super().__init__(app)
        self.handler = handler
        self._subs: dict[str, InstrumentId] = {}

    def subscribe(self, topic: str, client: web.WebSocketResponse):
        if topic in self._subs:
            return
        instrument_id, _ = tuple(topic.split("@"))
        instrument_id = InstrumentId.from_str(instrument_id.upper())
        self._subs[topic] = instrument_id
        self.agent.subscribe_trade_ticks(instrument_id)

    def unsubscribe(self, topic: str, client: web.WebSocketResponse):
        if topic not in self._subs:
            return
        self.agent.unsubscribe_trade_ticks(self._subs[topic])
        self._subs.pop(topic)

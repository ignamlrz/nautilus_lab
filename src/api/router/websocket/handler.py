from aiohttp import web

from src.api.router.base import BaseRouter

# from src.api.router.websocket.handlers.bar import BarsWebsocketHandler
# from src.api.router.websocket.handlers.drawing import DrawingWebsocketHandler
# from src.api.router.websocket.handlers.miniticker import MinitickerWebsocketHandler
from src.api.router.websocket.handlers.market import MinitickerWebsocketHandler
from src.api.router.websocket.manager import WebsocketManager


class WebsocketHandler(BaseRouter):
    def __init__(self, app: web.Application, manager: WebsocketManager):
        super().__init__(app)
        self.manager = manager
        self.handlers = {
            "miniTicker": MinitickerWebsocketHandler(app, self),
            # "kline": BarsWebsocketHandler(app, self),
            # "tradeTick": TradeTicksWebsocketHandler(app, self),
            # "drawing": DrawingWebsocketHandler(app, self)
        }

    async def dispatch(self, ws: web.WebSocketResponse, data: dict):
        if "method" not in data:
            return
        method = data["method"]

        match method:
            case "SUBSCRIBE":
                params = self._extract_params(data)
                for topic in params:
                    self.subscribe(topic, ws, source=self.__class__.__name__)
            case "UNSUBSCRIBE":
                params = self._extract_params(data)
                for topic in params:
                    self.unsubscribe(topic, ws, source=self.__class__.__name__)

    def subscribe(self, topic: str, client: web.WebSocketResponse, source: str):
        type_sub = self._extract_type_sub(topic)
        if type_sub in self.handlers and self.manager.subscribe(topic, client, source):
            self.handlers[type_sub].subscribe(topic, client)

    def unsubscribe(self, topic: str, client: web.WebSocketResponse, source: str):
        type_sub = self._extract_type_sub(topic)
        if type_sub in self.handlers and self.manager.unsubscribe(topic, client, source):
            self.handlers[type_sub].unsubscribe(topic, client)

    def publish(self, topic: str, message: dict):
        self.manager.publish(topic, message)

    def _extract_params(self, data: dict) -> list[str]:
        if "params" not in data:
            return []
        params: list[str] | str = data["params"]
        if isinstance(params, list):
            return params
        else:
            return [params]

    def _extract_type_sub(self, topic: str) -> str:
        if "@" in topic:
            _, type_sub = tuple(topic.split("@"))
            if "_" in type_sub:
                return next(iter(type_sub.split("_")))
            else:
                return type_sub
        raise NotImplementedError

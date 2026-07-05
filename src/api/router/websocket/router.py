from aiohttp import web
from nautilus_trader.common.enums import ComponentState

from src.api.router.base import BaseRouter
from src.api.router.websocket.handler import WebsocketHandler
from src.api.router.websocket.manager import WebsocketManager


class WebsocketRouter(BaseRouter):
    def __init__(self, app: web.Application):
        super().__init__(app)
        self.manager = WebsocketManager(app)
        self.handler = WebsocketHandler(app, self.manager)
        self.manager.register_handler(self.handler)

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        if self.is_backtest or self.agent.state != ComponentState.RUNNING:
            ws = web.WebSocketResponse(heartbeat=30)
            await ws.prepare(request)
            await ws.close(code=1011)
            return ws

        ws = await self.manager.connect(request)
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self.handler.dispatch(ws, msg.json())
                if msg.type == web.WSMsgType.ERROR:
                    self.error(f"WebSocket error: {ws.exception()}")
                    break
        finally:
            await self.manager.disconnect(ws)
        return ws

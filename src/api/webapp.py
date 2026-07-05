from aiohttp import web

from .middleware import cors_middleware
from .router import v1


# from .routes import websocket


# ws_router = websocket.WebsocketRouter(v1.market_service)

app = web.Application(middlewares=[cors_middleware])
app.add_subapp("/api/v1", v1.app)
# app.add_routes(
#     [
#         web.get("/api/v1/ws", handler=ws_router.handle, name="websocket"),
#     ]
# )

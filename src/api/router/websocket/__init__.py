from aiohttp import web

from .router import WebsocketRouter


app = web.Application()

websocket = WebsocketRouter(app)

app.add_routes(
    [
        web.get("/ws", handler=websocket.handle, name="websocket"),
    ]
)

__all__ = ["app"]

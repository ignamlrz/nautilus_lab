from aiohttp import web

from .middleware import cors_middleware
from .router import v1
from .router import websocket


app = web.Application(middlewares=[cors_middleware])
app.add_subapp("/api/v1", v1.app)
app.add_subapp("/stream", websocket.app)

"""v1 REST routes mounted at ``/api/v1``.

Each handler is instantiated once at import time and registered
against the shared aiohttp ``app``.  Route names are prefixed with
``"v1-"`` so the React client (and tests) can reference them
unambiguously.
"""

from aiohttp import web

from .drawing import DrawingRouter
from .exchange import ExchangeRouter
from .market import MarketRouter


NAME_PREFIX = "v1-"

app = web.Application()

exchange = ExchangeRouter(app)
market = MarketRouter(app)
drawing = DrawingRouter(app)

app.add_routes(
    [
        web.get("/exchangeInfo", handler=exchange.exchange_info, name=f"{NAME_PREFIX}exchangeInfo"),
        web.get("/klines", handler=market.klines, name=f"{NAME_PREFIX}klines"),
        web.get("/ticker/24hr", handler=market.ticker24hour, name=f"{NAME_PREFIX}ticker24hr"),
        web.get("/drawings", handler=drawing.drawings, name=f"{NAME_PREFIX}drawings"),
    ]
)

__all__ = ["app"]

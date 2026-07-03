"""Nautilus actor that exposes the trading state to the HTTP/WebSocket API.

The actor attaches itself to the shared :mod:`aiohttp` app under the
``"actor"`` key so HTTP handlers can reach the live ``Actor.cache``
without going through globals.  It also owns the TradingView-style
timeframe ↔ :class:`BarSpecification` translation tables that the REST
routes consume.
"""

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.enums import LogColor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model import BarSpecification

from .server import Server


class ServerAgentActorConfig(ActorConfig):
    enabled: bool = True
    port: int = 8080
    host: str = ""


TRADINGVIEW_BAR_SPEC_MAP = {
    "1m": BarSpecification.from_str("1-MINUTE-LAST"),
    "5m": BarSpecification.from_str("5-MINUTE-LAST"),
    "15m": BarSpecification.from_str("15-MINUTE-LAST"),
    "1h": BarSpecification.from_str("1-HOUR-LAST"),
    "4h": BarSpecification.from_str("4-HOUR-LAST"),
    "1d": BarSpecification.from_str("1-DAY-LAST"),
    "1w": BarSpecification.from_str("1-WEEK-LAST"),
}

TRADINGVIEW_INTERVAL_MAP = {v: k for k, v in TRADINGVIEW_BAR_SPEC_MAP.items()}


class ServerAgentActor(Actor):
    config: ServerAgentActorConfig

    def __init__(self, config: ServerAgentActorConfig):
        super().__init__(config)
        self.server = Server(config)

    def on_start(self):
        self.log.info("Starting the server.")
        self.server.start()

    def on_stop(self):
        """Stop the API server and remove the actor from the app."""
        if not isinstance(self.clock, LiveClock):
            self.log.info("Press Enter to stop...", color=LogColor.YELLOW)
            input()
        self.log.info("Stopping the server.")
        self.server.stop()

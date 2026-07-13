from aiohttp import web
from nautilus_trader.model.identifiers import InstrumentId

from src.api.router.base import BaseRouter


class DrawingRouter(BaseRouter):
    """Router for exchange-related endpoints."""

    async def drawings(self, request: web.Request):
        """
        Returns a list of bars for a given symbol and interval.
        """
        instrument_id = InstrumentId.from_str(request.query["id"])

        drawings = sorted(
            self.agent.drawings.get(instrument_id, []), key=lambda d: d.points[0].time
        )

        # find bar type
        return web.json_response([d.model_dump() for d in drawings])

from aiohttp import web

from src.api.models.exchange import ExchangeInfoDTO
from src.api.models.exchange import ExchangeSymbolDTO
from src.api.router.base import BaseRouter


class ExchangeRouter(BaseRouter):
    """Router for exchange-related endpoints."""

    async def exchange_info(self, request: web.Request):
        """
        Returns exchange information, including a list of symbols and their details."""
        symbols = []
        for id in self.cache.instrument_ids():
            instrument = self.cache.instrument(id)
            exchange_symbol_dto = ExchangeSymbolDTO.from_bar(instrument)
            symbols.append(exchange_symbol_dto)
        exchange_info_dto = ExchangeInfoDTO(live=self.is_live, symbols=symbols)
        return web.json_response(exchange_info_dto.model_dump())

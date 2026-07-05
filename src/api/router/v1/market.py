import json
from datetime import timedelta

from aiohttp import web
from nautilus_trader.model.identifiers import InstrumentId

from src.api.models.market import BarDTO
from src.api.models.market import Ticker24hrDTO
from src.api.router.base import BaseRouter


class MarketRouter(BaseRouter):
    """Router for exchange-related endpoints."""

    async def klines(self, request: web.Request):
        """
        Returns a list of bars for a given symbol and interval.
        """
        bar_spec = self.timeframe_to_bar_spec(request.query.get("interval", "1m"))
        instrument_id = InstrumentId.from_str(request.query["symbol"])
        before = int(request.query.get("before", self.clock.timestamp_ms())) * 10**6
        limit = int(request.query.get("limit", 500))

        # find bar type
        bars = self.bars(instrument_id=instrument_id, bar_spec=bar_spec, end=before, limit=limit)
        return web.json_response([BarDTO.from_bar(b).model_dump() for b in bars])

    async def ticker24hour(self, request: web.Request):
        """
        Returns 24-hour ticker statistics for a given symbol.
        """
        unique_symbol = False
        if "symbols" in request.query:
            symbols = json.loads(request.query["symbols"])
        elif "symbol" in request.query:
            symbol = request.query["symbol"]
            if symbol:
                unique_symbol = True
                symbols = [symbol]
        tickers = []
        for symbol in symbols:
            instrument_id = InstrumentId.from_str(symbol.upper())
            ticker = self._calculate_24hr_ticker(instrument_id)
            if ticker:
                tickers.append(ticker)
        if unique_symbol and tickers:
            return web.json_response(tickers[0].model_dump())
        return web.json_response([t.model_dump() for t in tickers])

    def _calculate_24hr_ticker(self, instrument_id: InstrumentId):
        """
        Calculate 24-hour ticker statistics for a given instrument.
        """
        end_time = self.clock.timestamp_ns()
        start_time = end_time - int(timedelta(hours=24).total_seconds()) * 10**9
        bar_spec = self.timeframe_to_bar_spec("1m")
        bars = self.bars(
            instrument_id=instrument_id, bar_spec=bar_spec, start=start_time, end=end_time
        )
        if not bars:
            return None
        open_price = bars[0].open
        close_price = bars[-1].close

        trade_tick = self.cache.trade_tick(instrument_id)
        if trade_tick:
            close_price = trade_tick.price

        high_price = max(b.high for b in bars)
        low_price = min(b.low for b in bars)
        volume = sum(b.volume for b in bars)
        price_change = close_price - open_price
        return Ticker24hrDTO(
            id=str(instrument_id),
            symbol=str(instrument_id),
            last_price=close_price.as_decimal(),
            price_change=price_change.as_decimal(),
            price_change_percent=price_change / open_price,
            high_price=high_price.as_decimal(),
            low_price=low_price.as_decimal(),
            volume=volume,
        )

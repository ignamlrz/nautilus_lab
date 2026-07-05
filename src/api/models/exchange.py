from nautilus_trader.model.instruments import Instrument
from pydantic import BaseModel
from pydantic import model_serializer


class ExchangeSymbolDTO(BaseModel):
    """Data Transfer Object for Exchange information."""

    id: str
    symbol: str
    symbol_name: str
    base_asset: str
    quote_asset: str
    price_precision: str
    status: str
    venue: str

    @classmethod
    def from_bar(cls, instrument: Instrument):
        return cls(
            id=str(instrument.id),
            symbol=str(instrument.id),
            symbol_name=str(instrument.symbol),
            base_asset=str(getattr(instrument, "base_currency", instrument.symbol)),
            quote_asset=str(instrument.quote_currency),
            price_precision=str(instrument.price_precision),
            status=str(instrument.asset_class.name),
            venue=str(instrument.venue),
        )

    @model_serializer
    def serialize(self):
        return {
            "id": self.id,
            "symbol": self.symbol,
            "symbolName": self.symbol_name,
            "baseAsset": self.base_asset,
            "quoteAsset": self.quote_asset,
            "pricePrecision": self.price_precision,
            "status": self.status,
            "venue": self.venue,
        }


class ExchangeInfoDTO(BaseModel):
    """Data Transfer Object for Exchange information."""

    live: bool
    symbols: list[ExchangeSymbolDTO]

from decimal import Decimal

from nautilus_trader.model import Bar
from pydantic import BaseModel
from pydantic import model_serializer


class BarDTO(BaseModel):
    open_time: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    close_time: int

    @classmethod
    def from_bar(cls, bar: Bar):
        ts_event = bar.ts_event
        timedelta_ns = bar.bar_type.spec.timedelta.value
        adjustment_interval = ts_event % timedelta_ns
        open_time = (
            ts_event - adjustment_interval if adjustment_interval else ts_event - timedelta_ns
        )

        return cls(
            open_time=open_time,
            open=str(bar.open),
            high=str(bar.high),
            low=str(bar.low),
            close=str(bar.close),
            volume=str(bar.volume),
            close_time=ts_event,
        )

    @model_serializer
    def serialize(self):
        return [
            self.open_time // 1_000_000,
            # unix_nanos_to_dt(self.open_time).isoformat(),
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.close_time // 1_000_000,
            # unix_nanos_to_dt(self.close_time).isoformat(),
            "0",
            0,
            "0",
            "0",
            "0",
        ]


class Ticker24hrDTO(BaseModel):
    id: str
    symbol: str
    last_price: Decimal
    price_change: Decimal
    price_change_percent: Decimal
    high_price: Decimal
    low_price: Decimal
    volume: Decimal

    @model_serializer
    def serialize(self):
        return {
            "id": self.id,
            "symbol": self.symbol,
            "lastPrice": str(self.last_price),
            "priceChange": str(self.price_change),
            "priceChangePercent": f"{self.price_change_percent * 100:.2f}",
            "highPrice": str(self.high_price),
            "lowPrice": str(self.low_price),
            "volume": str(self.volume),
            "quoteVolume": str(self.volume * self.last_price),
        }

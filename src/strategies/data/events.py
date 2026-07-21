from nautilus_trader.core import UUID4
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.identifiers import InstrumentId

from src.api.models.drawings import Drawing
from src.api.models.drawings import DrawingPoint
from src.api.models.drawings import DrawingStyle
from src.api.models.drawings import DrawingTool


@customdataclass
class LiveBarData(Data):
    instrument_id: InstrumentId
    bar_type: str


@customdataclass
class HistoricalBarData(Data):
    instrument_id: InstrumentId
    bar_type: str


@customdataclass
class HistoricalBarLoadedData(Data):
    instrument_id: InstrumentId
    bar_type: str


@customdataclass
class ClosedMarketData(Data):
    instrument_id: InstrumentId
    market: str
    high_price: float
    low_price: float
    open_datetime: int
    close_datetime: int
    recursive_markets_breaked_above: float
    recursive_markets_breaked_below: float
    color: str = "#3051E2"

    def to_drawing(self) -> Drawing:
        points = [
            {"time": self.open_datetime // 10**9, "price": self.low_price},
            {"time": self.close_datetime // 10**9, "price": self.high_price},
        ]
        text = self.market
        if self.recursive_markets_breaked_above > 0:
            text += f" | RMBA: {self.recursive_markets_breaked_above:.2f}"
        if self.recursive_markets_breaked_below > 0:
            text += f" | RMBB: {self.recursive_markets_breaked_below:.2f}"
        return Drawing(
            id=str(UUID4()),
            instrument_id=str(self.instrument_id),
            tool=DrawingTool.RECTANGLE,
            points=[DrawingPoint(**p) for p in points],
            style=DrawingStyle(
                color="#00000000",
                fill=f"{self.color}22",
                text=text,
            ),
        )


@customdataclass
class MarketBreakAboveData(Data):
    instrument_id: InstrumentId
    is_principal: bool
    market: str
    session_high_price: float
    session_low_price: float
    markets_rebased_on_session: str
    recursive_markets_breaked_on_session: float
    price_market_rebased: float
    ts_market_rebased: int


@customdataclass
class MarketBreakBelowData(Data):
    instrument_id: InstrumentId
    is_principal: bool
    market: str
    session_high_price: float
    session_low_price: float
    markets_rebased_on_session: str
    recursive_markets_breaked_on_session: float
    price_market_rebased: float
    ts_market_rebased: int


@customdataclass
class NewSessionHighData(Data):
    instrument_id: InstrumentId
    market: str
    price: float


@customdataclass
class NewSessionLowData(Data):
    instrument_id: InstrumentId
    market: str
    price: float


@customdataclass
class TelegramTextData(Data):
    instrument_id: InstrumentId
    type: str
    label: str
    text: str

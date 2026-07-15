from nautilus_trader.core import UUID4
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId

from src.api.models.drawings import Drawing
from src.api.models.drawings import DrawingPoint
from src.api.models.drawings import DrawingStyle
from src.api.models.drawings import DrawingTool


@customdataclass
class SwingData(Data):
    label: str
    instrument_id: InstrumentId
    bar_type: str
    order_side: str
    high_price: float
    low_price: float
    tested_price: float
    duration: int

    def to_drawing(self) -> Drawing:
        order_side = (
            OrderSide[self.order_side] if isinstance(self.order_side, str) else self.order_side
        )
        points = [
            {"time": self.ts_event // 10**9, "price": 0},
        ]
        return Drawing(
            id=str(UUID4()),
            instrument_id=str(self.instrument_id),
            tool=DrawingTool.VLINE,
            points=points,
            style=DrawingStyle(color="#2F9147" if order_side == OrderSide.BUY else "#A03030"),
        )


@customdataclass
class OpenMarketData(Data):
    label: str
    market: str
    min_diff: float
    operable: bool
    open_datetime: int
    close_datetime: int


@customdataclass
class __ClosedMarketData(Data):
    instrument_id: InstrumentId
    market: str
    high_price: float
    low_price: float
    operable: bool
    open_datetime: int
    close_datetime: int
    color: str = "#3051E2"

    def to_drawing(self) -> Drawing:
        points = [
            {"time": self.open_datetime // 10**9, "price": self.low_price},
            {"time": self.close_datetime // 10**9, "price": self.high_price},
        ]
        return Drawing(
            id=str(UUID4()),
            instrument_id=str(self.instrument_id),
            tool=DrawingTool.RECTANGLE,
            points=[DrawingPoint(**p) for p in points],
            style=DrawingStyle(color="#00000000", fill=f"{self.color}22"),
        )


@customdataclass
class BosLine(Data):
    instrument_id: InstrumentId
    open_datetime: int
    close_datetime: int
    price: float
    color: str = "#3051E2"

    def to_drawing(self) -> Drawing:
        points = [
            {"time": self.open_datetime // 10**9, "price": self.price},
            {"time": self.close_datetime // 10**9, "price": self.price},
        ]
        return Drawing(
            id=str(UUID4()),
            instrument_id=str(self.instrument_id),
            tool=DrawingTool.LINE,
            points=[DrawingPoint(**p) for p in points],
            style=DrawingStyle(color=self.color, width=0.5, dashed=True),
        )


@customdataclass
class ChocLine(Data):
    instrument_id: InstrumentId
    open_datetime: int
    close_datetime: int
    price: float
    color: str = "#3051E2"

    def to_drawing(self) -> Drawing:
        points = [
            {"time": self.open_datetime // 10**9, "price": self.price},
            {"time": self.close_datetime // 10**9, "price": self.price},
        ]
        return Drawing(
            id=str(UUID4()),
            instrument_id=str(self.instrument_id),
            tool=DrawingTool.LINE,
            points=[DrawingPoint(**p) for p in points],
            style=DrawingStyle(color=self.color, width=0.5, dashed=True),
        )


@customdataclass
class BosPerfectPattern(Data):
    instrument_id: InstrumentId
    datetime: int
    color: str = "#3051E2"

    def to_drawing(self) -> Drawing:
        points = [
            {"time": self.datetime // 10**9, "price": 0},
        ]
        return Drawing(
            id=str(UUID4()),
            instrument_id=str(self.instrument_id),
            tool=DrawingTool.VLINE,
            points=[DrawingPoint(**p) for p in points],
            style=DrawingStyle(color=self.color, width=0.5, dashed=True),
        )


@customdataclass
class BosWithoutMssPattern(Data):
    instrument_id: InstrumentId
    datetime: int
    color: str = "#3051E2"

    def to_drawing(self) -> Drawing:
        points = [
            {"time": self.datetime // 10**9, "price": 0},
        ]
        return Drawing(
            id=str(UUID4()),
            instrument_id=str(self.instrument_id),
            tool=DrawingTool.VLINE,
            points=[DrawingPoint(**p) for p in points],
            style=DrawingStyle(color=self.color, width=0.5, dashed=True),
        )


@customdataclass
class ChangeOfCharacterConfirmationData(Data):
    label: str
    instrument_id: InstrumentId
    bar_type: str
    order_side: str
    globex: float
    # bos info
    bos_price: float
    bos_duration: int
    # choch info
    choc_price: float
    choc_duration: int
    # mss info
    mss_price: float
    mss_duration: int
    is_historical: bool

    def __repr__(self):
        if isinstance(self.order_side, str):
            order_side = OrderSide[self.order_side]
        else:
            order_side = self.order_side
        return (
            f"ChangeOfCharacterConfirmationData("
            f"Bar Type={self.bar_type}, "
            f"Side={order_side.name}, "
            f"Global {'Min' if order_side == OrderSide.BUY else 'Max'}={self.globex}, "
            f"BoS={self.bos_price}, "
            f"CHoC={self.choc_price}, "
            f"MSS={self.mss_price})"
        )

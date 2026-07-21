from nautilus_trader.core import UUID4
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.identifiers import InstrumentId

from src.api.models.drawings import Drawing
from src.api.models.drawings import DrawingPoint
from src.api.models.drawings import DrawingStyle
from src.api.models.drawings import DrawingTool


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

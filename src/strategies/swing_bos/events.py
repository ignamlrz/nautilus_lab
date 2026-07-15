from nautilus_trader.core import UUID4
from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId

from src.api.models.drawings import Drawing
from src.api.models.drawings import DrawingStyle
from src.api.models.drawings import DrawingTool


@customdataclass
class SwingsData(Data):
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

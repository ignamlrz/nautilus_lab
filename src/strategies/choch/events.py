from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId


@customdataclass
class SwingData(Data):
    label: str
    instrument_id: InstrumentId
    bar_type: str
    order_side: str
    high_price: float
    low_price: float
    duration: int


@customdataclass
class OpenMarketData(Data):
    label: str
    market: str
    min_diff: float
    operable: bool


@customdataclass
class ChangeOfCharacterConfirmationData(Data):
    label: str
    instrument_id: InstrumentId
    bar_type: str
    order_side: str
    global_peak_price: float
    # bos info
    bos_price: int
    bos_duration: int
    # choch info
    choc_price: int
    choc_duration: int
    # mss info
    mss_price: int
    mss_duration: int

    def __repr__(self):
        if isinstance(self.order_side, str):
            order_side = OrderSide[self.order_side]
        else:
            order_side = self.order_side
        return (
            f"ChangeOfCharacterConfirmationData("
            f"Bar Type={self.bar_type}, "
            f"Side={order_side.name}, "
            f"Global {'Min' if order_side == OrderSide.BUY else 'Max'}={self.global_peak_price}, "
            f"BoS={self.bos_price}, "
            f"CHoC={self.choc_price}, "
            f"MSS={self.mss_price})"
        )

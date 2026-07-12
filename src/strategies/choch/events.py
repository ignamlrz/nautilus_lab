from nautilus_trader.core.data import Data
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId


@customdataclass
class ChangeOfCharacterConfirmationData(Data):
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
        return (
            f"ChangeOfCharacterConfirmationData("
            f"Bar Type={self.bar_type}, "
            f"Side={self.order_side.name if isinstance(self.order_side, OrderSide) else self.order_side}, "
            f"Global {'Min' if str(self.order_side) == 'BUY' else 'Max'}={self.global_peak_price}, "
            f"BoS={self.bos_price}, "
            f"CHoC={self.choc_price}, "
            f"MSS={self.mss_price})"
        )

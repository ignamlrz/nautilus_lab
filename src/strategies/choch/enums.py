from enum import Enum
from enum import unique


@unique
class BosEvent(Enum):
    NONE = 1
    MSS_DETECTED = 2
    BOS_DETECTED = 3
    CHOCH_DETECTED = 4


@unique
class Market(Enum):
    NONE = "NONE"
    ASIA = "ASIA"
    LONDON = "LONDON"
    EEUU_PRE = "EEUU:PREMARKET"
    EEUU = "EEUU"
    EEUU_POST = "EEUU:POSTMARKET"

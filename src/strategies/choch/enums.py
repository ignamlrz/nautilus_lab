from enum import Enum
from enum import unique


@unique
class BosEvent(Enum):
    NONE = 1
    MSS = 2
    BOS = 3
    CHOCH = 4


@unique
class Market(Enum):
    NONE = "NONE"
    ASIA = "ASIA"
    LONDON = "LONDON"
    EEUU_PRE = "EEUU:PREMARKET"
    EEUU = "EEUU"
    EEUU_POST = "EEUU:POSTMARKET"

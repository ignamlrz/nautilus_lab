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
    SSE_SZSE = "SSE/SZSE"
    LSE = "LSE"
    PRE_NYSE = "PRE_NYSE"
    NYSE = "NYSE"
    POST_NYSE = "POST_NYSE"

from enum import StrEnum
from enum import unique

from pydantic import BaseModel


@unique
class DrawingTool(StrEnum):
    """Enum for Drawing types."""

    LINE = "TrendLine"
    VLINE = "VerticalLine"
    HLINE = "HorizontalLine"
    RECTANGLE = "Rectangle"


class DrawingPoint(BaseModel):
    """Data Transfer Object for Drawing Point information."""

    time: int
    price: float


class DrawingStyle(BaseModel):
    color: str = "#3051E2"
    width: int = 2
    dashed: bool = False
    fill: str | None = None


class Drawing(BaseModel):
    """Data Transfer Object for Drawing information."""

    id: str
    instrument_id: str
    tool: DrawingTool
    points: list[DrawingPoint]
    style: DrawingStyle = DrawingStyle()

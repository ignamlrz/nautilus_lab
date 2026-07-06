from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiohttp import web
from nautilus_trader.data.aggregation import BarBuilder
from nautilus_trader.model import Bar
from nautilus_trader.model import BarSpecification
from nautilus_trader.model import BarType
from nautilus_trader.model import Quantity
from nautilus_trader.model.enums import AggregationSource
from nautilus_trader.model.identifiers import InstrumentId

from src.api.router.base import BaseRouter


if TYPE_CHECKING:
    from src.api.router.websocket.handler import WebsocketHandler


@dataclass
class BarState:
    topic: str
    bar_builder: BarBuilder
    next_bar: int


class BarsWebsocketHandler(BaseRouter):
    bar_spec_sub = BarSpecification.from_str("250-MILLISECOND-LAST")

    def __init__(self, app: web.Application, handler: WebsocketHandler):
        super().__init__(app)
        self.handler = handler
        self._subs: dict[InstrumentId, dict[BarType, BarState]] = {}

    def subscribe(self, topic: str, client: web.WebSocketResponse):
        instrument_id, interval = tuple(topic.split("@"))
        instrument_id = InstrumentId.from_str(instrument_id.upper())
        _, interval = tuple(interval.split("_"))
        bar_type = self.find_bar_type(instrument_id, interval)
        if instrument_id not in self._subs:
            self._subs[instrument_id] = {}
        if bar_type in self._subs[instrument_id]:
            return

        self.handler.subscribe(
            f"{str(instrument_id).lower()}@drawing", client, source=self.__class__.__name__
        )

        current_ns = self.clock.timestamp_ns()
        diff = current_ns % bar_type.spec.timedelta.value
        start = current_ns - diff

        instrument = self.cache.instrument(instrument_id)
        bar_builder = BarBuilder(instrument, bar_type)
        bars = self.bars(instrument_id, bar_type.spec, limit=1)
        if bars:
            bar_builder.update(bars[0].close, Quantity.zero(), self.clock.timestamp_ns())
        self._subs[instrument_id][bar_type] = BarState(
            bar_builder=bar_builder,
            topic=topic,
            next_bar=start + bar_type.spec.timedelta.value,
        )
        if len(self._subs[instrument_id]) == 1:
            bar_type_sub = BarType(
                instrument_id=instrument_id,
                bar_spec=self.bar_spec_sub,
                aggregation_source=AggregationSource.INTERNAL,
            )
            self.agent.subscribe_bars(bar_type_sub)

    def unsubscribe(self, topic: str, client: web.WebSocketResponse):
        instrument_id, interval = tuple(topic.split("@"))
        instrument_id = InstrumentId.from_str(instrument_id.upper())
        _, interval = tuple(interval.split("_"))
        bar_type = self.find_bar_type(instrument_id, interval)
        if instrument_id not in self._subs or bar_type not in self._subs[instrument_id]:
            return

        self.handler.unsubscribe(
            f"{str(instrument_id).lower()}@drawing", client, source=self.__class__.__name__
        )

        self._subs[instrument_id].pop(bar_type)
        if len(self._subs[instrument_id]) == 0:
            bar_type_sub = self.find_bar_type(instrument_id, self.bar_spec_sub)
            self.agent.unsubscribe_bars(bar_type_sub)
            self._subs.pop(instrument_id)

    def on_bar(self, bar: Bar):
        id = bar.bar_type.instrument_id
        if id not in self._subs:
            return
        for bt in self._subs[id].copy():
            state = self._subs[id][bt]
            bar_builder = state.bar_builder
            is_closed = False
            b = None
            current_diff = bar.ts_event % self.bar_spec_sub.timedelta.value
            current_open_time = bar.ts_event - current_diff
            if current_open_time < state.next_bar:
                if not bar_builder.initialized:
                    bar_builder.update_bar(bar, bar.volume, bar.ts_init)
                    b = bar_builder.build_now()
                else:
                    b = bar_builder.build_now()
                    bar_builder.update_bar(b, b.volume, b.ts_init)
                bar_builder.update_bar(bar, bar.volume, bar.ts_init)
                open_time = state.next_bar - bt.spec.timedelta.value
            else:
                b = bar_builder.build_now()
                open_time = state.next_bar - bt.spec.timedelta.value
                state.next_bar += bt.spec.timedelta.value
                bar_builder.reset()
                bar_builder.update_bar(bar, bar.volume, bar.ts_init)
                is_closed = True
            if not b:
                continue
            close_time = b.ts_event
            msg = {
                "stream": state.topic,
                "data": {
                    "e": "kline",
                    "E": close_time // 1_000_000,
                    "s": str(id),
                    "k": {
                        "t": open_time // 1_000_000,
                        "T": close_time // 1_000_000,
                        "s": str(id),
                        "i": self.bar_spec_to_timeframe(bt.spec),
                        "o": str(b.open),
                        "c": str(b.close),
                        "h": str(b.high),
                        "l": str(b.low),
                        "v": str(b.volume),
                        "x": is_closed,
                    },
                },
            }
            self.handler.publish(state.topic, msg)

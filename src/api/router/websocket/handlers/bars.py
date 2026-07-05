from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING

from aiohttp import web
from nautilus_trader.core import UUID4
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.data.aggregation import BarBuilder
from nautilus_trader.model import Bar
from nautilus_trader.model import BarSpecification
from nautilus_trader.model import BarType
from nautilus_trader.model.enums import AggregationSource
from nautilus_trader.model.identifiers import InstrumentId

from src.api.router.base import BaseRouter


if TYPE_CHECKING:
    from src.api.router.websocket.handler import WebsocketHandler


@dataclass
class BarState:
    topic: str
    bar_builder: BarBuilder
    lookup_bar_type: BarType
    next_bar: int
    start: int | None = None
    pending_bars: list[Bar] = field(default_factory=list)
    uuid: UUID4 | None = None


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

        # lookup bar type and request bars if needed
        lookup_bar_type = self.lookup_bar_type(bar_type, real_time=True)
        if lookup_bar_type is None:
            self.log.warning(f"Bar type {bar_type} not found for instrument {instrument_id}")
            return

        current_ns = self.clock.timestamp_ns()
        diff = current_ns % bar_type.spec.timedelta.value
        start = current_ns - diff
        uuid = None
        last_bar = self.cache.bar(lookup_bar_type)
        if (
            not last_bar
            or last_bar.ts_event + bar_type.spec.timedelta.value < self.clock.timestamp_ns()
        ):
            request_start = start - (bar_type.spec.timedelta.value * 1000)  # request 1000 bars back
            if lookup_bar_type.is_internally_aggregated():
                uuid = self.agent.request_aggregated_bars(
                    [lookup_bar_type], start=unix_nanos_to_dt(request_start)
                )
            else:
                uuid = self.agent.request_bars(
                    lookup_bar_type, start=unix_nanos_to_dt(request_start)
                )

        instrument = self.cache.instrument(instrument_id)
        self._subs[instrument_id][bar_type] = BarState(
            bar_builder=BarBuilder(instrument, bar_type),
            topic=topic,
            uuid=uuid,
            lookup_bar_type=lookup_bar_type,
            start=start,
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
            topic = state.topic
            if not bar_builder.initialized:
                if not self.agent.is_pending_request(state.uuid):
                    bars = [
                        b
                        for b in self.cache.bars(state.lookup_bar_type)
                        if b.ts_event >= state.start
                    ]
                    bars.sort(key=lambda b: b.ts_event)
                    self.log.info(f"Initializing bar builder for {topic} with {len(bars)} bars")
                    for b in bars:
                        bar_builder.update_bar(b, b.volume, b.ts_init)
                    for p in state.pending_bars:
                        bar_builder.update_bar(p, p.volume, p.ts_init)
                    state.pending_bars.clear()
                else:
                    state.pending_bars.append(bar)
                    continue
            is_closed = False
            b = None
            current_diff = bar.ts_event % self.bar_spec_sub.timedelta.value
            current_open_time = bar.ts_event - current_diff
            if current_open_time < state.next_bar:
                if bar_builder.initialized:
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
                "stream": topic,
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
            self.handler.publish(topic, msg)

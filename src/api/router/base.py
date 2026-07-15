from datetime import timedelta

from aiohttp import web
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import TestClock
from nautilus_trader.data.aggregation import TimeBarAggregator
from nautilus_trader.model import BarSpecification
from nautilus_trader.model import BarType
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.enums import aggregation_source_to_str
from nautilus_trader.model.enums import bar_aggregation_to_str
from nautilus_trader.model.identifiers import InstrumentId

from src.api.keys import LOOP_KEY
from src.api.keys import NAUTILUS_TRADER_KEY


ALLOWED_TIMEFRAME_TO_BAR_SPEC = {
    "1m": BarSpecification.from_str("1-MINUTE-LAST"),
    "5m": BarSpecification.from_str("5-MINUTE-LAST"),
    "15m": BarSpecification.from_str("15-MINUTE-LAST"),
    "30m": BarSpecification.from_str("30-MINUTE-LAST"),
    "1h": BarSpecification.from_str("1-HOUR-LAST"),
    "4h": BarSpecification.from_str("4-HOUR-LAST"),
    "1d": BarSpecification.from_str("1-DAY-LAST"),
    "1w": BarSpecification.from_str("1-WEEK-LAST"),
}

ALLOWED_BAR_SPEC_TO_TIMEFRAME = {v: k for k, v in ALLOWED_TIMEFRAME_TO_BAR_SPEC.items()}

LOOKUP_BARS = {
    timedelta(minutes=5): BarSpecification.from_timedelta(timedelta(seconds=1), PriceType.LAST),
    timedelta(hours=12): BarSpecification.from_timedelta(timedelta(minutes=1), PriceType.LAST),
    timedelta(days=7): BarSpecification.from_timedelta(timedelta(minutes=15), PriceType.LAST),
    timedelta(days=31 * 3): BarSpecification.from_timedelta(timedelta(hours=4), PriceType.LAST),
    timedelta(days=365 * 100): BarSpecification.from_timedelta(timedelta(days=1), PriceType.LAST),
}


class BaseRouter:
    def __init__(self, app: web.Application):
        self.app = app

    @property
    def nautilus_app(self):
        return self.app[NAUTILUS_TRADER_KEY]

    @property
    def loop(self):
        return self.app[LOOP_KEY]

    @property
    def kernel(self):
        return self.nautilus_app.kernel

    @property
    def agent(self):
        from src.api.server.agent import ServerAgentActor

        actor = self.nautilus_app.kernel.trader.actors()[-1]
        if isinstance(actor, ServerAgentActor):
            return actor
        return None

    @property
    def cache(self):
        return self.kernel.cache

    @property
    def clock(self):
        return self.kernel.clock

    @property
    def is_live(self):
        return isinstance(self.clock, LiveClock)

    @property
    def is_backtest(self):
        return not self.is_live

    @property
    def log(self):
        return self.kernel.logger

    @staticmethod
    def timeframe_to_bar_spec(interval: str):
        """Convert a TradingView interval string to a Nautilus :class:`BarSpecification`."""
        bar_spec = ALLOWED_TIMEFRAME_TO_BAR_SPEC.get(interval)
        if not bar_spec:
            raise web.HTTPNotFound(text=f"interval '{interval}' was not found on allowed bar_types")
        return bar_spec

    @staticmethod
    def bar_spec_to_timeframe(bar_spec: BarSpecification):
        """Convert a Nautilus :class:`BarSpecification` to a TradingView interval string."""
        timeframe = ALLOWED_BAR_SPEC_TO_TIMEFRAME.get(bar_spec)
        if not timeframe:
            raise web.HTTPNotFound(text=f"bar_spec '{bar_spec}' was not found on allowed bar_types")
        return timeframe

    def find_bar_type(
        self, instrument_id: InstrumentId, bar_spec: BarSpecification, real_time: bool = False
    ) -> BarType:
        """Find a Nautilus :class:`BarType` for a given instrument and bar specification."""
        if isinstance(bar_spec, str):
            bar_spec = self.timeframe_to_bar_spec(bar_spec)
        bar_types = self.cache.bar_types(instrument_id=instrument_id)
        for bar_type in bar_types:
            if bar_type.spec == bar_spec:
                return bar_type

        bar_type_aggregated = self.lookup_bar_type(
            BarType(instrument_id=instrument_id, bar_spec=bar_spec), real_time=real_time
        )
        if bar_type_aggregated:
            spec_aggregated = bar_type_aggregated.spec
            return BarType.from_str(
                f"{instrument_id}-{bar_spec}-INTERNAL@{spec_aggregated.step}-{bar_aggregation_to_str(spec_aggregated.aggregation)}-{aggregation_source_to_str(bar_type_aggregated.aggregation_source)}"
            )
        return BarType.from_str(f"{instrument_id}-{bar_spec}-INTERNAL")

    def lookup_bar_type(self, current: BarType, real_time: bool = False) -> BarType | None:
        for max_td, bar_spec in LOOKUP_BARS.items():
            if not real_time and bar_spec.timedelta < timedelta(minutes=1):
                continue
            if current.spec.timedelta <= max_td:
                if bar_spec.timedelta < timedelta(minutes=1):
                    bar_type = BarType.from_str(f"{current.instrument_id}-{bar_spec}-INTERNAL")
                else:
                    bar_type = BarType.from_str(f"{current.instrument_id}-{bar_spec}-EXTERNAL")
                return bar_type
        return None

    def bars(
        self,
        instrument_id: InstrumentId,
        bar_spec: BarSpecification,
        start: int | None = None,
        end: int | None = None,
        limit: int | None = None,
    ):
        """Return a list of bars for a given bar type, start and end timestamps, and limit."""
        start = start or 0
        end = end or self.clock.timestamp_ns()
        limit = limit or 500
        # find bar type
        bar_type = self.find_bar_type(instrument_id=instrument_id, bar_spec=bar_spec)
        if not bar_type.is_composite():
            # filter cache bars by before date and then apply limit
            bars = [b for b in self.cache.bars(bar_type=bar_type) if start <= b.ts_event < end][
                :limit
            ]

            # order by open_time ascending
            bars.sort(key=lambda b: b.ts_event)
        elif bar_type.is_composite() and bar_type.composite().spec.timedelta == timedelta(
            minutes=1
        ):
            # try aggregate with 1m bars if the requested bar type is not found and the interval is greater than 1 minute
            bar_spec_1m = self.timeframe_to_bar_spec("1m")
            bar_type_1m = self.find_bar_type(instrument_id=instrument_id, bar_spec=bar_spec_1m)
            if not bar_type_1m:
                raise web.HTTPNotFound(
                    text=f"bar_type '{bar_spec}' was not found for instrument '{instrument_id}'"
                )
            else:
                instrument = self.cache.instrument(instrument_id)  # Ensure the instrument is cached
                aggregated_bars = []
                handler = aggregated_bars.append
                # TimeBarAggregator drives emission from a clock (advanced by each
                # 1m bar's ts_init), so it works for any timeframe — unlike
                # TickBarAggregator, which emits by count and only happens to
                # align when spec.step equals the 1m bars per interval.
                clock = TestClock()
                aggregator = TimeBarAggregator(
                    instrument=instrument,
                    bar_type=BarType.from_str(f"{instrument_id}-{bar_spec}-INTERNAL"),
                    handler=handler,
                    clock=clock,
                    timestamp_on_close=True,
                )
                aggregator.set_historical_mode(True, handler)

                # filter cache bars by before date and then apply limit
                bars_1m = [
                    b for b in self.cache.bars(bar_type=bar_type_1m) if start <= b.ts_event < end
                ]

                # order by open_time ascending
                bars_1m.sort(key=lambda b: b.ts_event)
                for b in bars_1m:
                    aggregator.handle_bar(b)

                bars = aggregated_bars[-limit:]
        else:
            raise web.HTTPNotFound(
                text=f"bar_type '{bar_spec}' was not found for instrument '{instrument_id}'"
            )
        return bars

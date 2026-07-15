import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.core import UUID4
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId

from src.strategies.data.events import HistoricalBarData
from src.strategies.data.events import HistoricalBarLoadedData
from src.strategies.data.events import LiveBarData


class BarSyncronizerActorConfig(ActorConfig, frozen=True):
    """
    Configuration for ``BarSyncronizerActor`` instances.
    """

    instrument_ids: list[InstrumentId]
    bar_type_specs: list[str]

    historical_data_timedelta: pd.Timedelta = pd.Timedelta(days=1)
    client_id: ClientId | None = None
    log_data: bool = False


class BarSyncronizerActor(Actor):
    config: BarSyncronizerActorConfig

    def __init__(self, config: BarSyncronizerActorConfig) -> None:
        super().__init__(config)
        self._pending_requests_bars: dict[UUID4, list[BarType]] = {}
        self._map_on_historical_data = {
            Bar: self.on_historical_bar,
        }

    def on_start(self) -> None:
        client_id = self.config.client_id
        requests_start = self.clock.utc_now() - self.config.historical_data_timedelta

        for bar_type_spec in self.config.bar_type_specs:
            if "INTERNAL" in bar_type_spec:
                aggregated_bar_types = set()
                for instrument_id in self.config.instrument_ids or []:
                    bar_type = BarType.from_str(f"{instrument_id.value}-{bar_type_spec}")
                    aggregated_bar_types.add(bar_type)

                uuid = UUID4()
                self._pending_requests_bars[uuid] = list(aggregated_bar_types)
                self.request_aggregated_bars(
                    bar_types=list(aggregated_bar_types),
                    start=requests_start,
                    client_id=client_id,
                    update_subscriptions=True,
                    callback=self.on_requests_bars_finished,
                    request_id=uuid,
                )
            else:
                for instrument_id in self.config.instrument_ids or []:
                    bar_type = BarType.from_str(f"{instrument_id.value}-{bar_type_spec}")

                    uuid = UUID4()
                    self._pending_requests_bars[uuid] = [bar_type]
                    self.request_bars(
                        bar_type=bar_type,
                        start=requests_start,
                        client_id=client_id,
                        callback=self.on_requests_bars_finished,
                        request_id=uuid,
                    )

    def on_requests_bars_finished(self, uuid: UUID4) -> None:
        client_id = self.config.client_id

        for bar_type in self._pending_requests_bars.get(uuid, []):
            self.subscribe_bars(bar_type=bar_type, client_id=client_id)
            data = HistoricalBarLoadedData(instrument_id=bar_type.instrument_id, bar_type=bar_type)
            self.publish_data(DataType(HistoricalBarLoadedData), data)

    def on_stop(self) -> None:
        client_id = self.config.client_id

        for bar_type_spec in self.config.bar_type_specs:
            for instrument_id in self.config.instrument_ids or []:
                bar_type = BarType.from_str(f"{instrument_id.value}-{bar_type_spec}")
                self.unsubscribe_bars(bar_type=bar_type, client_id=client_id)

    def on_historical_data(self, data):
        self._map_on_historical_data.get(type(data), lambda x: None)(data)

    def on_historical_bar(self, bar: Bar) -> None:
        data = HistoricalBarData(instrument_id=bar.bar_type.instrument_id, bar_type=bar.bar_type)
        self.publish_data(DataType(HistoricalBarData), data)

    def on_bar(self, bar: Bar) -> None:
        data = LiveBarData(instrument_id=bar.bar_type.instrument_id, bar_type=bar.bar_type)
        self.publish_data(DataType(LiveBarData), data)

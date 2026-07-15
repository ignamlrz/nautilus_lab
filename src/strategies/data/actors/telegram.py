import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.common.enums import LogColor
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId

from src.notifications.bot import TelegramNotifier
from src.notifications.bridge import SyncTelegramBridge
from src.notifications.config import TelegramConfig
from src.strategies.data.events import TelegramTextData


class TelegramActorConfig(ActorConfig, frozen=True):
    """
    Configuration for ``TelegramActor`` instances.
    """

    use_env: bool = True
    bot_token: str | None = None
    chat_ids: str | None = None
    client_id: ClientId | None = None
    log_data: bool = True


class TelegramActor(Actor):
    config: TelegramActorConfig

    def __init__(self, config: TelegramActorConfig) -> None:
        super().__init__(config)
        self._map_on_data = {
            TelegramTextData: self.on_telegram_text_data,
        }
        self._telegram: SyncTelegramBridge | None = None
        self._date_started: pd.Timestamp | None = None

    def on_start(self) -> None:
        client_id = self.config.client_id

        self._date_started = self.clock.utc_now()

        # Initialize the telegram bridge if running in live mode
        if isinstance(self.clock, LiveClock):
            if self.config.use_env:
                telegram_notifier = TelegramNotifier.from_env()
            else:
                telegram_notifier = TelegramNotifier(
                    TelegramConfig(
                        bot_token=self.config.telegram_bot_token,
                        chat_ids=self.config.telegram_chat_ids,
                        disable_notification=self.config.telegram_disable_notification,
                        timeout=self.config.telegram_timeout,
                    )
                )
            self._telegram = SyncTelegramBridge(telegram_notifier)

        self.subscribe_data(DataType(TelegramTextData), client_id=client_id)

    def on_stop(self) -> None:
        client_id = self.config.client_id

        self.unsubscribe_data(DataType(TelegramTextData), client_id=client_id)

    def on_data(self, data: TelegramTextData):
        self._map_on_data.get(type(data), lambda x: None)(data)

    def on_telegram_text_data(self, data: TelegramTextData) -> None:
        emoji = ""
        match data.type:
            case s if "BUY" in s:
                emoji = "🟢"
            case s if "SELL" in s:
                emoji = "🔴"
            case s if "INFO" in s:
                emoji = "ℹ️"
            case s if "WARNING" in s:
                emoji = "⚠️"
        if not self._telegram:
            if self.config.log_data:
                self.log.info(
                    f"{data.instrument_id} -> Label: {data.label} | Text: {data.text}",
                    LogColor.YELLOW,
                )
        elif unix_nanos_to_dt(data.ts_event) > self._date_started:
            text_telegram = f"<code>{data.instrument_id.venue}:{data.instrument_id.symbol}</code>\n{emoji} <b>{data.label}</b>\n\n{data.text}"
            self._telegram.send(text_telegram)

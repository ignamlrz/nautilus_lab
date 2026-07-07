"""Configuration for the Telegram notifier."""

import msgspec


class TelegramConfig(msgspec.Struct, frozen=True):
    """Static settings for a :class:`TelegramNotifier` instance.

    ``chat_ids`` is a list so the same bot can broadcast to several
    recipients (your phone, a group, a second device).
    """

    bot_token: str
    chat_ids: list[int]
    parse_mode: str = "HTML"
    disable_notification: bool = False
    timeout: float = 10.0

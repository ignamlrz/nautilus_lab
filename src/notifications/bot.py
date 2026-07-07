"""Outbound-only Telegram notifier.

Wraps ``python-telegram-bot``'s :class:`telegram.Bot` so the rest of the
trading bot can fire-and-forget alerts without dealing with the
``Application``/polling machinery. Use it from anywhere:

    from src.notifications import TelegramNotifier

    notifier = TelegramNotifier.from_env()
    await notifier.send("BTC-USDT broke resistance at 100k")

A sync :meth:`send_sync` is provided for one-off scripts. Failures on one
chat_id are logged and skipped, so a bad recipient won't break the others.
"""

import asyncio
import logging
import os

from telegram import Bot
from telegram.error import TelegramError

from src.notifications.config import TelegramConfig


logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Send text messages to one or more Telegram chats."""

    def __init__(self, config: TelegramConfig) -> None:
        self._config = config
        self._bot = Bot(token=config.bot_token)

    @classmethod
    def from_env(cls) -> TelegramNotifier:
        """Build a notifier from ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_IDS``.

        ``TELEGRAM_CHAT_IDS`` is a comma-separated list, e.g.
        ``"123456789,987654321"``.
        """
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_ids_raw = os.environ.get("TELEGRAM_CHAT_IDS", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN env var is not set")
        if not chat_ids_raw:
            raise RuntimeError("TELEGRAM_CHAT_IDS env var is not set")
        chat_ids = [int(x) for x in chat_ids_raw.split(",") if x.strip()]
        if not chat_ids:
            raise RuntimeError("TELEGRAM_CHAT_IDS did not contain any integer chat_id")
        return cls(
            TelegramConfig(
                bot_token=token,
                chat_ids=chat_ids,
                parse_mode=os.environ.get("TELEGRAM_PARSE_MODE", "HTML"),
                disable_notification=os.environ.get("TELEGRAM_SILENT", "").lower()
                in {"1", "true", "yes"},
            ),
        )

    async def send(self, text: str, *, parse_mode: str | None = None) -> None:
        """Send ``text`` to every chat_id. Errors on individual recipients
        are logged but do not propagate."""
        mode = parse_mode if parse_mode is not None else self._config.parse_mode
        for chat_id in self._config.chat_ids:
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=mode,
                    disable_notification=self._config.disable_notification,
                    read_timeout=self._config.timeout,
                    write_timeout=self._config.timeout,
                )
            except TelegramError:
                logger.exception("Telegram send failed for chat_id=%s", chat_id)

    def send_sync(self, text: str) -> None:
        """Sync wrapper for one-off scripts and tests."""
        asyncio.run(self.send(text))

    async def close(self) -> None:
        """Release the underlying HTTP session. Call on shutdown."""
        await self._bot.shutdown()

    # --- convenience formatters -----------------------------------------

    @staticmethod
    def format_trade(
        *,
        side: str,
        symbol: str,
        price: float,
        size: float,
        extra: str = "",
    ) -> str:
        arrow = "🟢" if side.upper() == "BUY" else "🔴"
        msg = (
            f"{arrow} <b>{side.upper()} {symbol}</b>\n"
            f"price: <code>{price}</code>\n"
            f"size : <code>{size}</code>"
        )
        if extra:
            msg += f"\n{extra}"
        return msg

    @staticmethod
    def format_signal(*, title: str, symbol: str, body: str) -> str:
        return f"🚨 <b>{title}</b> — {symbol}\n{body}"

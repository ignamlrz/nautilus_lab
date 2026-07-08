"""Print the chat_id of whoever sends /start to your bot.

Run once to discover your numeric chat_id, then save it as the
``TELEGRAM_CHAT_IDS`` env var.

Usage:
    export TELEGRAM_BOT_TOKEN=...
    python scripts/get_telegram_chat_id.py
"""

import asyncio
import logging
import os
import signal
from types import FrameType

from telegram import Update
from telegram.ext import Application
from telegram.ext import CommandHandler
from telegram.ext import ContextTypes


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    logger.info(
        "chat_id=%s from user=%s (@%s, id=%s)",
        chat.id,
        user.full_name if user else "?",
        user.username if user else "?",
        user.id if user else "?",
    )
    if update.message:
        await update.message.reply_text(
            f"Tu chat_id es <code>{chat.id}</code>. "
            "Ya podés cerrar este script y guardar el número en TELEGRAM_CHAT_IDS.",
            parse_mode="HTML",
        )


async def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN first")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))

    stop = asyncio.Event()

    def _on_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    async with app:
        await app.start()
        await app.updater.start_polling()
        logger.info(
            "Waiting for /start... open Telegram, find your bot, send /start. Ctrl+C to quit.",
        )
        await stop.wait()
        await app.updater.stop_polling()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())

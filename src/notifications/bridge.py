"""Bridge a :class:`TelegramNotifier` onto a daemon thread with its own event loop.

Nautilus calls actor/strategy handlers synchronously on its engine thread. The
notifier is async. This module wires the two: callers from any thread can do
``bridge.send(text)`` and the work gets scheduled onto the bridge's loop via
:func:`asyncio.run_coroutine_threadsafe`.
"""

import asyncio
import threading
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from src.notifications.bot import TelegramNotifier


class SyncTelegramBridge:
    """Thread-safe sync facade over an async :class:`TelegramNotifier`."""

    def __init__(self, notifier: TelegramNotifier) -> None:
        self._notifier = notifier
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = False

        self._thread = threading.Thread(
            target=self._run,
            name="telegram-notifier",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Telegram notifier thread failed to start its event loop")

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(self._loop)
            self._ready.set()
            self._loop.run_forever()
        finally:
            self._loop.close()

    def send(self, text: str) -> None:
        """Schedule ``notifier.send(text)`` on the bridge's loop. Non-blocking."""
        if self._closed or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._notifier.send(text), self._loop)
        except RuntimeError:
            # Loop was closed between the check and the call — drop silently.
            pass

    def close(self) -> None:
        """Stop the loop and join the thread. Idempotent."""
        if self._closed:
            return
        self._closed = True
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._notifier.close(), loop).result(timeout=5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        if thread.is_alive():
            thread.join(timeout=5)

"""Threaded aiohttp server that hosts the API in the background.

Nautilus Trader's trading node and FastAPI's ASGI loop both want the
main thread, so the API runs in a daemon thread of its own.  The
:class:`Server` class encapsulates that thread, the asyncio loop it
owns, and the cross-thread synchronisation primitives that let
``start()``, ``wait()`` and ``stop()`` cooperate from the main
process.
"""

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from aiohttp import web

from src.api.keys import LOOP_KEY
from src.api.webapp import app


if TYPE_CHECKING:
    from .agent import ServerAgentActorConfig

logger = logging.getLogger(__name__)


class Server:
    """Async HTTP/WebSocket server running on a dedicated daemon thread.

    The lifecycle is:

    1. :meth:`start` spawns the thread and blocks until the TCP site
       is bound.
    2. :meth:`wait` blocks the caller (typically the main thread)
       until :meth:`stop` is invoked or Ctrl+C is pressed.
    3. :meth:`stop` signals the asyncio loop, joins the thread, and
       releases the socket.
    """

    def __init__(self, cfg: ServerAgentActorConfig):
        self._config = cfg
        # asyncio loop running inside the daemon thread, populated by
        # ``_run_loop`` after the thread starts.  ``None`` until then.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # Set by the server thread; consumed by ``wait()`` in the main
        # thread.  A second Event is needed because we can't share the
        # asyncio one across threads.
        self._ready = threading.Event()
        self._stop_wake = threading.Event()
        # asyncio Event set inside the loop; cross-thread access goes
        # through ``call_soon_threadsafe``.
        self._loop_stop: asyncio.Event | None = None
        # Runner so ``stop()`` can ``cleanup()`` it cleanly.
        self._runner = None
        # ``True`` once the TCPSite is bound and accepting.  ``False``
        # after ``stop()``.
        self._running = False

    @property
    def host(self) -> str:
        """Root URL the API server is served at."""
        return self._config.host

    @property
    def port(self) -> int:
        """TCP port the API server is listening on."""
        return self._config.port

    # ---- public API ----

    def start(self) -> None:
        """Spawn the daemon thread and start the server (non-blocking).

        Idempotent: a second call while the dashboard is running is a
        no-op.

        In WebSocket-only mode (``serve_frontend=False``) the browser
        is never opened even if :attr:`DashboardConfig.open_browser`
        is ``True`` — there is no UI to point it at.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_wake.clear()
        self._ready.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="api-server",
            daemon=True,
        )
        self._thread.start()
        # Block the caller until the server is bound so ``url`` is
        # valid by the time ``start()`` returns.
        self._ready.wait(timeout=5)
        if not self._ready.is_set():
            raise RuntimeError("API server failed to start within 5s")
        logger.info(
            "API server listening at %s",
            f"{self._config.host}:{self._config.port}",
        )

    def wait(self) -> None:
        """Block the calling thread until :meth:`stop` (or Ctrl+C).

        KeyboardInterrupt is caught and forwarded to :meth:`stop` so
        Ctrl+C cleanly tears the server down.  This is the call that
        keeps the process alive *after* the trading node has stopped
        — the user reviews the final state in the browser and then
        hits Ctrl+C to exit.
        """
        try:
            self._stop_wake.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        """Gracefully stop the server.

        Safe to call from any thread.  Multiple calls are coalesced.
        """
        if not self._running:
            return
        self._running = False
        if self._loop is not None and self._loop_stop is not None:
            self._loop.call_soon_threadsafe(self._loop_stop.set)
        # Wake up ``wait()`` in the main thread.
        self._stop_wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def is_running(self) -> bool:
        """``True`` between :meth:`start` and the end of :meth:`stop`."""
        return self._running

    def _run_loop(self) -> None:
        """Entry point of the daemon thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        app[LOOP_KEY] = self._loop
        for subapp in app._subapps:
            subapp[LOOP_KEY] = self._loop
        try:
            self._loop.run_until_complete(self._serve())
        finally:
            self._loop.close()
            self._loop = None

    async def _serve(self) -> None:
        """Bind the TCPSite and run until :meth:`stop` is called."""
        self._loop_stop = asyncio.Event()
        self._runner = web.AppRunner(app, access_log=None)
        # TO ENHANCE: we are creating task for read websocket queue
        from src.api.router.websocket import websocket

        consumer_task = asyncio.create_task(websocket.manager.consumer_task())
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._config.host, self._config.port)
        try:
            await site.start()
        except OSError as exc:
            # Port in use, etc. — surface the error to the main thread
            # via ``_ready`` so ``start()`` raises and ``wait()`` exits.
            logger.error(
                "Dashboard failed to bind %s:%s — %s", self._config.host, self._config.port, exc
            )
            self._ready.set()
            self._running = False
            return
        self._ready.set()
        try:
            await self._loop_stop.wait()
        finally:
            consumer_task.cancel()
            await asyncio.gather(consumer_task, self._runner.cleanup())
            self._runner = None

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiohttp import web

from src.api.router.base import BaseRouter


if TYPE_CHECKING:
    from src.api.router.websocket.handler import WebsocketHandler


@dataclass(frozen=True)
class TopicSubscription:
    source: str
    client: web.WebSocketResponse


class WebsocketManager(BaseRouter):
    def __init__(self, app: web.Application):
        super().__init__(app)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._clients: set[web.WebSocketResponse] = set()
        self._topics: dict[str, set[TopicSubscription]] = {}
        self.handler: WebsocketHandler | None = None

    def register_handler(self, handler: WebsocketHandler):
        self.handler = handler

    async def connect(self, request: web.Request):
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)

        self._clients.add(ws)
        self.log.info(f"Client connected ({len(self._clients)} total)")
        return ws

    async def disconnect(self, client: web.WebSocketResponse):
        for topic in set(self._topics.keys()):
            for sub in self._topics[topic]:
                if sub.client == client:
                    if self.handler:
                        await self.handler.unsubscribe(topic, client, source=sub.source)
                    else:
                        self.unsubscribe(topic, client, source=sub.source)
        self._clients.discard(client)
        self.log.info(f"Client disconnected ({len(self._clients)} left)")

    def subscribe(self, topic: str, client: web.WebSocketResponse, source: str) -> bool:
        need_run_subscription = False
        if not topic or not client:
            return need_run_subscription
        if topic not in self._topics:
            self._topics[topic] = set()
            need_run_subscription = True
        if client not in self._topics[topic]:
            sub = TopicSubscription(source=source, client=client)
            self._topics[topic].add(sub)
        return need_run_subscription

    def unsubscribe(self, topic: str, client: web.WebSocketResponse, source: str) -> bool:
        need_clear_subscription = False
        if not topic or not client or topic not in self._topics:
            return need_clear_subscription
        subs = self._topics[topic]
        sub = TopicSubscription(source=source, client=client)
        if sub not in subs:
            return need_clear_subscription
        subs.remove(sub)
        if not len(subs):
            self._topics.pop(topic)
            need_clear_subscription = True
        return need_clear_subscription

    def publish(self, topic: str, message: dict):
        self.loop.call_soon_threadsafe(self._queue.put_nowait, (topic, message))

    async def consumer_task(self):
        try:
            while True:
                try:
                    topic, message = await self._queue.get()
                    self.log.debug(f"Received message from topic {topic}")

                    subs = self._topics.get(topic, [])
                    clients = set([sub.client for sub in subs])
                    for client in clients:
                        try:
                            await client.send_json(message)
                        except Exception as e:
                            self.log.error(str(e))
                            if isinstance(e, ConnectionResetError):
                                await self.disconnect(client)
                except Exception as e:
                    self.log.error(str(e))
                self._queue.task_done()
        except asyncio.CancelledError:
            return

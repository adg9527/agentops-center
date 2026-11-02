"""In-process pub/sub used to push live updates to WebSocket clients.

Single-process by design (phase 1); swap for Redis pub/sub when running multiple workers.
"""

import asyncio
import logging

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subs.discard(q)

    def publish(self, topic: str, data: dict):
        msg = {"topic": topic, "data": data}
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning("dropping slow websocket subscriber")
                self._subs.discard(q)


bus = EventBus()

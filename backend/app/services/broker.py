"""Task broker: publish jobs, consume them, report progress.

Two backends behind one interface:

* **RabbitMQ** (`aio-pika`) when `AGENTOPS_RABBITMQ_URL` is configured and reachable —
  the deployment the platform is designed for: a small number of workers can consume
  the same queue from several processes/machines.
* **in-process** `asyncio.Queue` fallback — so a laptop without a broker still runs,
  and tests do not need RabbitMQ.

Queues:

| queue | 方向 | 内容 |
| --- | --- | --- |
| `agentops.tasks` | 发布 → 消费 | `{task_id, run_id, agent_id}` 作业 |
| `agentops.progress` | 发布 → 消费 | 进度/状态事件（仅用于外部订阅者，UI 走 WebSocket） |
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import get_settings

log = logging.getLogger(__name__)

TASK_QUEUE = "agentops.tasks"
PROGRESS_QUEUE = "agentops.progress"

Handler = Callable[[dict[str, Any]], Awaitable[None]]


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class Broker:
    """Publishes task jobs and runs one consumer per queue."""

    def __init__(self) -> None:
        self._connection: Any = None
        self._channel: Any = None
        self._memory: dict[str, asyncio.Queue] = {}
        self._consumers: list[asyncio.Task] = []
        self._handlers: dict[str, Handler] = {}
        self._started = False
        self._connected = False
        self._error = ""
        self._published = 0
        self._consumed = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._spawned: set[str] = set()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self._started and self._loop is not loop:
            # 新的事件循环（例如测试之间）：旧消费者已经随循环结束，重建
            self._consumers.clear()
            self._spawned.clear()
            self._memory.clear()
            self._started = False
            self._connected = False
        if self._started:
            return
        self._loop = loop
        self._started = True
        url = get_settings().rabbitmq_url
        if not url and _port_open("127.0.0.1", 5672):
            # 本机起了 RabbitMQ 但没配环境变量时直接用它（guest 只允许 localhost）
            url = "amqp://guest:guest@127.0.0.1:5672/"
            log.info("broker: detected a local RabbitMQ on 5672, using guest/guest")
        if url:
            try:
                import aio_pika  # 延迟导入：没装 / 没配也不影响启动

                self._connection = await aio_pika.connect_robust(url, timeout=5)
                self._channel = await self._connection.channel()
                await self._channel.declare_queue(TASK_QUEUE, durable=True)
                await self._channel.declare_queue(PROGRESS_QUEUE, durable=True)
                self._connected = True
                log.info("broker: connected to RabbitMQ at %s", url)
            except Exception as exc:  # noqa: BLE001 - fall back to in-process
                self._connection = self._channel = None
                self._connected = False
                self._error = f"{type(exc).__name__}: {exc}"
                log.warning("broker: RabbitMQ unavailable (%s), falling back to in-process queue", self._error)
        if not self._connected:
            self._memory.setdefault(TASK_QUEUE, asyncio.Queue())
            self._memory.setdefault(PROGRESS_QUEUE, asyncio.Queue())
            if not url:
                self._error = "未配置 AGENTOPS_RABBITMQ_URL，使用进程内队列"
        for queue, handler in self._handlers.items():
            self._spawn_consumer(queue, handler)

    async def stop(self) -> None:
        for task in self._consumers:
            task.cancel()
        self._consumers.clear()
        self._spawned.clear()
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:  # noqa: BLE001
                pass
        self._connection = self._channel = None
        self._connected = False
        self._started = False

    def on(self, queue: str, handler: Handler) -> None:
        """Register a consumer for a queue (can be called before `start`)."""
        self._handlers[queue] = handler
        if self._started:
            self._spawn_consumer(queue, handler)

    # ------------------------------------------------------------------- publish

    async def publish(self, queue: str, payload: dict[str, Any]) -> None:
        # 总是走一遍 start()：它会检测事件循环是否换了（测试、重启），必要时重建消费者
        await self.start()
        self._published += 1
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        if self._connected:
            import aio_pika

            await self._channel.default_exchange.publish(
                aio_pika.Message(body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                routing_key=queue,
            )
        else:
            self._memory.setdefault(queue, asyncio.Queue()).put_nowait(payload)

    async def publish_progress(self, payload: dict[str, Any]) -> None:
        await self.publish(PROGRESS_QUEUE, payload)

    # ------------------------------------------------------------------- consume

    def _spawn_consumer(self, queue: str, handler: Handler) -> None:
        if queue in self._spawned:
            return
        self._spawned.add(queue)
        # 慢任务不能堵住队列：工作队列起多个消费者并发取活儿
        workers = max(1, get_settings().max_global_concurrency) if queue == TASK_QUEUE else 1
        for index in range(workers):
            task = asyncio.create_task(self._consume_loop(queue), name=f"broker:{queue}:{index}")
            self._consumers.append(task)

    async def _consume_loop(self, queue: str) -> None:
        if self._connected:
            import aio_pika

            q = await self._channel.declare_queue(queue, durable=True)
            async with q.iterator() as it:
                async for message in it:
                    async with message.process(requeue=False):
                        try:
                            payload = json.loads(message.body)
                            await self._handlers[queue](payload)
                            self._consumed += 1
                        except Exception:  # noqa: BLE001 - 一条坏消息不能拖垮消费者
                            log.exception("broker: handler failed for %s", queue)
            return
        mem = self._memory.setdefault(queue, asyncio.Queue())
        while True:
            payload = await mem.get()
            try:
                await self._handlers[queue](payload)
                self._consumed += 1
            except Exception:  # noqa: BLE001
                log.exception("broker: handler failed for %s", queue)
            finally:
                mem.task_done()

    # -------------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        queues = {}
        for name in (TASK_QUEUE, PROGRESS_QUEUE):
            mem = self._memory.get(name)
            queues[name] = mem.qsize() if mem is not None else None
        return {
            "backend": "rabbitmq" if self._connected else "memory",
            "url": get_settings().rabbitmq_url or "",
            "connected": self._connected,
            "error": self._error,
            "queues": queues,
            "published": self._published,
            "consumed": self._consumed,
            "workers": len([c for c in self._consumers if not c.done()]),
            "scheduler": get_settings().scheduler,
        }


broker = Broker()

import asyncio
import logging
import httpx

from app.models import EventLog

logger = logging.getLogger(__name__)


class EventQueue:
    """Async event queue — events are enqueued by the scheduler and consumed by notification handlers."""

    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue | None = None
        self._maxsize = maxsize
        self._consumer_task: asyncio.Task | None = None
        self._dispatcher: "NotificationDispatcher | None" = None

    def bind(self, dispatcher: "NotificationDispatcher"):
        self._dispatcher = dispatcher

    async def start(self):
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        self._consumer_task = asyncio.create_task(self._consume())
        logger.info("Event queue consumer started")

    async def stop(self):
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        logger.info("Event queue consumer stopped")

    async def put(self, event: EventLog):
        if self._queue is None:
            logger.warning("Event queue not started, dropping event")
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.error("Event queue full, dropping event")

    @property
    def qsize(self) -> int:
        return self._queue.qsize() if self._queue else 0

    async def _consume(self):
        while True:
            event = await self._queue.get()
            try:
                if self._dispatcher:
                    await self._dispatcher.dispatch(event)
            except Exception as e:
                logger.error(f"Event queue consumer error: {e}")
            finally:
                self._queue.task_done()


class NotificationDispatcher:
    """Extensible notification system with handler registry."""

    def __init__(self):
        self._handlers = []

    def register_handler(self, handler):
        self._handlers.append(handler)

    async def dispatch(self, event: EventLog):
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"Notification handler '{handler.__class__.__name__}' error: {e}")


class WebhookHandler:
    """Webhook notification handler — posts event payload to configured URL."""

    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url

    async def __call__(self, event: EventLog):
        if not self.webhook_url:
            return
        payload = {
            "event_type": event.event_type,
            "task_id": event.task_id,
            "payload": event.payload,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.webhook_url, json=payload)
                logger.info(f"Webhook sent to {self.webhook_url}: status={resp.status_code}")
        except Exception as e:
            logger.error(f"Webhook delivery failed: {e}")


event_queue = EventQueue()
dispatcher = NotificationDispatcher()
dispatcher.register_handler(WebhookHandler())
event_queue.bind(dispatcher)

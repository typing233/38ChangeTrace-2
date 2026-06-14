import logging
from app.models import EventLog

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Extensible notification system with webhook support."""

    def __init__(self):
        self._handlers = []

    def register_handler(self, handler):
        self._handlers.append(handler)

    async def dispatch(self, event: EventLog):
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(f"Notification handler error: {e}")


class WebhookHandler:
    """Placeholder webhook handler — extend with actual HTTP calls."""

    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url

    async def __call__(self, event: EventLog):
        if not self.webhook_url:
            return
        logger.info(f"Webhook dispatch to {self.webhook_url}: event_type={event.event_type}, task_id={event.task_id}")


dispatcher = NotificationDispatcher()
dispatcher.register_handler(WebhookHandler())

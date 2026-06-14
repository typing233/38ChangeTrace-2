import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import httpx
from jinja2 import Template
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EventLog, NotificationChannel, TaskChannelBinding, DeliveryLog, Task

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE = """【ChangeTrace 变更通知】
任务: {{ task_name }}
网址: {{ task_url }}
时间: {{ timestamp }}
变化摘要: {{ change_summary }}
差异查看: {{ diff_link }}
"""


class EventQueue:
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
    def __init__(self):
        self._handlers: dict[str, "BaseHandler"] = {}

    def register_handler(self, channel_type: str, handler: "BaseHandler"):
        self._handlers[channel_type] = handler

    async def dispatch(self, event: EventLog):
        from app.database import async_session

        async with async_session() as session:
            task = await session.get(Task, event.task_id)
            if not task:
                return

            result = await session.execute(
                select(TaskChannelBinding, NotificationChannel)
                .join(NotificationChannel, TaskChannelBinding.channel_id == NotificationChannel.id)
                .where(
                    TaskChannelBinding.task_id == event.task_id,
                    TaskChannelBinding.enabled == True,
                    NotificationChannel.enabled == True,
                )
            )
            bindings = result.all()

            any_sent = False
            for binding, channel in bindings:
                sent = await self._deliver(event, task, binding, channel, session)
                if sent:
                    any_sent = True

            if any_sent:
                db_event = await session.get(EventLog, event.id)
                if db_event:
                    db_event.delivered = True
                    await session.commit()

    async def _deliver(
        self, event: EventLog, task: Task,
        binding: TaskChannelBinding, channel: NotificationChannel,
        session: AsyncSession
    ) -> bool:
        if await self._is_duplicate(event.id, channel.id, session):
            await self._log_delivery(
                task.id, channel.id, event.id, "skipped_dedup", 1, "", 0, "", 0, "", session
            )
            return False

        if await self._is_rate_limited(channel.id, channel.rate_limit_per_minute, session):
            await self._log_delivery(
                task.id, channel.id, event.id, "skipped_rate_limit", 1, "", 0, "", 0, "", session
            )
            return False

        handler = self._handlers.get(channel.channel_type)
        if not handler:
            logger.warning(f"No handler for channel type: {channel.channel_type}")
            return False

        template_str = binding.template or DEFAULT_TEMPLATE
        rendered = self._render_template(template_str, task, event)

        for attempt in range(1, settings.NOTIFICATION_RETRY_MAX + 1):
            t0 = time.time()
            try:
                result = await handler.send(rendered, channel.config)
                latency = int((time.time() - t0) * 1000)
                await self._log_delivery(
                    task.id, channel.id, event.id, "sent", attempt,
                    rendered[:2000], result.get("status_code", 200),
                    result.get("response", "")[:500], latency, "", session
                )
                return True
            except Exception as e:
                latency = int((time.time() - t0) * 1000)
                await self._log_delivery(
                    task.id, channel.id, event.id, "failed", attempt,
                    rendered[:2000], 0, "", latency, str(e)[:500], session
                )
                if attempt < settings.NOTIFICATION_RETRY_MAX:
                    await asyncio.sleep(min(2 ** attempt * 5, 60))

        return False

    def _render_template(self, template_str: str, task: Task, event: EventLog) -> str:
        try:
            tmpl = Template(template_str)
            payload = event.payload or {}
            snapshot_id = payload.get("snapshot_id", "")
            diff_link = f"/api/snapshots/{snapshot_id}/diff" if snapshot_id else ""
            return tmpl.render(
                task_name=task.name,
                task_url=task.url,
                task_id=task.id,
                timestamp=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                change_summary=f"内容哈希变更: {payload.get('old_hash', '')[:8]}→{payload.get('new_hash', '')[:8]}",
                old_hash=payload.get("old_hash", ""),
                new_hash=payload.get("new_hash", ""),
                event_type=event.event_type,
                diff_link=diff_link,
            )
        except Exception as e:
            logger.error(f"Template render error: {e}")
            return f"[ChangeTrace] 任务 {task.name} 发生变更 ({task.url})"

    async def _is_duplicate(self, event_id: int, channel_id: int, session: AsyncSession) -> bool:
        if not event_id:
            return False
        result = await session.execute(
            select(DeliveryLog).where(
                DeliveryLog.event_id == event_id,
                DeliveryLog.channel_id == channel_id,
                DeliveryLog.status == "sent",
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _is_rate_limited(self, channel_id: int, limit: int, session: AsyncSession) -> bool:
        one_minute_ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
        result = await session.execute(
            select(func.count(DeliveryLog.id)).where(
                DeliveryLog.channel_id == channel_id,
                DeliveryLog.created_at >= one_minute_ago,
                DeliveryLog.status.in_(["sent", "pending"]),
            )
        )
        count = result.scalar() or 0
        return count >= limit

    async def _log_delivery(
        self, task_id, channel_id, event_id, status, attempt,
        request_payload, response_status, response_body, latency_ms, error_message,
        session: AsyncSession
    ):
        log = DeliveryLog(
            task_id=task_id,
            channel_id=channel_id,
            event_id=event_id,
            status=status,
            attempt=attempt,
            request_payload=request_payload,
            response_status=response_status,
            response_body=response_body,
            latency_ms=latency_ms,
            error_message=error_message,
        )
        session.add(log)
        await session.commit()


class BaseHandler:
    async def send(self, message: str, config: dict) -> dict:
        raise NotImplementedError


class WebhookHandler(BaseHandler):
    async def send(self, message: str, config: dict) -> dict:
        url = config.get("url", "")
        if not url:
            raise ValueError("Webhook URL not configured")

        payload = {"text": message, "timestamp": datetime.datetime.utcnow().isoformat()}
        body = json.dumps(payload, ensure_ascii=False)
        headers = {"Content-Type": "application/json"}

        secret = config.get("secret", "")
        if secret:
            sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Signature-256"] = f"sha256={sig}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, content=body, headers=headers)
            resp.raise_for_status()
            return {"status_code": resp.status_code, "response": resp.text[:200]}


class EmailHandler(BaseHandler):
    async def send(self, message: str, config: dict) -> dict:
        import aiosmtplib
        from email.mime.text import MIMEText

        host = config.get("smtp_host") or settings.SMTP_HOST
        port = config.get("smtp_port") or settings.SMTP_PORT
        user = config.get("smtp_user") or settings.SMTP_USER
        password = config.get("smtp_password") or settings.SMTP_PASSWORD
        from_addr = config.get("smtp_from") or settings.SMTP_FROM
        to_addrs = config.get("to", [])
        use_tls = config.get("use_tls", settings.SMTP_USE_TLS)

        if not host or not to_addrs:
            raise ValueError("Email config incomplete: need smtp_host and to addresses")

        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = config.get("subject", "ChangeTrace 变更通知")
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs) if isinstance(to_addrs, list) else to_addrs

        await aiosmtplib.send(
            msg,
            hostname=host,
            port=port,
            username=user,
            password=password,
            use_tls=use_tls,
        )
        return {"status_code": 250, "response": "sent"}


class TelegramHandler(BaseHandler):
    async def send(self, message: str, config: dict) -> dict:
        token = config.get("bot_token") or settings.TELEGRAM_BOT_TOKEN
        chat_id = config.get("chat_id", "")
        if not token or not chat_id:
            raise ValueError("Telegram config incomplete: need bot_token and chat_id")

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return {"status_code": resp.status_code, "response": resp.text[:200]}


class DingTalkHandler(BaseHandler):
    async def send(self, message: str, config: dict) -> dict:
        webhook_url = config.get("webhook_url") or settings.DINGTALK_WEBHOOK_URL
        secret = config.get("secret") or settings.DINGTALK_SECRET
        if not webhook_url:
            raise ValueError("DingTalk webhook_url not configured")

        if secret:
            timestamp = str(int(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
            import base64
            sign = base64.b64encode(hmac_code).decode()
            separator = "&" if "?" in webhook_url else "?"
            webhook_url = f"{webhook_url}{separator}timestamp={timestamp}&sign={sign}"

        payload = {"msgtype": "text", "text": {"content": message}}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            return {"status_code": resp.status_code, "response": resp.text[:200]}


class SlackHandler(BaseHandler):
    async def send(self, message: str, config: dict) -> dict:
        webhook_url = config.get("webhook_url") or settings.SLACK_WEBHOOK_URL
        if not webhook_url:
            raise ValueError("Slack webhook_url not configured")

        payload = {"text": message}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            return {"status_code": resp.status_code, "response": resp.text[:200]}


event_queue = EventQueue()
dispatcher = NotificationDispatcher()
dispatcher.register_handler("webhook", WebhookHandler())
dispatcher.register_handler("email", EmailHandler())
dispatcher.register_handler("telegram", TelegramHandler())
dispatcher.register_handler("dingtalk", DingTalkHandler())
dispatcher.register_handler("slack", SlackHandler())
event_queue.bind(dispatcher)

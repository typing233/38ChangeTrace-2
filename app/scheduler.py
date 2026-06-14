import asyncio
import datetime
import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.models import Base, Task, Snapshot, EventLog
from app.fetcher import fetch_static, fetch_js, normalize_and_extract, compute_hash
from app.differ import compute_diff
from app.notifier import dispatcher

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "./data")
DB_PATH = os.path.join(DATA_DIR, "changetrace.db")
DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"
SCREENSHOTS_DIR = os.path.join(DATA_DIR, "screenshots")

engine = create_async_engine(DB_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_semaphore = asyncio.Semaphore(5)
_running_tasks: set[int] = set()

scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{DB_PATH}")},
    executors={"default": ThreadPoolExecutor(10)},
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
)


async def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def restore_jobs():
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.status == "active"))
        tasks = result.scalars().all()
        for task in tasks:
            schedule_task(task)


def schedule_task(task: Task):
    job_id = f"task_{task.id}"
    scheduler.add_job(
        run_task,
        "interval",
        seconds=task.interval_seconds,
        id=job_id,
        replace_existing=True,
        args=[task.id],
        next_run_time=datetime.datetime.utcnow() + datetime.timedelta(seconds=5),
    )


def unschedule_task(task_id: int):
    job_id = f"task_{task_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


async def run_task(task_id: int):
    if task_id in _running_tasks:
        return
    _running_tasks.add(task_id)
    try:
        await _execute_task(task_id)
    finally:
        _running_tasks.discard(task_id)


async def _execute_task(task_id: int):
    async with _semaphore:
        async with async_session() as session:
            task = await session.get(Task, task_id)
            if not task or task.status != "active":
                return

            try:
                screenshot_path = ""
                if task.render_mode == "js":
                    screenshot_path = os.path.join(SCREENSHOTS_DIR, f"task_{task.id}_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}.png")
                    html = await fetch_js(task.url, task.headers or {}, task.cookies or {}, task.proxy, task.timeout_seconds, screenshot_path)
                else:
                    html = await fetch_static(task.url, task.headers or {}, task.cookies or {}, task.proxy, task.timeout_seconds)

                normalized_html, extracted_text = normalize_and_extract(html, task.include_selector, task.exclude_selector)
                content_hash = compute_hash(extracted_text)

                if content_hash != task.last_hash:
                    snapshot = Snapshot(
                        task_id=task.id,
                        content_hash=content_hash,
                        raw_html=normalized_html,
                        extracted_text=extracted_text,
                        resource_meta={"url": task.url, "render_mode": task.render_mode},
                        screenshot_path=screenshot_path,
                    )
                    session.add(snapshot)

                    event = EventLog(
                        task_id=task.id,
                        event_type="change_detected",
                        payload={"old_hash": task.last_hash, "new_hash": content_hash},
                    )
                    session.add(event)

                    task.last_hash = content_hash
                    await dispatcher.dispatch(event)

                task.last_run_at = datetime.datetime.utcnow()
                task.next_run_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=task.interval_seconds)
                task.retry_count = 0
                task.last_error = ""
                await session.commit()

            except Exception as e:
                task.retry_count += 1
                task.last_error = str(e)
                task.last_run_at = datetime.datetime.utcnow()

                if task.retry_count >= task.max_retries:
                    task.status = "error"
                    unschedule_task(task.id)
                else:
                    backoff = min(2 ** task.retry_count * task.interval_seconds, 3600)
                    task.next_run_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=backoff)

                event = EventLog(task_id=task.id, event_type="error", payload={"error": str(e), "retry": task.retry_count})
                session.add(event)
                await session.commit()
                logger.error(f"Task {task.id} failed: {e}")

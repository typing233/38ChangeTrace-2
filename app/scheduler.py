import asyncio
import datetime
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.models import Base, Task, Snapshot, EventLog
from app.fetcher import fetch_static, fetch_js, normalize_and_extract, compute_hash
from app.notifier import event_queue

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
    executors={"default": AsyncIOExecutor()},
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
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
        logger.info(f"Restored {len(tasks)} active jobs")


def schedule_task(task: Task):
    job_id = f"task_{task.id}"
    next_run = datetime.datetime.utcnow() + datetime.timedelta(seconds=5)
    scheduler.add_job(
        run_task,
        trigger=IntervalTrigger(seconds=task.interval_seconds),
        id=job_id,
        replace_existing=True,
        args=[task.id],
        next_run_time=next_run,
    )
    logger.info(f"Scheduled task {task.id} every {task.interval_seconds}s, next run: {next_run}")


def reschedule_with_backoff(task_id: int, backoff_seconds: int):
    """Reschedule a failed task to retry after backoff_seconds."""
    job_id = f"task_{task_id}"
    next_run = datetime.datetime.utcnow() + datetime.timedelta(seconds=backoff_seconds)
    try:
        job = scheduler.get_job(job_id)
        if job:
            job.modify(next_run_time=next_run)
            logger.info(f"Task {task_id} rescheduled with {backoff_seconds}s backoff, next run: {next_run}")
        else:
            scheduler.add_job(
                run_task,
                trigger=DateTrigger(run_date=next_run),
                id=f"{job_id}_retry",
                replace_existing=True,
                args=[task_id],
            )
    except Exception as e:
        logger.error(f"Failed to reschedule task {task_id}: {e}")


def unschedule_task(task_id: int):
    job_id = f"task_{task_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    try:
        scheduler.remove_job(f"{job_id}_retry")
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
                    screenshot_path = os.path.join(
                        SCREENSHOTS_DIR,
                        f"task_{task.id}_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}.png"
                    )
                    html, resource_meta = await fetch_js(
                        task.url, task.headers or {}, task.cookies or {},
                        task.proxy, task.timeout_seconds, screenshot_path
                    )
                else:
                    html, resource_meta = await fetch_static(
                        task.url, task.headers or {}, task.cookies or {},
                        task.proxy, task.timeout_seconds
                    )

                normalized_html, extracted_text = normalize_and_extract(
                    html, task.include_selector, task.exclude_selector
                )
                content_hash = compute_hash(extracted_text)

                if content_hash != task.last_hash:
                    snapshot = Snapshot(
                        task_id=task.id,
                        content_hash=content_hash,
                        raw_html=normalized_html,
                        extracted_text=extracted_text,
                        resource_meta=resource_meta,
                        screenshot_path=screenshot_path,
                    )
                    session.add(snapshot)

                    event = EventLog(
                        task_id=task.id,
                        event_type="change_detected",
                        payload={
                            "old_hash": task.last_hash,
                            "new_hash": content_hash,
                            "resource_meta": resource_meta,
                        },
                    )
                    session.add(event)
                    task.last_hash = content_hash
                    await session.commit()
                    await event_queue.put(event)
                else:
                    pass

                now = datetime.datetime.utcnow()
                task.last_run_at = now
                task.next_run_at = now + datetime.timedelta(seconds=task.interval_seconds)
                task.retry_count = 0
                task.last_error = ""
                await session.commit()
                logger.info(f"Task {task.id} completed, hash={'changed' if content_hash != task.last_hash else 'same'}")

            except Exception as e:
                task.retry_count += 1
                task.last_error = str(e)
                task.last_run_at = datetime.datetime.utcnow()

                if task.retry_count >= task.max_retries:
                    task.status = "error"
                    unschedule_task(task.id)
                    task.next_run_at = None
                else:
                    backoff = min(2 ** task.retry_count * 30, 3600)
                    task.next_run_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=backoff)
                    reschedule_with_backoff(task.id, backoff)

                event = EventLog(
                    task_id=task.id,
                    event_type="error",
                    payload={"error": str(e), "retry_count": task.retry_count, "max_retries": task.max_retries},
                )
                session.add(event)
                await session.commit()
                await event_queue.put(event)
                logger.error(f"Task {task.id} failed (attempt {task.retry_count}/{task.max_retries}): {e}")

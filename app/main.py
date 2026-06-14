import datetime
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import select, update

from app.models import Task, Snapshot, EventLog
from app.schemas import TaskCreate, TaskUpdate, TaskOut, SnapshotOut, DiffResult
from app.scheduler import (
    async_session, init_db, restore_jobs, scheduler,
    schedule_task, unschedule_task, run_task,
)
from app.fetcher import fetch_static, fetch_js, normalize_and_extract
from app.differ import compute_diff

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler.start()
    await restore_jobs()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="ChangeTrace", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")


# --- Task CRUD ---

@app.post("/api/tasks", response_model=TaskOut)
async def create_task(body: TaskCreate):
    async with async_session() as session:
        task = Task(**body.model_dump())
        task.next_run_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=5)
        session.add(task)
        await session.commit()
        await session.refresh(task)
        schedule_task(task)
        return task


@app.get("/api/tasks", response_model=list[TaskOut])
async def list_tasks():
    async with async_session() as session:
        result = await session.execute(select(Task).order_by(Task.created_at.desc()))
        return result.scalars().all()


@app.get("/api/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: int):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404)
        return task


@app.put("/api/tasks/{task_id}", response_model=TaskOut)
async def update_task(task_id: int, body: TaskUpdate):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404)
        data = body.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(task, k, v)
        task.updated_at = datetime.datetime.utcnow()
        await session.commit()
        await session.refresh(task)
        if task.status == "active":
            schedule_task(task)
        else:
            unschedule_task(task.id)
        return task


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404)
        unschedule_task(task.id)
        await session.delete(task)
        await session.commit()
        return {"ok": True}


# --- Batch operations ---

@app.post("/api/tasks/batch/pause")
async def batch_pause(task_ids: list[int]):
    async with async_session() as session:
        await session.execute(update(Task).where(Task.id.in_(task_ids)).values(status="paused"))
        await session.commit()
    for tid in task_ids:
        unschedule_task(tid)
    return {"ok": True}


@app.post("/api/tasks/batch/resume")
async def batch_resume(task_ids: list[int]):
    async with async_session() as session:
        await session.execute(update(Task).where(Task.id.in_(task_ids)).values(status="active", retry_count=0, last_error=""))
        await session.commit()
        result = await session.execute(select(Task).where(Task.id.in_(task_ids)))
        for task in result.scalars():
            schedule_task(task)
    return {"ok": True}


@app.post("/api/tasks/batch/delete")
async def batch_delete(task_ids: list[int]):
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id.in_(task_ids)))
        for task in result.scalars():
            unschedule_task(task.id)
            await session.delete(task)
        await session.commit()
    return {"ok": True}


# --- Manual trigger ---

@app.post("/api/tasks/{task_id}/trigger")
async def trigger_task(task_id: int):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404)
    await run_task(task_id)
    return {"ok": True}


# --- Snapshots & Diff ---

@app.get("/api/tasks/{task_id}/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(task_id: int, limit: int = Query(20, le=100)):
    async with async_session() as session:
        result = await session.execute(
            select(Snapshot).where(Snapshot.task_id == task_id).order_by(Snapshot.created_at.desc()).limit(limit)
        )
        return result.scalars().all()


@app.get("/api/snapshots/{snapshot_id}/diff", response_model=DiffResult)
async def get_diff(snapshot_id: int):
    async with async_session() as session:
        snapshot = await session.get(Snapshot, snapshot_id)
        if not snapshot:
            raise HTTPException(404)
        prev = await session.execute(
            select(Snapshot)
            .where(Snapshot.task_id == snapshot.task_id, Snapshot.id < snapshot.id)
            .order_by(Snapshot.id.desc())
            .limit(1)
        )
        prev_snapshot = prev.scalar_one_or_none()
        old_text = prev_snapshot.extracted_text if prev_snapshot else ""
        diff = compute_diff(old_text, snapshot.extracted_text)
        return DiffResult(
            old_snapshot_id=prev_snapshot.id if prev_snapshot else None,
            new_snapshot_id=snapshot.id,
            **diff,
        )


# --- Preview (live CSS selector test) ---

@app.post("/api/preview")
async def preview_content(body: dict):
    url = body.get("url", "")
    include_selector = body.get("include_selector", "")
    exclude_selector = body.get("exclude_selector", "")
    render_mode = body.get("render_mode", "static")
    headers = body.get("headers", {})
    cookies = body.get("cookies", {})
    proxy = body.get("proxy", "")

    if not url:
        raise HTTPException(400, "url required")
    try:
        if render_mode == "js":
            html = await fetch_js(url, headers, cookies, proxy, 15)
        else:
            html = await fetch_static(url, headers, cookies, proxy, 15)
        _, text = normalize_and_extract(html, include_selector, exclude_selector)
        return {"text": text[:5000]}
    except Exception as e:
        raise HTTPException(400, str(e))


# --- Events ---

@app.get("/api/tasks/{task_id}/events")
async def list_events(task_id: int, limit: int = Query(50, le=200)):
    async with async_session() as session:
        result = await session.execute(
            select(EventLog).where(EventLog.task_id == task_id).order_by(EventLog.created_at.desc()).limit(limit)
        )
        events = result.scalars().all()
        return [{"id": e.id, "event_type": e.event_type, "payload": e.payload, "created_at": e.created_at.isoformat()} for e in events]

import datetime
import json
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.auth import require_auth
from app.database import async_session
from app.models import Task, NotificationChannel, TaskChannelBinding, MonitoringRule
from app.notifier import event_queue
from app.scheduler import scheduler

router = APIRouter(tags=["admin"])


@router.get("/health")
async def health_check():
    async with async_session() as session:
        try:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "version": "2.0.0",
        "db_ok": db_ok,
        "scheduler_running": scheduler.running,
        "active_jobs": len(scheduler.get_jobs()),
        "queue_size": event_queue.qsize,
    }


@router.get("/admin/export")
async def export_config(user: str = Depends(require_auth)):
    async with async_session() as session:
        tasks = (await session.execute(select(Task))).scalars().all()
        channels = (await session.execute(select(NotificationChannel))).scalars().all()
        bindings = (await session.execute(select(TaskChannelBinding))).scalars().all()
        rules = (await session.execute(select(MonitoringRule))).scalars().all()

    data = {
        "version": 2,
        "exported_at": datetime.datetime.utcnow().isoformat(),
        "tasks": [
            {
                "name": t.name, "url": t.url, "interval_seconds": t.interval_seconds,
                "headers": t.headers, "cookies": t.cookies, "proxy": t.proxy,
                "render_mode": t.render_mode, "include_selector": t.include_selector,
                "exclude_selector": t.exclude_selector, "max_retries": t.max_retries,
                "timeout_seconds": t.timeout_seconds, "status": t.status,
            }
            for t in tasks
        ],
        "channels": [
            {
                "name": c.name, "channel_type": c.channel_type, "config": c.config,
                "rate_limit_per_minute": c.rate_limit_per_minute, "enabled": c.enabled,
            }
            for c in channels
        ],
        "bindings": [
            {"task_id": b.task_id, "channel_id": b.channel_id, "template": b.template, "enabled": b.enabled}
            for b in bindings
        ],
        "rules": [
            {
                "task_id": r.task_id, "rule_type": r.rule_type, "config": r.config,
                "logic_group": r.logic_group, "enabled": r.enabled,
            }
            for r in rules
        ],
    }
    return JSONResponse(content=data, headers={"Content-Disposition": "attachment; filename=changetrace_export.json"})


@router.post("/admin/import")
async def import_config(body: dict, user: str = Depends(require_auth)):
    from app.scheduler import schedule_task

    if body.get("version", 0) < 2:
        raise HTTPException(400, "不支持的导入格式版本")

    imported = {"tasks": 0, "channels": 0, "rules": 0, "bindings": 0}

    async with async_session() as session:
        task_id_map = {}
        for t_data in body.get("tasks", []):
            existing = await session.execute(
                select(Task).where(Task.name == t_data["name"], Task.url == t_data["url"])
            )
            if existing.scalar_one_or_none():
                continue
            task = Task(**t_data)
            task.next_run_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=10)
            session.add(task)
            await session.flush()
            task_id_map[t_data["name"]] = task.id
            imported["tasks"] += 1

        channel_id_map = {}
        for c_data in body.get("channels", []):
            existing = await session.execute(
                select(NotificationChannel).where(NotificationChannel.name == c_data["name"])
            )
            if existing.scalar_one_or_none():
                continue
            ch = NotificationChannel(**c_data)
            session.add(ch)
            await session.flush()
            channel_id_map[c_data["name"]] = ch.id
            imported["channels"] += 1

        for r_data in body.get("rules", []):
            rule = MonitoringRule(**r_data)
            session.add(rule)
            imported["rules"] += 1

        for b_data in body.get("bindings", []):
            binding = TaskChannelBinding(**b_data)
            session.add(binding)
            imported["bindings"] += 1

        await session.commit()

        result = await session.execute(select(Task).where(Task.status == "active"))
        for task in result.scalars():
            schedule_task(task)

    return {"ok": True, "imported": imported}


@router.get("/queue/status")
async def queue_status(user: str = Depends(require_auth)):
    return {"pending_events": event_queue.qsize}

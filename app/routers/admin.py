import datetime
import json
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.auth import require_auth
from app.database import async_session
from app.models import Task, NotificationChannel, TaskChannelBinding, MonitoringRule, AuditLog
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
        session.add(AuditLog(user=user, action="admin.export", resource_type="system"))
        await session.commit()

    data = {
        "version": 2,
        "exported_at": datetime.datetime.utcnow().isoformat(),
        "tasks": [
            {
                "id": t.id,
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
                "id": c.id,
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

    imported = {"tasks": 0, "channels": 0, "rules": 0, "bindings": 0, "skipped": 0}

    async with async_session() as session:
        old_task_id_map = {}
        for idx, t_data in enumerate(body.get("tasks", [])):
            old_id = t_data.pop("id", idx)
            existing = await session.execute(
                select(Task).where(Task.name == t_data["name"], Task.url == t_data["url"])
            )
            found = existing.scalar_one_or_none()
            if found:
                old_task_id_map[old_id] = found.id
                imported["skipped"] += 1
                continue
            task = Task(**t_data)
            task.next_run_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=task.interval_seconds)
            session.add(task)
            await session.flush()
            old_task_id_map[old_id] = task.id
            imported["tasks"] += 1

        old_channel_id_map = {}
        for idx, c_data in enumerate(body.get("channels", [])):
            old_id = c_data.pop("id", idx)
            existing = await session.execute(
                select(NotificationChannel).where(NotificationChannel.name == c_data["name"])
            )
            found = existing.scalar_one_or_none()
            if found:
                old_channel_id_map[old_id] = found.id
                imported["skipped"] += 1
                continue
            ch = NotificationChannel(**c_data)
            session.add(ch)
            await session.flush()
            old_channel_id_map[old_id] = ch.id
            imported["channels"] += 1

        for r_data in body.get("rules", []):
            r_data.pop("id", None)
            old_task_id = r_data.pop("task_id", None)
            new_task_id = old_task_id_map.get(old_task_id)
            if new_task_id is None:
                continue
            rule = MonitoringRule(task_id=new_task_id, **r_data)
            session.add(rule)
            imported["rules"] += 1

        for b_data in body.get("bindings", []):
            b_data.pop("id", None)
            old_task_id = b_data.pop("task_id", None)
            old_channel_id = b_data.pop("channel_id", None)
            new_task_id = old_task_id_map.get(old_task_id)
            new_channel_id = old_channel_id_map.get(old_channel_id)
            if new_task_id is None or new_channel_id is None:
                continue
            existing_binding = await session.execute(
                select(TaskChannelBinding).where(
                    TaskChannelBinding.task_id == new_task_id,
                    TaskChannelBinding.channel_id == new_channel_id,
                )
            )
            if existing_binding.scalar_one_or_none():
                continue
            binding = TaskChannelBinding(task_id=new_task_id, channel_id=new_channel_id, **b_data)
            session.add(binding)
            imported["bindings"] += 1

        await session.commit()

        session.add(AuditLog(user=user, action="admin.import", resource_type="system", new_value=imported))
        await session.commit()

        result = await session.execute(select(Task).where(Task.status == "active"))
        for task in result.scalars():
            schedule_task(task)

    return {"ok": True, "imported": imported}


@router.get("/queue/status")
async def queue_status(user: str = Depends(require_auth)):
    return {"pending_events": event_queue.qsize}

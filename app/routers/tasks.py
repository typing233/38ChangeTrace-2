import datetime
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy import select, update

from app.auth import require_auth
from app.database import async_session
from app.models import Task, MonitoringRule, AuditLog
from app.schemas import TaskCreate, TaskUpdate, TaskOut, RuleCreate, RuleUpdate, RuleOut
from app.scheduler import schedule_task, unschedule_task, run_task

router = APIRouter(tags=["tasks"])


@router.post("/tasks", response_model=TaskOut)
async def create_task(body: TaskCreate, user: str = Depends(require_auth)):
    async with async_session() as session:
        task = Task(**body.model_dump())
        task.next_run_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=task.interval_seconds)
        session.add(task)
        await session.commit()
        await session.refresh(task)
        schedule_task(task)
        session.add(AuditLog(user=user, action="task.create", resource_type="task", resource_id=task.id, new_value=body.model_dump()))
        await session.commit()
        return task


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(user: str = Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(select(Task).order_by(Task.created_at.desc()))
        return result.scalars().all()


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: int, user: str = Depends(require_auth)):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404)
        return task


@router.put("/tasks/{task_id}", response_model=TaskOut)
async def update_task(task_id: int, body: TaskUpdate, user: str = Depends(require_auth)):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404)
        if body.version is not None and body.version != task.version:
            raise HTTPException(409, "该任务已被其他操作修改，请刷新后重试")
        old_data = {"name": task.name, "url": task.url, "status": task.status}
        data = body.model_dump(exclude_unset=True)
        data.pop("version", None)
        for k, v in data.items():
            setattr(task, k, v)
        task.version += 1
        task.updated_at = datetime.datetime.utcnow()
        await session.commit()
        await session.refresh(task)
        if task.status == "active":
            schedule_task(task)
        else:
            unschedule_task(task.id)
        session.add(AuditLog(user=user, action="task.update", resource_type="task", resource_id=task.id, old_value=old_data, new_value=data))
        await session.commit()
        return task


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, user: str = Depends(require_auth)):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404)
        unschedule_task(task.id)
        await session.delete(task)
        session.add(AuditLog(user=user, action="task.delete", resource_type="task", resource_id=task_id))
        await session.commit()
        return {"ok": True}


@router.post("/tasks/batch/pause")
async def batch_pause(task_ids: list[int], user: str = Depends(require_auth)):
    async with async_session() as session:
        await session.execute(update(Task).where(Task.id.in_(task_ids)).values(status="paused"))
        session.add(AuditLog(user=user, action="task.batch_pause", resource_type="task", new_value={"task_ids": task_ids}))
        await session.commit()
    for tid in task_ids:
        unschedule_task(tid)
    return {"ok": True}


@router.post("/tasks/batch/resume")
async def batch_resume(task_ids: list[int], user: str = Depends(require_auth)):
    async with async_session() as session:
        await session.execute(update(Task).where(Task.id.in_(task_ids)).values(status="active", retry_count=0, last_error=""))
        session.add(AuditLog(user=user, action="task.batch_resume", resource_type="task", new_value={"task_ids": task_ids}))
        await session.commit()
        result = await session.execute(select(Task).where(Task.id.in_(task_ids)))
        for task in result.scalars():
            schedule_task(task)
    return {"ok": True}


@router.post("/tasks/batch/delete")
async def batch_delete(task_ids: list[int], user: str = Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(select(Task).where(Task.id.in_(task_ids)))
        for task in result.scalars():
            unschedule_task(task.id)
            await session.delete(task)
        session.add(AuditLog(user=user, action="task.batch_delete", resource_type="task", new_value={"task_ids": task_ids}))
        await session.commit()
    return {"ok": True}


@router.post("/tasks/{task_id}/trigger")
async def trigger_task(task_id: int, user: str = Depends(require_auth)):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404)
    await run_task(task_id)
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if task and task.status == "active":
            task.next_run_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=task.interval_seconds)
            await session.commit()
    return {"ok": True}


@router.post("/tasks/{task_id}/reset-error")
async def reset_error(task_id: int, user: str = Depends(require_auth)):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404)
        task.status = "active"
        task.retry_count = 0
        task.last_error = ""
        task.version += 1
        await session.commit()
        await session.refresh(task)
        schedule_task(task)
        return {"ok": True}


# --- Monitoring Rules ---

@router.get("/tasks/{task_id}/rules", response_model=list[RuleOut])
async def list_rules(task_id: int, user: str = Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(
            select(MonitoringRule).where(MonitoringRule.task_id == task_id)
        )
        return result.scalars().all()


@router.post("/tasks/{task_id}/rules", response_model=RuleOut)
async def create_rule(task_id: int, body: RuleCreate, user: str = Depends(require_auth)):
    async with async_session() as session:
        task = await session.get(Task, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        rule = MonitoringRule(task_id=task_id, **body.model_dump())
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        session.add(AuditLog(user=user, action="rule.create", resource_type="rule", resource_id=rule.id, new_value={"task_id": task_id, **body.model_dump()}))
        await session.commit()
        return rule


@router.put("/rules/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: int, body: RuleUpdate, user: str = Depends(require_auth)):
    async with async_session() as session:
        rule = await session.get(MonitoringRule, rule_id)
        if not rule:
            raise HTTPException(404)
        old_data = {"rule_type": rule.rule_type, "config": rule.config, "logic_group": rule.logic_group}
        data = body.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(rule, k, v)
        await session.commit()
        await session.refresh(rule)
        session.add(AuditLog(user=user, action="rule.update", resource_type="rule", resource_id=rule_id, old_value=old_data, new_value=data))
        await session.commit()
        return rule


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, user: str = Depends(require_auth)):
    async with async_session() as session:
        rule = await session.get(MonitoringRule, rule_id)
        if not rule:
            raise HTTPException(404)
        await session.delete(rule)
        session.add(AuditLog(user=user, action="rule.delete", resource_type="rule", resource_id=rule_id))
        await session.commit()
        return {"ok": True}

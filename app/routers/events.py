from fastapi import APIRouter, Query, Depends
from sqlalchemy import select

from app.auth import require_auth
from app.database import async_session
from app.models import EventLog, AuditLog

router = APIRouter(tags=["events"])


@router.get("/events")
async def search_events(
    task_id: int = Query(None),
    event_type: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    keyword: str = Query(None),
    limit: int = Query(50, le=200),
    user: str = Depends(require_auth),
):
    async with async_session() as session:
        q = select(EventLog).order_by(EventLog.created_at.desc())
        if task_id:
            q = q.where(EventLog.task_id == task_id)
        if event_type:
            q = q.where(EventLog.event_type == event_type)
        if date_from:
            q = q.where(EventLog.created_at >= date_from)
        if date_to:
            q = q.where(EventLog.created_at <= date_to)
        q = q.limit(limit)
        result = await session.execute(q)
        events = result.scalars().all()
        out = []
        for e in events:
            item = {
                "id": e.id,
                "task_id": e.task_id,
                "event_type": e.event_type,
                "payload": e.payload,
                "delivered": e.delivered,
                "notified": e.notified,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            if keyword and keyword.lower() not in str(e.payload).lower():
                continue
            out.append(item)
        return out


@router.get("/audit-log")
async def query_audit_log(
    action: str = Query(None),
    resource_type: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    limit: int = Query(50, le=200),
    user: str = Depends(require_auth),
):
    async with async_session() as session:
        q = select(AuditLog).order_by(AuditLog.created_at.desc())
        if action:
            q = q.where(AuditLog.action == action)
        if resource_type:
            q = q.where(AuditLog.resource_type == resource_type)
        if date_from:
            q = q.where(AuditLog.created_at >= date_from)
        if date_to:
            q = q.where(AuditLog.created_at <= date_to)
        q = q.limit(limit)
        result = await session.execute(q)
        logs = result.scalars().all()
        return [
            {
                "id": l.id,
                "user": l.user,
                "action": l.action,
                "resource_type": l.resource_type,
                "resource_id": l.resource_id,
                "old_value": l.old_value,
                "new_value": l.new_value,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ]

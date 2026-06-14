import datetime
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy import select

from app.auth import require_auth
from app.database import async_session
from app.models import NotificationChannel, TaskChannelBinding, DeliveryLog
from app.schemas import ChannelCreate, ChannelUpdate, ChannelOut, BindingCreate, BindingOut, DeliveryLogOut

router = APIRouter(tags=["notifications"])


@router.post("/channels", response_model=ChannelOut)
async def create_channel(body: ChannelCreate, user: str = Depends(require_auth)):
    async with async_session() as session:
        ch = NotificationChannel(**body.model_dump())
        session.add(ch)
        await session.commit()
        await session.refresh(ch)
        return ch


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(user: str = Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(select(NotificationChannel).order_by(NotificationChannel.created_at.desc()))
        return result.scalars().all()


@router.put("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel(channel_id: int, body: ChannelUpdate, user: str = Depends(require_auth)):
    async with async_session() as session:
        ch = await session.get(NotificationChannel, channel_id)
        if not ch:
            raise HTTPException(404)
        data = body.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(ch, k, v)
        ch.updated_at = datetime.datetime.utcnow()
        await session.commit()
        await session.refresh(ch)
        return ch


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: int, user: str = Depends(require_auth)):
    async with async_session() as session:
        ch = await session.get(NotificationChannel, channel_id)
        if not ch:
            raise HTTPException(404)
        await session.delete(ch)
        await session.commit()
        return {"ok": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: int, user: str = Depends(require_auth)):
    from app.notifier import dispatcher
    async with async_session() as session:
        ch = await session.get(NotificationChannel, channel_id)
        if not ch:
            raise HTTPException(404)
    handler = dispatcher._handlers.get(ch.channel_type)
    if not handler:
        raise HTTPException(400, f"不支持的通道类型: {ch.channel_type}")
    try:
        result = await handler.send("【ChangeTrace 测试通知】这是一条测试消息，确认通道配置正常。", ch.config)
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(400, f"发送失败: {str(e)}")


@router.get("/tasks/{task_id}/channels", response_model=list[BindingOut])
async def list_task_bindings(task_id: int, user: str = Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(
            select(TaskChannelBinding).where(TaskChannelBinding.task_id == task_id)
        )
        return result.scalars().all()


@router.post("/tasks/{task_id}/channels", response_model=BindingOut)
async def bind_channel(task_id: int, body: BindingCreate, user: str = Depends(require_auth)):
    async with async_session() as session:
        existing = await session.execute(
            select(TaskChannelBinding).where(
                TaskChannelBinding.task_id == task_id,
                TaskChannelBinding.channel_id == body.channel_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, "该通道已绑定")
        binding = TaskChannelBinding(task_id=task_id, **body.model_dump())
        session.add(binding)
        await session.commit()
        await session.refresh(binding)
        return binding


@router.delete("/tasks/{task_id}/channels/{channel_id}")
async def unbind_channel(task_id: int, channel_id: int, user: str = Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(
            select(TaskChannelBinding).where(
                TaskChannelBinding.task_id == task_id,
                TaskChannelBinding.channel_id == channel_id,
            )
        )
        binding = result.scalar_one_or_none()
        if not binding:
            raise HTTPException(404)
        await session.delete(binding)
        await session.commit()
        return {"ok": True}


@router.get("/delivery-log", response_model=list[DeliveryLogOut])
async def query_delivery_log(
    task_id: int = Query(None),
    channel_id: int = Query(None),
    status: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    limit: int = Query(50, le=200),
    user: str = Depends(require_auth),
):
    async with async_session() as session:
        q = select(DeliveryLog).order_by(DeliveryLog.created_at.desc())
        if task_id:
            q = q.where(DeliveryLog.task_id == task_id)
        if channel_id:
            q = q.where(DeliveryLog.channel_id == channel_id)
        if status:
            q = q.where(DeliveryLog.status == status)
        if date_from:
            q = q.where(DeliveryLog.created_at >= date_from)
        if date_to:
            q = q.where(DeliveryLog.created_at <= date_to)
        q = q.limit(limit)
        result = await session.execute(q)
        return result.scalars().all()

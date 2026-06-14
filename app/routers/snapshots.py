import os
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy import select

from app.auth import require_auth
from app.config import settings
from app.database import async_session
from app.models import Snapshot
from app.schemas import SnapshotOut
from app.differ import compute_diff
from app.fetcher import normalize_and_extract, fetch_static, fetch_js

router = APIRouter(tags=["snapshots"])


@router.get("/tasks/{task_id}/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(task_id: int, limit: int = Query(20, le=100), user: str = Depends(require_auth)):
    async with async_session() as session:
        result = await session.execute(
            select(Snapshot).where(Snapshot.task_id == task_id).order_by(Snapshot.created_at.desc()).limit(limit)
        )
        return result.scalars().all()


@router.get("/snapshots/{snapshot_id}/diff")
async def get_diff(snapshot_id: int, compare_to: int = Query(None), user: str = Depends(require_auth)):
    async with async_session() as session:
        snapshot = await session.get(Snapshot, snapshot_id)
        if not snapshot:
            raise HTTPException(404)

        if compare_to:
            prev_snapshot = await session.get(Snapshot, compare_to)
        else:
            prev = await session.execute(
                select(Snapshot)
                .where(Snapshot.task_id == snapshot.task_id, Snapshot.id < snapshot.id)
                .order_by(Snapshot.id.desc())
                .limit(1)
            )
            prev_snapshot = prev.scalar_one_or_none()

        old_text = prev_snapshot.extracted_text if prev_snapshot else ""
        diff = compute_diff(old_text, snapshot.extracted_text)

        old_screenshot = ""
        new_screenshot = ""
        if prev_snapshot and prev_snapshot.screenshot_path:
            fname = os.path.basename(prev_snapshot.screenshot_path)
            old_screenshot = f"/screenshots/{fname}"
        if snapshot.screenshot_path:
            fname = os.path.basename(snapshot.screenshot_path)
            new_screenshot = f"/screenshots/{fname}"

        return {
            "old_snapshot_id": prev_snapshot.id if prev_snapshot else None,
            "new_snapshot_id": snapshot.id,
            "old_screenshot": old_screenshot,
            "new_screenshot": new_screenshot,
            "old_resource_meta": prev_snapshot.resource_meta if prev_snapshot else None,
            "new_resource_meta": snapshot.resource_meta,
            **diff,
        }


@router.post("/preview")
async def preview_content(body: dict, user: str = Depends(require_auth)):
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
            html, _ = await fetch_js(url, headers, cookies, proxy, 15)
        else:
            html, _ = await fetch_static(url, headers, cookies, proxy, 15)
        _, text = normalize_and_extract(html, include_selector, exclude_selector)
        return {"text": text[:5000]}
    except Exception as e:
        raise HTTPException(400, str(e))

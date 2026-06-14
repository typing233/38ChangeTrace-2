from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TaskCreate(BaseModel):
    name: str
    url: str
    interval_seconds: int = 300
    headers: dict = {}
    cookies: dict = {}
    proxy: str = ""
    render_mode: str = "static"
    include_selector: str = ""
    exclude_selector: str = ""
    max_retries: int = 3
    timeout_seconds: int = 30


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    interval_seconds: Optional[int] = None
    headers: Optional[dict] = None
    cookies: Optional[dict] = None
    proxy: Optional[str] = None
    render_mode: Optional[str] = None
    include_selector: Optional[str] = None
    exclude_selector: Optional[str] = None
    status: Optional[str] = None
    max_retries: Optional[int] = None
    timeout_seconds: Optional[int] = None


class TaskOut(BaseModel):
    id: int
    name: str
    url: str
    interval_seconds: int
    headers: dict
    cookies: dict
    proxy: str
    render_mode: str
    include_selector: str
    exclude_selector: str
    status: str
    last_error: str
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    last_hash: str
    retry_count: int
    max_retries: int
    timeout_seconds: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SnapshotOut(BaseModel):
    id: int
    task_id: int
    content_hash: str
    extracted_text: str
    resource_meta: dict
    screenshot_path: str
    created_at: datetime

    class Config:
        from_attributes = True


class DiffResult(BaseModel):
    old_snapshot_id: Optional[int]
    new_snapshot_id: int
    added_lines: list[str]
    removed_lines: list[str]
    unified_diff: str

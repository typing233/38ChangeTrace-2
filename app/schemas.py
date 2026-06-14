from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime
import re


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

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v):
        if not v.strip():
            raise ValueError("任务名称不能为空")
        return v.strip()

    @field_validator("url")
    @classmethod
    def url_valid(cls, v):
        if not re.match(r"https?://\S+", v):
            raise ValueError("请输入有效的URL（以http://或https://开头）")
        return v

    @field_validator("interval_seconds")
    @classmethod
    def interval_range(cls, v):
        if v < 10:
            raise ValueError("抓取间隔不能小于10秒")
        return v

    @field_validator("render_mode")
    @classmethod
    def render_mode_valid(cls, v):
        if v not in ("static", "js"):
            raise ValueError("渲染方式必须是 static 或 js")
        return v


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
    version: Optional[int] = None


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
    version: int = 1
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
    structured_diff: list[dict] = []
    stats: dict = {}


# --- Notification Channels ---

class ChannelCreate(BaseModel):
    name: str
    channel_type: str
    config: dict = {}
    rate_limit_per_minute: int = 30
    enabled: bool = True

    @field_validator("channel_type")
    @classmethod
    def channel_type_valid(cls, v):
        allowed = ("webhook", "email", "telegram", "dingtalk", "slack")
        if v not in allowed:
            raise ValueError(f"通道类型必须是: {', '.join(allowed)}")
        return v


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    rate_limit_per_minute: Optional[int] = None
    enabled: Optional[bool] = None


class ChannelOut(BaseModel):
    id: int
    name: str
    channel_type: str
    config: dict
    rate_limit_per_minute: int
    enabled: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BindingCreate(BaseModel):
    channel_id: int
    template: str = ""
    enabled: bool = True


class BindingOut(BaseModel):
    id: int
    task_id: int
    channel_id: int
    template: str
    enabled: bool

    class Config:
        from_attributes = True


class DeliveryLogOut(BaseModel):
    id: int
    task_id: int
    channel_id: int
    event_id: Optional[int]
    status: str
    attempt: int
    response_status: int
    latency_ms: int
    error_message: str
    created_at: datetime

    class Config:
        from_attributes = True


# --- Monitoring Rules ---

class RuleCreate(BaseModel):
    rule_type: str
    config: dict = {}
    logic_group: str = "AND"
    enabled: bool = True

    @field_validator("rule_type")
    @classmethod
    def rule_type_valid(cls, v):
        allowed = ("xpath", "keyword_include", "keyword_exclude", "regex")
        if v not in allowed:
            raise ValueError(f"规则类型必须是: {', '.join(allowed)}")
        return v

    @field_validator("logic_group")
    @classmethod
    def logic_group_valid(cls, v):
        if v.upper() not in ("AND", "OR"):
            raise ValueError("逻辑组必须是 AND 或 OR")
        return v.upper()


class RuleUpdate(BaseModel):
    rule_type: Optional[str] = None
    config: Optional[dict] = None
    logic_group: Optional[str] = None
    enabled: Optional[bool] = None


class RuleOut(BaseModel):
    id: int
    task_id: int
    rule_type: str
    config: dict
    logic_group: str
    enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True

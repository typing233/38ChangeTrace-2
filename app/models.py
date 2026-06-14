import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, JSON, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    url = Column(Text, nullable=False)
    interval_seconds = Column(Integer, nullable=False, default=300)
    headers = Column(JSON, default=dict)
    cookies = Column(JSON, default=dict)
    proxy = Column(String(512), default="")
    render_mode = Column(String(32), default="static")
    include_selector = Column(Text, default="")
    exclude_selector = Column(Text, default="")
    status = Column(String(32), default="active")
    last_error = Column(Text, default="")
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_hash = Column(String(64), default="")
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    timeout_seconds = Column(Integer, default=30)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    snapshots = relationship("Snapshot", back_populates="task", order_by="desc(Snapshot.created_at)")
    channel_bindings = relationship("TaskChannelBinding", back_populates="task", cascade="all, delete-orphan")
    rules = relationship("MonitoringRule", back_populates="task", cascade="all, delete-orphan")


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (Index("idx_task_created", "task_id", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    content_hash = Column(String(64), nullable=False)
    raw_html = Column(Text, default="")
    extracted_text = Column(Text, default="")
    resource_meta = Column(JSON, default=dict)
    screenshot_path = Column(String(512), default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    task = relationship("Task", back_populates="snapshots")


class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSON, default=dict)
    delivered = Column(Boolean, default=False)
    notified = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    channel_type = Column(String(32), nullable=False)
    config = Column(JSON, default=dict)
    rate_limit_per_minute = Column(Integer, default=30)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    bindings = relationship("TaskChannelBinding", back_populates="channel", cascade="all, delete-orphan")


class TaskChannelBinding(Base):
    __tablename__ = "task_channel_bindings"
    __table_args__ = (UniqueConstraint("task_id", "channel_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(Integer, ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False)
    template = Column(Text, default="")
    enabled = Column(Boolean, default=True)

    task = relationship("Task", back_populates="channel_bindings")
    channel = relationship("NotificationChannel", back_populates="bindings")


class DeliveryLog(Base):
    __tablename__ = "delivery_log"
    __table_args__ = (
        Index("idx_delivery_log_event", "event_id"),
        Index("idx_delivery_log_channel", "channel_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(Integer, ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(Integer, ForeignKey("event_log.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    attempt = Column(Integer, default=1)
    request_payload = Column(Text, default="")
    response_status = Column(Integer, default=0)
    response_body = Column(Text, default="")
    latency_ms = Column(Integer, default=0)
    error_message = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class MonitoringRule(Base):
    __tablename__ = "monitoring_rules"
    __table_args__ = (Index("idx_monitoring_rules_task", "task_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    rule_type = Column(String(32), nullable=False)
    config = Column(JSON, default=dict)
    logic_group = Column(String(8), default="AND")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    task = relationship("Task", back_populates="rules")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("idx_audit_log_created", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user = Column(String(64), default="system")
    action = Column(String(64), nullable=False)
    resource_type = Column(String(32), default="")
    resource_id = Column(Integer, default=0)
    old_value = Column(JSON, nullable=True)
    new_value = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

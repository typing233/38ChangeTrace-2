import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, JSON, ForeignKey, Index
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
    render_mode = Column(String(32), default="static")  # static | js
    include_selector = Column(Text, default="")
    exclude_selector = Column(Text, default="")
    status = Column(String(32), default="active")  # active | paused | error
    last_error = Column(Text, default="")
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_hash = Column(String(64), default="")
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    timeout_seconds = Column(Integer, default=30)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    snapshots = relationship("Snapshot", back_populates="task", order_by="desc(Snapshot.created_at)")


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
    event_type = Column(String(64), nullable=False)  # change_detected | error | manual_trigger
    payload = Column(JSON, default=dict)
    delivered = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

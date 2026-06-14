import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.database import init_db
from app.notifier import event_queue
from app.scheduler import scheduler, restore_jobs
from app.routers import (
    tasks_router, snapshots_router, notifications_router,
    events_router, admin_router, auth_router,
)

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await event_queue.start()
    scheduler.start()
    await restore_jobs()
    yield
    scheduler.shutdown(wait=False)
    await event_queue.stop()


app = FastAPI(title="ChangeTrace", version="2.0.0", lifespan=lifespan)

app.include_router(tasks_router, prefix="/api")
app.include_router(snapshots_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(auth_router, prefix="/api")

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")


@app.get("/screenshots/{filename}")
async def serve_screenshot(filename: str):
    from fastapi import HTTPException
    path = os.path.join(settings.SCREENSHOTS_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "Screenshot not found")
    return FileResponse(path, media_type="image/png")

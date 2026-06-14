from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.auth import verify_login, create_token
from app.config import settings

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
async def login(body: LoginRequest):
    if not settings.AUTH_ENABLED:
        return create_token("anonymous")
    if not verify_login(body.username, body.password):
        raise HTTPException(401, "用户名或密码错误")
    return create_token(body.username)


@router.get("/auth/status")
async def auth_status():
    return {"auth_enabled": settings.AUTH_ENABLED}

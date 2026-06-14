import datetime
import logging
from typing import Optional

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError

from app.config import settings

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"


def create_token(username: str) -> dict:
    expire = datetime.datetime.utcnow() + datetime.timedelta(hours=settings.AUTH_TOKEN_EXPIRE_HOURS)
    payload = {"sub": username, "exp": expire}
    token = jwt.encode(payload, settings.AUTH_SECRET_KEY, algorithm=ALGORITHM)
    return {"token": token, "expires_at": expire.isoformat()}


def verify_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.AUTH_SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


async def require_auth(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not settings.AUTH_ENABLED:
        return "anonymous"
    if not credentials:
        raise HTTPException(status_code=401, detail="未登录")
    username = verify_token(credentials.credentials)
    if not username:
        raise HTTPException(status_code=401, detail="Token无效或已过期")
    return username


def verify_login(username: str, password: str) -> bool:
    return username == settings.AUTH_USERNAME and password == settings.AUTH_PASSWORD

from __future__ import annotations

import os

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer()


def require_token(credentials: HTTPAuthorizationCredentials = Security(_bearer)) -> None:
    expected = os.getenv("API_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=500, detail="API_TOKEN not configured")
    if credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Invalid token")

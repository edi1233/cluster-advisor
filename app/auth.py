import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

PORTAL_USER = os.environ.get("PORTAL_USER", "admin")
PORTAL_PASSWORD = os.environ.get("PORTAL_PASSWORD", "")


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not PORTAL_PASSWORD:
        return  # no password configured -> auth disabled
    ok_user = secrets.compare_digest(credentials.username, PORTAL_USER)
    ok_pass = secrets.compare_digest(credentials.password, PORTAL_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

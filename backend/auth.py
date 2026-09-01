"""
auth.py
=======
JWT-based authentication for the Admin Portal and Customer Portal.

Two independent principal types share one token format, distinguished by the
"role" claim ("admin" | "customer"). Passwords are hashed with bcrypt
directly (not passlib, which has a known noisy-warning incompatibility with
bcrypt >= 4.1).

get_optional_customer_id() is the key dependency that keeps guest checkout
completely unaffected: it never raises, it just returns None when there's no
(or an invalid) customer token, so existing unauthenticated booking flows are
untouched.
"""

import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config.auth_config import AuthConfig

_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_access_token(data: dict) -> str:
    payload = dict(data)
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=AuthConfig.JWT_EXPIRE_MINUTES)
    return jwt.encode(payload, AuthConfig.JWT_SECRET_KEY, algorithm=AuthConfig.JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, AuthConfig.JWT_SECRET_KEY, algorithms=[AuthConfig.JWT_ALGORITHM])
    except Exception:
        return None


def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing admin credentials")
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return payload


def get_current_customer(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing customer credentials")
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    if payload.get("role") != "customer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer access required")
    return payload


def get_optional_customer_id(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> int | None:
    """Never raises. Returns the customer_id if a valid customer token was
    presented, otherwise None (missing header, garbage token, admin token,
    expired token — all just fall through to guest behavior)."""
    if credentials is None:
        return None
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("role") != "customer":
        return None
    return payload.get("customer_id")


def get_admin_or_customer(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> dict:
    """Accepts EITHER a valid admin or customer token — used by endpoints
    (like invoice lookup) that both portals share but that must never be
    reachable without signing in to one of them. Returns
    {"role": "admin"|"customer", "customer_id": int|None, "admin_id": int|None}.
    Raises 401 if no valid token of either kind is presented."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign-in required")
    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("role") not in ("admin", "customer"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return {
        "role": payload.get("role"),
        "customer_id": payload.get("customer_id"),
        "admin_id": payload.get("admin_id"),
    }

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


# Staff roles (16/11/2026, admin site split): their tokens are signed with
# ADMIN_JWT_SECRET_KEY and only ever accepted by the admin site's backend
# (APP_MODE=admin). Customer tokens use JWT_SECRET_KEY.
STAFF_ROLES = ("admin", "analytics")
IS_ADMIN_SITE = AuthConfig.APP_MODE == "admin"


def create_access_token(data: dict) -> str:
    payload = dict(data)
    staff = payload.get("role") in STAFF_ROLES
    if staff and not IS_ADMIN_SITE:
        raise RuntimeError("Staff sign-ins are only issued by the admin site (APP_MODE=admin)")
    minutes = AuthConfig.ADMIN_JWT_EXPIRE_MINUTES if staff else AuthConfig.JWT_EXPIRE_MINUTES
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    key = AuthConfig.ADMIN_JWT_SECRET_KEY if staff else AuthConfig.JWT_SECRET_KEY
    return jwt.encode(payload, key, algorithm=AuthConfig.JWT_ALGORITHM)


def decode_staff_token(token: str) -> dict | None:
    """A staff (admin / analytics) token — admin site only; None elsewhere."""
    if not IS_ADMIN_SITE or not AuthConfig.ADMIN_JWT_SECRET_KEY:
        return None
    try:
        payload = jwt.decode(token, AuthConfig.ADMIN_JWT_SECRET_KEY, algorithms=[AuthConfig.JWT_ALGORITHM])
    except Exception:
        return None
    return payload if payload.get("role") in STAFF_ROLES else None


# 16/11/2026 (security): an admin token is only honoured while that staff
# account still exists in the admins table, so deleting/renaming an account
# ends its sessions. Checked at most once a minute per account.
_admin_seen: dict[tuple, float] = {}


def _admin_still_exists(payload: dict) -> bool:
    import time
    import database  # local import: database does not import auth
    key = (payload.get("admin_id"), payload.get("sub"))
    now = time.monotonic()
    if now - _admin_seen.get(key, -1e9) < 60:
        return True
    try:
        row = database.get_admin_by_username(payload.get("sub") or "")
    except Exception:
        return True  # database briefly unreachable: don't lock staff out
    if not row or row.get("id") != payload.get("admin_id"):
        _admin_seen.pop(key, None)
        return False
    _admin_seen[key] = now
    return True


def create_manage_booking_token(locator_code: str, customer_id: int | None) -> str:
    """Issued by Booking Status after the PNR/ticket and last name matched a
    traveler on Travelport; required by every Manage My Booking call. 30 min."""
    return jwt.encode({"role": "manage_booking", "locator": locator_code, "customer_id": customer_id,
                       "exp": datetime.now(timezone.utc) + timedelta(minutes=30)},
                      AuthConfig.JWT_SECRET_KEY, algorithm=AuthConfig.JWT_ALGORITHM)


def decode_manage_booking_token(token: str) -> dict | None:
    payload = decode_token(token or "")
    return payload if payload and payload.get("role") == "manage_booking" and payload.get("locator") else None


def decode_token(token: str) -> dict | None:
    """A customer-side token (customer, manage_booking). Never a staff one."""
    try:
        payload = jwt.decode(token, AuthConfig.JWT_SECRET_KEY, algorithms=[AuthConfig.JWT_ALGORITHM])
    except Exception:
        return None
    return None if payload.get("role") in STAFF_ROLES else payload


def _staff_or_customer(token: str) -> dict | None:
    return decode_staff_token(token) or decode_token(token)


def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing admin credentials")
    payload = decode_staff_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    if not _admin_still_exists(payload):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This staff account no longer exists")
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


def get_optional_principal(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> dict | None:
    """Never raises. {"role", "customer_id", "admin_id"} for a valid admin or
    customer token, otherwise None — for public endpoints that show more
    detail to staff or to the booking's own customer."""
    if credentials is None:
        return None
    payload = _staff_or_customer(credentials.credentials)
    if payload is None or payload.get("role") not in ("admin", "customer"):
        return None
    if payload.get("role") == "admin" and not _admin_still_exists(payload):
        return None
    return {"role": payload.get("role"), "customer_id": payload.get("customer_id"), "admin_id": payload.get("admin_id")}


def get_admin_or_customer(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> dict:
    """Accepts EITHER a valid admin or customer token — used by endpoints
    (like invoice lookup) that both portals share but that must never be
    reachable without signing in to one of them. Returns
    {"role": "admin"|"customer", "customer_id": int|None, "admin_id": int|None}.
    Raises 401 if no valid token of either kind is presented."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign-in required")
    payload = _staff_or_customer(credentials.credentials)
    if payload is None or payload.get("role") not in ("admin", "customer"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    if payload.get("role") == "admin" and not _admin_still_exists(payload):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This staff account no longer exists")
    return {
        "role": payload.get("role"),
        "customer_id": payload.get("customer_id"),
        "admin_id": payload.get("admin_id"),
    }

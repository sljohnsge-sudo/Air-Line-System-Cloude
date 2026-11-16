"""
Simple in-memory rate limits (16/11/2026, security review): stops password
guessing on admin / customer sign-in, PNR + surname guessing on the booking
lookups, and scripts holding seats with dummy bookings.

Per process: with several API workers each keeps its own count, which still
caps an attacker. Counts reset when the backend restarts.
"""
import threading
import time
from collections import deque

from fastapi import HTTPException

_lock = threading.Lock()
_hits: dict[tuple[str, str], deque] = {}
_MAX_KEYS = 50_000


def _recent(bucket: str, key: str, window: float) -> deque:
    q = _hits.setdefault((bucket, key), deque())
    cutoff = time.monotonic() - window
    while q and q[0] < cutoff:
        q.popleft()
    return q


def check(bucket: str, key: str | None, limit: int, window_seconds: float, message: str) -> None:
    """Raise 429 if `key` already has `limit` hits in the window (does not count)."""
    if not key:
        return
    with _lock:
        if len(_recent(bucket, key, window_seconds)) >= limit:
            raise HTTPException(status_code=429, detail=message)


def hit(bucket: str, key: str | None, limit: int, window_seconds: float, message: str) -> None:
    """Count one attempt for `key`; raise 429 once it is over `limit` in the window."""
    if not key:
        return
    with _lock:
        if len(_hits) > _MAX_KEYS:
            _hits.clear()  # never let the table grow without bound
        q = _recent(bucket, key, window_seconds)
        if len(q) >= limit:
            raise HTTPException(status_code=429, detail=message)
        q.append(time.monotonic())


def clear(bucket: str, key: str | None) -> None:
    if key:
        with _lock:
            _hits.pop((bucket, key), None)


TOO_MANY_SIGN_INS = "Too many sign-in attempts. Please wait 15 minutes and try again."
TOO_MANY_LOOKUPS = "Too many booking look-ups. Please wait a few minutes and try again."
TOO_MANY_REQUESTS = "Too many requests from your connection. Please wait a little and try again."

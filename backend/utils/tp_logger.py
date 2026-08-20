"""
utils/tp_logger.py
===================
Optional Travelport request/response file logger.

When the TP_LOG_DIR environment variable is set, every outbound HTTP call to
Travelport (made through any httpx.Client that has HOOKS attached — see
auth_service.py, search_service.py, workbench_service.py, ticket_service.py,
invoice_service.py) is written to that directory as a numbered pair of files,
in the same layout as Travelport's own reference booking-flow examples:

    {n}.{step}_RQ.txt   -- curl-style request (method, headers, body)
    {n}.{step}_RS.json  -- pretty-printed JSON response body

Zero overhead and writes nothing when TP_LOG_DIR is unset (the default) —
safe to leave attached permanently.

Bearer tokens and session cookies are redacted before being written to disk —
but as a stable per-token FINGERPRINT rather than a fixed placeholder, so
multiple bookings that reused the same cached OAuth token (Travelport issues
one token valid 24h) show the *same* fingerprint across files/folders,
proving reuse, while a fresh token shows a visibly different one. The
fingerprint is the token's own "jti" (JWT ID) claim when present — an
identifier Travelport's own system embedded in the token — falling back to
a short SHA-256 hash of the raw token otherwise. Neither reveals the
original secret.
"""

import os
import re
import json
import base64
import hashlib
import threading
import httpx

_lock = threading.Lock()
_counter = 0
_pending: dict[int, tuple[int, str]] = {}

_REDACT_HEADER_VALUES = {"cookie": "{{session_cookie}}"}
_REDACT_BODY_KEYS = {"refresh_token", "password", "client_secret"}
_SKIP_HEADERS = {"host", "content-length", "accept-encoding", "connection", "user-agent"}


def _jwt_claims(token: str) -> dict | None:
    """Best-effort decode of a JWT's payload segment — no signature
    verification, just reading claims Travelport already put in the token."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return None


def _token_fingerprint(token: str) -> str:
    """Stable identifier for a token: its own jti claim if present, else a
    short hash of the raw value. Same token -> same fingerprint, always."""
    claims = _jwt_claims(token)
    if claims and claims.get("jti"):
        return str(claims["jti"])
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _redact_bearer(value: str) -> str:
    if not value.lower().startswith("bearer "):
        return value
    token = value[7:].strip()
    if not token:
        return value
    return f"Bearer {{{{access_token:{_token_fingerprint(token)}}}}}"


def _log_dir() -> str | None:
    d = os.environ.get("TP_LOG_DIR")
    return d or None


def _step_name(method: str, url: str) -> str:
    """Derive a Travelport-flow step name from the request method + path."""
    path = url.split("?")[0]
    m = method.upper()

    if "oauth/token" in path:
        return "token"
    if path.endswith("/catalogproductofferings"):
        return "search"
    if path.endswith("/catalogproductofferings/buildnext"):
        return "search_nextleg"
    if path.endswith("/buildoptions"):
        return "price_upsell"
    if path.endswith("/buildpremiumflexoptions"):
        return "price_premiumflex_upsell"
    if path.endswith("/premiumflex"):
        return "price_premiumflex"
    if "airavailability" in path.lower():
        return "availability"
    if path.endswith("/travelers/list"):
        return "add_travelers_batch"
    if re.search(r"/travelers$", path):
        return "add_traveler"
    if path.endswith("/buildfromlocator"):
        return "session_postcommit"
    if re.search(r"/reservationworkbench$", path) and m == "POST":
        return "session"
    if re.search(r"/reservationworkbench/[^/]+$", path) and m == "GET":
        return "session_get"
    if re.search(r"/reservationworkbench/[^/]+$", path) and m == "DELETE":
        return "session_discard"
    if path.endswith("/offers/buildfromproducts") or path.endswith("/offers/buildfromcatalogproductofferings"):
        return "add_offer"
    if path.endswith("/formofpayment"):
        return "formofpayment"
    if "/paymentoffer/" in path and path.endswith("/payments"):
        return "payment"
    if path.endswith("/cancelitems"):
        return "cancel_items"
    if re.search(r"/reservations/[^/]+$", path) and m == "POST":
        return "issue" if "Issuance=Ticket" in url else "reservation_commit"
    if re.search(r"/reservations/[^/]+$", path) and m == "GET":
        return "retrieve"
    if "seatavailabilities" in path.lower():
        return "seatmap"

    fallback = path.strip("/").split("/")[-1]
    return re.sub(r"[^a-zA-Z0-9]+", "_", fallback) or "request"


def _redact_body(data):
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k == "access_token" and isinstance(v, str) and v:
                out[k] = f"{{{{access_token:{_token_fingerprint(v)}}}}}"
            elif k in _REDACT_BODY_KEYS:
                out[k] = "{{redacted}}"
            else:
                out[k] = _redact_body(v)
        return out
    if isinstance(data, list):
        return [_redact_body(v) for v in data]
    return data


def _to_curl(request: httpx.Request) -> str:
    lines = [f"curl --location --globoff '{request.url}' \\"]
    for k, v in request.headers.items():
        if k.lower() in _SKIP_HEADERS:
            continue
        if k.lower() == "authorization":
            v = _redact_bearer(v)
        else:
            v = _REDACT_HEADER_VALUES.get(k.lower(), v)
        lines.append(f"--header '{k}: {v}' \\")

    body = request.content
    if body:
        try:
            parsed = _redact_body(json.loads(body))
            lines.append(f"--data-raw '{json.dumps(parsed, indent=4)}'")
        except Exception:
            lines.append(f"--data-raw '{body.decode('utf-8', errors='replace')}'")
    else:
        lines[-1] = lines[-1].rstrip(" \\")

    return "\n".join(lines)


def _on_request(request: httpx.Request) -> None:
    log_dir = _log_dir()
    if not log_dir:
        return
    global _counter
    with _lock:
        _counter += 1
        n = _counter
    name = _step_name(request.method, str(request.url))
    _pending[id(request)] = (n, name)

    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, f"{n}.{name}_RQ.txt"), "w", encoding="utf-8") as f:
        f.write(_to_curl(request))


def _on_response(response: httpx.Response) -> None:
    log_dir = _log_dir()
    if not log_dir:
        return
    n, name = _pending.pop(id(response.request), (None, None))
    if n is None:
        return

    response.read()
    try:
        text = json.dumps(_redact_body(response.json()), indent=4)
    except Exception:
        text = response.text

    prefix = "" if response.status_code < 400 else f"HTTP {response.status_code}\n"
    with open(os.path.join(log_dir, f"{n}.{name}_RS.json"), "w", encoding="utf-8") as f:
        f.write(prefix + text)


HOOKS = {"request": [_on_request], "response": [_on_response]}


def reset(start_at: int = 0) -> None:
    """Reset the step counter — call before starting a fresh logging session."""
    global _counter
    with _lock:
        _counter = start_at
    _pending.clear()

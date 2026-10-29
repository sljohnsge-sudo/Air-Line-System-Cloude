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

Masked by default before anything is written to disk: session cookies,
passwords/client secrets, Bearer tokens and access_token values (last 6
characters kept, enough to show the same 24h token was reused across calls),
client ids, the access group, PayCorp AUTHTOKEN/HMAC headers, card numbers and
CVVs, and passengers' passport numbers and birth dates. These logs used to be
committed to a public repository with all of that in clear text.

Set TP_LOG_UNREDACTED=true only for a one-off log capture Travelport itself
asks for (e.g. certification review, to inspect the real token). Passwords and
client secrets stay redacted even then. Never commit the log directory.
"""

import os
import re
import json
import threading
from urllib.parse import parse_qsl, quote
import httpx

_lock = threading.Lock()
_counter = 0
_pending: dict[int, tuple[int, str]] = {}

_REDACT_HEADER_VALUES = {"cookie": "{{session_cookie}}"}
_REDACT_BODY_KEYS = {"refresh_token", "password", "client_secret"}
_SKIP_HEADERS = {"host", "content-length", "accept-encoding", "connection", "user-agent"}

# Masked unless TP_LOG_UNREDACTED is set. Values keep their last few
# characters where that's useful for matching calls up (tokens, ids).
_MASK_HEADERS = {"authorization", "xauth_travelport_accessgroup", "authtoken", "hmac"}
_MASK_BODY_KEYS = {
    "access_token", "client_id", "clientId",           # credentials
    "docNumber", "birthDate",                           # passport number, date of birth
    "CardNumber", "SeriesCode", "cvv", "CVV",           # payment card number, CVV
}


def _log_dir() -> str | None:
    d = os.environ.get("TP_LOG_DIR")
    return d or None


def _unredacted() -> bool:
    return os.environ.get("TP_LOG_UNREDACTED", "").strip().lower() in ("1", "true", "yes")


def _mask(value) -> str:
    """'{{masked …ab12cd}}' — keeps only the last 6 characters of a string."""
    if isinstance(value, str) and len(value) > 10:
        return "{{masked …" + value[-6:] + "}}"
    return "{{masked}}"


def _mask_header(name: str, value: str) -> str:
    if _unredacted() or name.lower() not in _MASK_HEADERS:
        return value
    if name.lower() == "authorization" and value.lower().startswith("bearer "):
        return "Bearer " + _mask(value[7:])
    return _mask(value)


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
    # AirPrice (STEP 3b, GDS-only — see workbench_service.confirm_price) posts
    # to /air/price/offers/buildfromcatalogproductofferings, which shares its
    # trailing segment with the real workbench-scoped Add Offer call below
    # ("/offers/buildfromcatalogproductofferings"). Must be checked first, or
    # every AirPrice call gets mislabeled "add_offer" and is indistinguishable
    # from the actual Add Offer step in the logs.
    if "/price/offers/" in path:
        return "airprice"
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
    if path.endswith("/documents/void"):
        return "batch_void"
    if "/ticket/tickets/updatestatus/" in path:
        return "ticket_void"
    if path.endswith("/offers/canceloffer"):
        return "refund_quote"
    if re.search(r"/receipt/reservations/[^/]+/receipts$", path) and m == "POST":
        return "reservation_cancel"
    if re.search(r"/reservations/[^/]+$", path) and m == "POST":
        return "issue" if "Issuance=Ticket" in url else "reservation_commit"
    if re.search(r"/reservations/[^/]+$", path) and m == "GET":
        return "retrieve"
    if "seatavailabilities" in path.lower():
        return "seatmap"
    if re.search(r"/ticket/tickets/\d+$", path):
        return "retrieve_ticket"

    fallback = path.strip("/").split("/")[-1]
    return re.sub(r"[^a-zA-Z0-9]+", "_", fallback) or "request"


def _redact_body(data):
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in _REDACT_BODY_KEYS:
                out[k] = "{{redacted}}"
            elif k in _MASK_BODY_KEYS and not _unredacted():
                out[k] = _mask(v)
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
        v = _REDACT_HEADER_VALUES.get(k.lower(), _mask_header(k, v))
        lines.append(f"--header '{k}: {v}' \\")

    body = request.content
    if body:
        try:
            parsed = _redact_body(json.loads(body))
            lines.append(f"--data-raw '{json.dumps(parsed, indent=4)}'")
        except Exception:
            text = body.decode('utf-8', errors='replace')
            if "application/x-www-form-urlencoded" in request.headers.get("content-type", ""):
                pairs = parse_qsl(text, keep_blank_values=True)
                def _form_value(k, v):
                    if k in _REDACT_BODY_KEYS:
                        return "{{redacted}}"
                    if k in _MASK_BODY_KEYS and not _unredacted():
                        return _mask(v)
                    return quote(v, safe='')
                text = "&".join(f"{k}={_form_value(k, v)}" for k, v in pairs)
            lines.append(f"--data-raw '{text}'")
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

"""
services/cybersource_service.py
===============================
NDB Bank card payments — CyberSource Secure Acceptance Hosted Checkout
(see "Secure Acceptance Hosted Checkout Integration", Cybersource).

Same two calls as the previous gateway (paycorp_service), so the booking,
add-on and hotel flows — and every check in main._verify_and_claim_payment —
stay as they are:

  init_payment(...)     -> {"reqid", "payment_page_url", "expire_at"}
      Records the payment and returns a page on our own backend
      (/api/payments/cybersource/redirect/{reqid}) that POSTs the signed
      fields to the bank's hosted page (Hosted Checkout takes a form POST,
      not a link).
  handle_receipt(form)  -> where to send the browser next
      The bank POSTs the result to our receipt page. Its HMAC-SHA256
      signature (secret key) is checked before anything is stored; a result
      that doesn't verify is ignored. The browser then goes to the site's
      return URL with ?reqid=..., exactly like before.
  complete_payment(reqid) -> the stored, verified result
      success only for decision ACCEPT with reason code 100/110. REVIEW
      (authorization declined) is NOT a success — no ticket is issued on it.

reqid is our transaction_uuid (unique per payment attempt).
"""
import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

import database
from config.cybersource_config import CyberSourceConfig
from services.paycorp_service import PayCorpError

logger = logging.getLogger(__name__)


class CyberSourceError(PayCorpError):
    """A payment-gateway error (subclass of the existing gateway error, so
    every caller's error handling works unchanged)."""


_SUCCESS_REASON_CODES = {"100", "110"}


def _ensure_table() -> None:
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cybersource_payments (
                reqid          VARCHAR(40) PRIMARY KEY,
                client_ref     VARCHAR(60) NULL,
                amount         DECIMAL(14,2) NOT NULL,
                currency       VARCHAR(5) NOT NULL,
                return_url     VARCHAR(500) NOT NULL,
                cancel_url     VARCHAR(500) NULL,
                api_base       VARCHAR(300) NOT NULL,
                comment        VARCHAR(255) NULL,
                status         VARCHAR(20) NOT NULL DEFAULT 'started',
                response_json  LONGTEXT NULL,
                created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at   DATETIME NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()


_table_ready = False


def _db(sql: str, args: tuple = (), fetch: bool = False):
    global _table_ready
    if not _table_ready:
        _ensure_table()
        _table_ready = True
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, args)
        if fetch:
            return cur.fetchone()
        conn.commit()
        return None
    finally:
        cur.close()
        conn.close()


def sign(fields: dict, signed_names: list[str]) -> str:
    """HMAC-SHA256 over "name=value,name=value…" in signed_field_names order,
    Base64 — the Secure Acceptance signature (request and response)."""
    data = ",".join(f"{n}={fields.get(n, '')}" for n in signed_names)
    digest = hmac.new(CyberSourceConfig.SECRET_KEY.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _api_base_for(return_url: str) -> str:
    if CyberSourceConfig.PUBLIC_API_BASE:
        return CyberSourceConfig.PUBLIC_API_BASE
    parts = urlsplit(return_url)
    return f"{parts.scheme}://{parts.netloc}/api"


def _with_query(url: str, extra: dict) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(extra)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def init_payment(amount: float, currency: str, return_url: str, cancel_url: str | None = None,
                 client_ref: str | None = None, comment: str | None = None) -> dict:
    if not CyberSourceConfig.configured():
        raise CyberSourceError("The NDB payment gateway is not configured (CYBERSOURCE_PROFILE_ID / ACCESS_KEY / SECRET_KEY).")
    reqid = uuid.uuid4().hex
    api_base = _api_base_for(return_url)
    _db("""INSERT INTO cybersource_payments (reqid, client_ref, amount, currency, return_url, cancel_url, api_base, comment)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (reqid, client_ref, round(float(amount), 2), (currency or "LKR").upper(), return_url, cancel_url, api_base,
         (comment or "")[:255] or None))
    logger.info(f"NDB/CyberSource payment started: {currency} {amount} ref={client_ref} reqid={reqid}")
    return {"reqid": reqid, "expire_at": None, "payment_page_url": f"{api_base}/payments/cybersource/redirect/{reqid}"}


def checkout_form(reqid: str) -> str:
    """The auto-submitting page that sends the customer to NDB's hosted
    payment page with the signed request."""
    row = _db("SELECT * FROM cybersource_payments WHERE reqid=%s", (reqid,), fetch=True)
    if not row:
        raise CyberSourceError("Payment not found.")
    if row["status"] != "started":
        raise CyberSourceError("This payment has already been completed or cancelled.")
    receipt = f"{row['api_base']}/payments/cybersource/receipt"
    fields = {
        "access_key": CyberSourceConfig.ACCESS_KEY,
        "profile_id": CyberSourceConfig.PROFILE_ID,
        "transaction_uuid": reqid,
        "signed_date_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "locale": CyberSourceConfig.LOCALE,
        "transaction_type": "sale",
        "reference_number": row["client_ref"] or reqid,
        "amount": f"{float(row['amount']):.2f}",
        "currency": row["currency"],
        "override_custom_receipt_page": receipt,
        "override_custom_cancel_page": receipt,
    }
    names = list(fields.keys()) + ["unsigned_field_names", "signed_field_names"]
    fields["unsigned_field_names"] = ""
    fields["signed_field_names"] = ",".join(names)
    fields["signature"] = sign(fields, names)
    inputs = "\n".join(f'<input type="hidden" name="{escape(k)}" value="{escape(str(v))}">' for k, v in fields.items())
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Redirecting to secure payment…</title>
<style>body{{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;color:#123B70}}</style>
</head><body onload="document.forms[0].submit()">
<form method="post" action="{escape(CyberSourceConfig.pay_url())}">{inputs}
<noscript><button type="submit">Continue to secure payment</button></noscript></form>
<p>Taking you to NDB Bank's secure payment page…</p></body></html>"""


def handle_receipt(form: dict) -> str:
    """Verifies and stores the bank's result; returns the URL to send the
    browser to. An unverifiable POST changes nothing."""
    names = [n for n in (form.get("signed_field_names") or "").split(",") if n]
    if not names or not hmac.compare_digest(sign(form, names), form.get("signature") or ""):
        logger.error(f"NDB/CyberSource receipt with an INVALID signature ignored (uuid={form.get('req_transaction_uuid')})")
        raise CyberSourceError("The payment result could not be verified.")
    reqid = form.get("req_transaction_uuid") or ""
    row = _db("SELECT * FROM cybersource_payments WHERE reqid=%s", (reqid,), fetch=True)
    if not row:
        raise CyberSourceError("Payment not found.")
    decision = (form.get("decision") or "").upper()
    if row["status"] == "started":
        signed = {n: form.get(n) for n in names}  # only what the bank signed is kept
        _db("UPDATE cybersource_payments SET status=%s, response_json=%s, completed_at=NOW() WHERE reqid=%s AND status='started'",
            (decision.lower() or "error", json.dumps(signed), reqid))
    logger.info(f"NDB/CyberSource result: {decision} reason={form.get('reason_code')} "
                f"ref={form.get('req_reference_number')} txn={form.get('transaction_id')}")
    if decision == "CANCEL":
        return _with_query(row["cancel_url"] or row["return_url"], {"reqid": reqid, "payment": "cancelled"})
    return _with_query(row["return_url"], {"reqid": reqid})


def owns(reqid: str) -> bool:
    """True when this reqid is an NDB/CyberSource payment."""
    try:
        return bool(_db("SELECT reqid FROM cybersource_payments WHERE reqid=%s", (reqid,), fetch=True))
    except Exception:
        return False


def complete_payment(reqid: str) -> dict:
    """Result of a payment, in the same shape the previous gateway returned."""
    row = _db("SELECT * FROM cybersource_payments WHERE reqid=%s", (reqid,), fetch=True)
    if not row:
        raise CyberSourceError("Payment not found.")
    if not row.get("response_json"):
        return {"success": False, "response_code": "PENDING", "response_text": "No result from the bank yet",
                "client_ref": row["client_ref"], "payment_amount": None, "currency": row["currency"], "raw": {}}
    r = json.loads(row["response_json"])
    decision = (r.get("decision") or "").upper()
    reason = str(r.get("reason_code") or "")
    amount = r.get("auth_amount") or r.get("req_amount")
    success = decision == "ACCEPT" and reason in _SUCCESS_REASON_CODES
    return {
        "success": success,
        "response_code": reason or decision,
        "response_text": r.get("message") or decision,
        "txn_reference": r.get("transaction_id"),
        "auth_code": r.get("auth_code"),
        # The reference we signed in the request, echoed (signed) by the bank.
        "client_ref": r.get("req_reference_number"),
        "comment": row.get("comment"),
        "card_type": r.get("req_card_type") or r.get("card_type_name"),
        "card_number_masked": r.get("req_card_number"),
        "card_expiry": r.get("req_card_expiry_date"),
        "total_amount": round(float(amount) * 100) if amount else None,
        "payment_amount": round(float(amount) * 100) if amount else None,
        "currency": (r.get("req_currency") or row["currency"] or "").upper(),
        "raw": r,
    }

"""
Customer contact changes with a one-time code (11/11/2026, item 4).

A customer changes the email or mobile number on their account only while
signed in, and only after typing the 6-digit code sent to the NEW address:
email by email, mobile by SMS (when SMS is set up). So tickets shared from the
admin Booking Lookup always go to contacts the customer proved are theirs —
staff can no longer type a contact in.

Codes: 6 digits, valid 10 minutes, 5 tries, at most 5 codes per customer per
day; only a hash is stored. Own table contact_change_otps, created on first use.
"""
import hashlib
import logging
import re
import secrets
import threading
from datetime import datetime, timedelta

import database

logger = logging.getLogger(__name__)

CODE_MINUTES = 10
MAX_TRIES = 5
MAX_PER_DAY = 5

_ready = False
_lock = threading.Lock()


class ContactChangeError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _ensure_table() -> None:
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        conn = database.get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS contact_change_otps (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    customer_id INT NOT NULL,
                    field VARCHAR(10) NOT NULL,
                    new_value VARCHAR(190) NOT NULL,
                    code_hash CHAR(64) NOT NULL,
                    expires_at DATETIME NOT NULL,
                    tries INT NOT NULL DEFAULT 0,
                    used_at DATETIME NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_cco_customer (customer_id)
                )""")
            conn.commit()
            _ready = True
        finally:
            cur.close()
            conn.close()


def _hash(customer_id: int, code: str) -> str:
    return hashlib.sha256(f"{customer_id}:{code}".encode()).hexdigest()


def mask(field: str, value: str) -> str:
    if field == "email":
        name, _, domain = value.partition("@")
        return f"{name[:2]}{'*' * max(len(name) - 2, 1)}@{domain}"
    digits = re.sub(r"\D", "", value)
    return f"{'*' * max(len(digits) - 3, 0)}{digits[-3:]}"


def _normalise(field: str, value: str) -> str:
    value = (value or "").strip()
    if field == "email":
        value = value.lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) or len(value) > 190:
            raise ContactChangeError("Please enter a valid email address.")
        return value
    digits = re.sub(r"[^\d+]", "", value)
    if len(re.sub(r"\D", "", digits)) < 9 or len(digits) > 16:
        raise ContactChangeError("Please enter a valid mobile number, e.g. 0771234567 or +94771234567.")
    return digits


def start(customer: dict, field: str, new_value: str) -> dict:
    """customer: {id, email, phone}. Sends the code to the new contact."""
    if field not in ("email", "phone"):
        raise ContactChangeError("Choose email or mobile number.")
    value = _normalise(field, new_value)
    current = (customer.get(field) or "").strip().lower() if field == "email" else re.sub(r"[^\d+]", "", customer.get(field) or "")
    if value == current:
        raise ContactChangeError(f"That is already your {'email' if field == 'email' else 'mobile number'}.")
    if field == "email" and database.get_customer_by_email(value):
        raise ContactChangeError("Another account already uses that email address.", 409)
    _ensure_table()
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM contact_change_otps WHERE customer_id=%s AND created_at >= %s",
                    (customer["id"], datetime.now() - timedelta(days=1)))
        if cur.fetchone()[0] >= MAX_PER_DAY:
            raise ContactChangeError("Too many codes requested today. Please try again tomorrow.", 429)
        code = f"{secrets.randbelow(1_000_000):06d}"
        text = (f"Your Tick and Book verification code is {code}. It is valid for {CODE_MINUTES} minutes. "
                f"If you didn't ask to change your contact details, ignore this message.")
        if field == "email":
            from services import email_service
            ok, err = email_service._send(value, "Your Tick and Book verification code", text + "\n\nTick and Book")
        else:
            from services import sms_service
            ok, err = sms_service.send_sms(value, text)
        if not ok:
            logger.warning(f"Customer {customer['id']}: verification code not sent to {mask(field, value)}: {err}")
            raise ContactChangeError("We couldn't send the verification code right now"
                                     + (" (email sending isn't set up yet)" if "not configured" in str(err or "").lower()
                                        or "smtp" in str(err or "").lower() else "")
                                     + ". Please try again later.", 503)
        cur.execute("UPDATE contact_change_otps SET used_at=NOW() WHERE customer_id=%s AND used_at IS NULL", (customer["id"],))
        cur.execute("INSERT INTO contact_change_otps (customer_id, field, new_value, code_hash, expires_at) VALUES (%s,%s,%s,%s,%s)",
                    (customer["id"], field, value, _hash(customer["id"], code), datetime.now() + timedelta(minutes=CODE_MINUTES)))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    logger.info(f"Customer {customer['id']}: {field} change code sent to {mask(field, value)}")
    return {"sent_to": mask(field, value), "field": field, "valid_minutes": CODE_MINUTES}


def verify(customer: dict, code: str) -> dict:
    """Checks the code and saves the new contact. Returns {field, value}."""
    code = re.sub(r"\D", "", code or "")
    _ensure_table()
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM contact_change_otps WHERE customer_id=%s AND used_at IS NULL ORDER BY id DESC LIMIT 1",
                    (customer["id"],))
        row = cur.fetchone()
        if not row or row["expires_at"] < datetime.now():
            raise ContactChangeError("This code has expired. Please ask for a new one.")
        if row["tries"] >= MAX_TRIES:
            raise ContactChangeError("Too many wrong codes. Please ask for a new one.", 429)
        if not secrets.compare_digest(row["code_hash"], _hash(customer["id"], code)):
            cur.execute("UPDATE contact_change_otps SET tries=tries+1 WHERE id=%s", (row["id"],))
            conn.commit()
            raise ContactChangeError("That code isn't right. Please check it and try again.")
        field, value = row["field"], row["new_value"]
        if field == "email" and database.get_customer_by_email(value):
            raise ContactChangeError("Another account already uses that email address.", 409)
        cur.execute(f"UPDATE customers SET {'email' if field == 'email' else 'phone'}=%s WHERE id=%s", (value, customer["id"]))
        cur.execute("UPDATE contact_change_otps SET used_at=NOW() WHERE id=%s", (row["id"],))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    logger.info(f"Customer {customer['id']}: {field} changed to {mask(field, value)} (verified by code)")
    return {"field": field, "value": value}

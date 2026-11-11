"""
Bank transfer / deposit and cash payments (11/11/2026, item 12).

Bank transfer: the customer's booking is held at the airline, the customer is
shown (and emailed) the amount, Tick and Book's bank details and the PNR to use
as the deposit reference. Staff confirm the money has arrived in Admin → Bank &
Cash Payments; only then is the ticket issued and emailed — the same issuing
step as after a card payment. Later the bank's API can confirm the deposit by
its PNR reference and call the same confirm step automatically.

Cash: for staff booking for a walk-in customer (only offered when a staff
member is signed in). Staff record the cash received and the ticket is issued.

Switches (payment_method_settings, one row): card ON, bank OFF, cash OFF by
default — so nothing changes until a super admin turns a method on. Own
tables, created on first use: payment_method_settings, offline_payments.
"""
import logging
import threading
from datetime import datetime, timedelta

import database

logger = logging.getLogger(__name__)

METHOD_LABEL = {"bank": "Bank Transfer", "cash": "Cash"}

_ready = False
_lock = threading.Lock()


def _ensure_tables() -> None:
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
                CREATE TABLE IF NOT EXISTS payment_method_settings (
                    id TINYINT PRIMARY KEY,
                    card_enabled TINYINT(1) NOT NULL DEFAULT 1,
                    bank_enabled TINYINT(1) NOT NULL DEFAULT 0,
                    cash_enabled TINYINT(1) NOT NULL DEFAULT 0,
                    bank_instructions TEXT NULL,
                    bank_hold_hours INT NOT NULL DEFAULT 12,
                    updated_by VARCHAR(100) NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )""")
            cur.execute("INSERT IGNORE INTO payment_method_settings (id) VALUES (1)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS offline_payments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    locator_code VARCHAR(12) NOT NULL,
                    method VARCHAR(10) NOT NULL,
                    amount DECIMAL(12,2) NOT NULL,
                    currency VARCHAR(3) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'awaiting',
                    customer_name VARCHAR(150) NULL,
                    customer_email VARCHAR(190) NULL,
                    started_by VARCHAR(100) NULL,
                    pay_by DATETIME NULL,
                    received_amount DECIMAL(12,2) NULL,
                    bank_reference VARCHAR(120) NULL,
                    confirmed_by VARCHAR(100) NULL,
                    confirmed_at DATETIME NULL,
                    ticket_number VARCHAR(40) NULL,
                    note VARCHAR(500) NULL,
                    error VARCHAR(1000) NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_offline_locator (locator_code),
                    INDEX idx_offline_status (status)
                )""")
            conn.commit()
            _ready = True
        finally:
            cur.close()
            conn.close()


def _q(sql, args=(), one=False, write=False):
    _ensure_tables()
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, args)
        if write:
            conn.commit()
            return cur.rowcount if not one else cur.lastrowid
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows
    finally:
        cur.close()
        conn.close()


def get_settings() -> dict:
    row = _q("SELECT * FROM payment_method_settings WHERE id=1", one=True) or {}
    return {"card_enabled": bool(row.get("card_enabled", 1)), "bank_enabled": bool(row.get("bank_enabled", 0)),
            "cash_enabled": bool(row.get("cash_enabled", 0)), "bank_instructions": row.get("bank_instructions") or "",
            "bank_hold_hours": int(row.get("bank_hold_hours") or 12), "updated_by": row.get("updated_by"),
            "updated_at": row["updated_at"].isoformat(sep=" ") if row.get("updated_at") else None}


def update_settings(values: dict, admin_username: str | None) -> dict:
    cols = {}
    for k in ("card_enabled", "bank_enabled", "cash_enabled"):
        if values.get(k) is not None:
            cols[k] = 1 if values[k] else 0
    if values.get("bank_instructions") is not None:
        cols["bank_instructions"] = str(values["bank_instructions"])[:4000]
    if values.get("bank_hold_hours") is not None:
        cols["bank_hold_hours"] = max(1, min(int(values["bank_hold_hours"]), 168))
    if cols:
        sets = ", ".join(f"{k}=%s" for k in cols)
        _q(f"UPDATE payment_method_settings SET {sets}, updated_by=%s WHERE id=1", (*cols.values(), admin_username), write=True)
        logger.warning(f"Payment method settings changed by {admin_username}: {cols}")
    return get_settings()


def public_settings() -> dict:
    s = get_settings()
    return {"card": s["card_enabled"], "bank": s["bank_enabled"], "cash": s["cash_enabled"],
            "bank_instructions": s["bank_instructions"] if s["bank_enabled"] else "",
            "bank_hold_hours": s["bank_hold_hours"]}


def open_for(locator: str) -> dict | None:
    return _q("SELECT * FROM offline_payments WHERE locator_code=%s AND status IN ('awaiting','confirming') "
              "ORDER BY id DESC LIMIT 1", (locator.upper(),), one=True)


def start(locator: str, method: str, amount: float, currency: str, customer_name: str | None,
          customer_email: str | None, started_by: str | None, hold_hours: int) -> dict:
    existing = open_for(locator)
    if existing:
        return existing
    pay_by = datetime.now() + timedelta(hours=hold_hours) if method == "bank" else None
    new_id = _q("""INSERT INTO offline_payments (locator_code, method, amount, currency, customer_name, customer_email,
                   started_by, pay_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (locator.upper(), method, amount, currency, (customer_name or "")[:150] or None,
                 (customer_email or "")[:190] or None, started_by, pay_by), one=True, write=True)
    return get(new_id)


def get(payment_id: int) -> dict | None:
    return _q("SELECT * FROM offline_payments WHERE id=%s", (payment_id,), one=True)


def claim_for_confirm(payment_id: int) -> bool:
    """awaiting -> confirming, once: two staff pressing Confirm can't issue twice."""
    return _q("UPDATE offline_payments SET status='confirming' WHERE id=%s AND status='awaiting'",
              (payment_id,), write=True) == 1


def finish(payment_id: int, status: str, **fields) -> None:
    allowed = {"received_amount", "bank_reference", "confirmed_by", "confirmed_at", "ticket_number", "note", "error"}
    cols = {k: v for k, v in fields.items() if k in allowed}
    sets = ", ".join([f"{k}=%s" for k in cols] + ["status=%s"])
    _q(f"UPDATE offline_payments SET {sets} WHERE id=%s", (*cols.values(), status, payment_id), write=True)


def listing(status: str | None = None, limit: int = 200) -> list:
    if status:
        rows = _q("SELECT * FROM offline_payments WHERE status=%s ORDER BY id DESC LIMIT %s", (status, limit))
    else:
        rows = _q("SELECT * FROM offline_payments ORDER BY id DESC LIMIT %s", (limit,))
    for r in rows:
        for k in ("pay_by", "confirmed_at", "created_at", "updated_at"):
            if r.get(k) and hasattr(r[k], "isoformat"):
                r[k] = r[k].isoformat(sep=" ")
        for k in ("amount", "received_amount"):
            if r.get(k) is not None:
                r[k] = float(r[k])
    return rows


def instructions_email(p: dict, instructions: str) -> tuple[str, str]:
    subject = f"Tick and Book booking {p['locator_code']} — how to pay by bank transfer"
    pay_by = p.get("pay_by")
    pay_by_txt = pay_by.strftime("%d/%m/%Y %H:%M") if hasattr(pay_by, "strftime") else (pay_by or "")
    body = (f"Dear {p.get('customer_name') or 'customer'},\n\n"
            f"Your booking {p['locator_code']} is held with the airline. To get your ticket, please deposit or transfer\n\n"
            f"    {p['currency']} {float(p['amount']):,.2f}\n\n"
            f"using your booking reference {p['locator_code']} as the payment reference"
            + (f", by {pay_by_txt}" if pay_by_txt else "") + ".\n\n"
            f"Bank details:\n{instructions or '(bank details will be added here by Tick and Book)'}\n\n"
            "As soon as our team confirms your payment has arrived, your e-ticket is issued and emailed to you.\n"
            "If the airline releases the booking before your payment reaches us, we will let you know.\n\n"
            "Tick and Book")
    return subject, body

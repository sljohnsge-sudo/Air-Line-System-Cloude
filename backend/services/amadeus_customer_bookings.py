"""
Amadeus customer bookings on the website (13/11/2026): hold -> pay -> ticket.

The Amadeus backend keeps the booking itself (its own amadeus_system DB).
This table is the website's side of it: what the customer is charged (the
Amadeus fare + our markup, worked out here on the server, never taken from
the browser), how they pay, and whether the ticket was issued after payment.

Status: held -> ticketing -> ticketed, or failed (paid, no ticket: an
automatic refund request is filed). A card payment is only accepted for a
'held' booking; 'ticketing' is claimed once, so two returns from the bank
page (or two staff pressing Confirm) can't issue twice.

Own table, created on first use: amadeus_customer_bookings. No passport
numbers are stored here.
"""
import json
import logging
import threading

import database

logger = logging.getLogger(__name__)

REF_PREFIX = "AD-"  # payment reference: AD-<Amadeus PNR>, never confused with a Travelport PNR

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
                CREATE TABLE IF NOT EXISTS amadeus_customer_bookings (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    locator VARCHAR(12) NOT NULL UNIQUE,
                    status VARCHAR(20) NOT NULL DEFAULT 'held',
                    net_amount DECIMAL(12,2) NOT NULL,
                    amount DECIMAL(12,2) NOT NULL,
                    currency VARCHAR(3) NOT NULL,
                    tickets INT NOT NULL DEFAULT 1,
                    customer_id INT NULL,
                    lead_name VARCHAR(150) NULL,
                    email VARCHAR(190) NULL,
                    phone VARCHAR(40) NULL,
                    travel_date VARCHAR(10) NULL,
                    route VARCHAR(120) NULL,
                    flights VARCHAR(200) NULL,
                    airline_code VARCHAR(3) NULL,
                    booked_by VARCHAR(100) NULL,
                    payment_method VARCHAR(10) NULL,
                    payment_ref VARCHAR(120) NULL,
                    ticket_numbers VARCHAR(400) NULL,
                    error VARCHAR(1000) NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_acb_status (status)
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
            return cur.rowcount
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows
    finally:
        cur.close()
        conn.close()


def _clean(row: dict | None) -> dict | None:
    if not row:
        return row
    for k in ("net_amount", "amount"):
        if row.get(k) is not None:
            row[k] = float(row[k])
    for k in ("created_at", "updated_at"):
        if row.get(k) and hasattr(row[k], "isoformat"):
            row[k] = row[k].isoformat(sep=" ")
    row["ticket_list"] = json.loads(row["ticket_numbers"]) if row.get("ticket_numbers") else []
    return row


def create(locator: str, *, net_amount: float, amount: float, currency: str, tickets: int, customer_id,
           lead_name: str, email: str, phone: str, travel_date: str, route: str, flights: str,
           airline_code: str, booked_by: str | None) -> dict:
    _q("""INSERT INTO amadeus_customer_bookings (locator, net_amount, amount, currency, tickets, customer_id,
              lead_name, email, phone, travel_date, route, flights, airline_code, booked_by)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
       (locator.upper(), net_amount, amount, currency, tickets, customer_id, (lead_name or "")[:150],
        (email or "")[:190], (phone or "")[:40], (travel_date or "")[:10], (route or "")[:120],
        (flights or "")[:200], (airline_code or "")[:3], booked_by), write=True)
    return get(locator)


def get(locator: str) -> dict | None:
    return _clean(_q("SELECT * FROM amadeus_customer_bookings WHERE locator=%s", ((locator or "").upper(),), one=True))


def set_method(locator: str, method: str) -> None:
    _q("UPDATE amadeus_customer_bookings SET payment_method=%s WHERE locator=%s AND status='held'",
       (method, locator.upper()), write=True)


def claim_ticketing(locator: str) -> bool:
    """held -> ticketing, once."""
    return _q("UPDATE amadeus_customer_bookings SET status='ticketing' WHERE locator=%s AND status='held'",
              (locator.upper(),), write=True) == 1


def finish(locator: str, status: str, *, payment_method: str | None = None, payment_ref: str | None = None,
           ticket_numbers: list | None = None, error: str | None = None) -> None:
    _q("""UPDATE amadeus_customer_bookings SET status=%s,
              payment_method=COALESCE(%s, payment_method), payment_ref=COALESCE(%s, payment_ref),
              ticket_numbers=COALESCE(%s, ticket_numbers), error=%s
          WHERE locator=%s""",
       (status, payment_method, (payment_ref or "")[:120] or None,
        json.dumps(ticket_numbers) if ticket_numbers else None, (error or "")[:1000] or None, locator.upper()),
       write=True)

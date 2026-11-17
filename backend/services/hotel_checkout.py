"""
services/hotel_checkout.py
==========================
Hotel payment — the customer pays the hotel by card (NDB / CyberSource, the
same gateway as flights) and only then is the room reserved with Travelport.
A hotel is always paid on its own: never added to a flight ticket's payment.

  1. /api/hotels/availability   every rate Travelport quotes is stored here
                                 (hotel_offer_quotes) with its conversion rate,
                                 so the price is never taken from the browser.
  2. /api/hotels/checkout        live Rules re-price of the chosen rate, the
                                 LKR amount (hotel total x Travelport's own
                                 rate), an order (hotel_orders) and the bank's
                                 payment page.
  3. /api/hotels/checkout/{ref}/complete
                                 after the bank: payment verified and claimed
                                 once (main._verify_and_claim_payment), then the
                                 room reserved (Create Reservation) and saved.
                                 If the hotel refuses after payment the order
                                 is left "book_failed" for staff to rebook or
                                 refund — the customer is told so.

Charged currency: LKR. Travelport keeps hotel prices in the hotel's currency
(AED, EUR...) and, for requestedCurrency LKR, sends its own conversion rate
with the rates (CurrencyRateConversion) — that rate is the one charged. No
markup (17/11/2026 decision).
"""
import json
import logging
import secrets
import string
from datetime import datetime, timedelta

from database import get_db_connection

logger = logging.getLogger(__name__)

CHARGE_CURRENCY = "LKR"
QUOTE_TTL = timedelta(minutes=30)   # Travelport availability sessions last 30 minutes
ORDER_PREFIX = "HTL"

# Order status: awaiting_payment -> paid -> booked
#                                         \-> book_failed (paid, hotel refused: staff rebook / refund)
#               awaiting_payment -> cancelled (customer left the bank page)


def _conn():
    return get_db_connection()


def init_tables() -> None:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hotel_offer_quotes (
                offer_id VARCHAR(255) PRIMARY KEY,
                data LONGTEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hotel_orders (
                order_ref VARCHAR(20) PRIMARY KEY,
                status VARCHAR(30) NOT NULL DEFAULT 'awaiting_payment',
                offer_id VARCHAR(255) NOT NULL,
                customer_id INT NULL,
                guest_name VARCHAR(150),
                guest_email VARCHAR(200),
                property_name VARCHAR(200),
                check_in_date DATE NULL,
                check_out_date DATE NULL,
                hotel_currency VARCHAR(5),
                hotel_total DOUBLE,
                charge_currency VARCHAR(5),
                charge_amount DOUBLE,
                conversion_rate DOUBLE,
                reqid VARCHAR(64) NULL,
                locator_code VARCHAR(50) NULL,
                confirmation_number VARCHAR(50) NULL,
                error VARCHAR(500) NULL,
                order_json LONGTEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_hotel_orders_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        cur.execute("DELETE FROM hotel_offer_quotes WHERE created_at < %s", (datetime.now() - timedelta(days=2),))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def save_quotes(offers: list[dict], context: dict) -> None:
    """Remember every rate Travelport just quoted (with the search it came
    from: hotel, dates, guests, rooms) so checkout prices it server-side."""
    if not offers:
        return
    rows = [(o["offer_id"], json.dumps({**o, "context": context}, default=str))
            for o in offers if o.get("offer_id")]
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.executemany("""INSERT INTO hotel_offer_quotes (offer_id, data) VALUES (%s, %s)
                           ON DUPLICATE KEY UPDATE data=VALUES(data), created_at=CURRENT_TIMESTAMP""", rows)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_quote(offer_id: str) -> dict | None:
    """The stored rate, or None when unknown or older than 30 minutes."""
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT data, created_at FROM hotel_offer_quotes WHERE offer_id=%s", (offer_id,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if not row or row["created_at"] < datetime.now() - QUOTE_TTL:
        return None
    return json.loads(row["data"])


def new_order_ref() -> str:
    return ORDER_PREFIX + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(9))


def create_order(order: dict) -> dict:
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO hotel_orders (order_ref, status, offer_id, customer_id, guest_name, guest_email,
                property_name, check_in_date, check_out_date, hotel_currency, hotel_total,
                charge_currency, charge_amount, conversion_rate, order_json)
            VALUES (%s,'awaiting_payment',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (order["order_ref"], order["offer_id"], order.get("customer_id"), order["guest_name"],
              order["guest_email"], order.get("property_name"), order.get("check_in_date"),
              order.get("check_out_date"), order["hotel_currency"], order["hotel_total"],
              order["charge_currency"], order["charge_amount"], order["conversion_rate"],
              json.dumps(order, default=str)))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return order


def get_order(order_ref: str) -> dict | None:
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM hotel_orders WHERE order_ref=%s", (order_ref,))
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if row:
        row["order"] = json.loads(row.pop("order_json") or "{}")
    return row


def update_order(order_ref: str, **fields) -> None:
    allowed = {"status", "reqid", "locator_code", "confirmation_number", "error"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(f"UPDATE hotel_orders SET {', '.join(f'{k}=%s' for k in sets)} WHERE order_ref=%s",
                    (*[str(v)[:500] if k == 'error' and v is not None else v for k, v in sets.items()], order_ref))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def list_orders(status: str | None = None, limit: int = 200) -> list[dict]:
    """Staff view: orders, newest first (book_failed ones need a person)."""
    conn = _conn()
    cur = conn.cursor(dictionary=True)
    try:
        sql = ("SELECT order_ref, status, guest_name, guest_email, property_name, check_in_date, check_out_date, "
               "hotel_currency, hotel_total, charge_currency, charge_amount, reqid, locator_code, "
               "confirmation_number, error, created_at, updated_at FROM hotel_orders")
        args = ()
        if status:
            sql += " WHERE status=%s"
            args = (status,)
        cur.execute(sql + " ORDER BY created_at DESC LIMIT %s", (*args, limit))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def charge_for(hotel_total: float, quote: dict) -> tuple[float, float] | None:
    """(LKR amount, rate) for a hotel total in the quote's currency, using
    Travelport's own rate from the same availability answer; None if
    Travelport sent no rate into LKR (then we do not take payment)."""
    conv = quote.get("converted") or {}
    if (conv.get("currency") or "").upper() != CHARGE_CURRENCY or not conv.get("rate"):
        if (quote.get("currency") or "").upper() == CHARGE_CURRENCY:
            return round(float(hotel_total), 2), 1.0
        return None
    rate = float(conv["rate"])
    return round(float(hotel_total) * rate, 2), rate


try:
    init_tables()
except Exception as e:  # never stop the API starting over this
    logger.error(f"hotel_checkout tables not created: {e}")

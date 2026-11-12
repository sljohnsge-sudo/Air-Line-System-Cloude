"""
database.py
============
MySQL cache for issued Amadeus bookings -- XAMPP MySQL backend.
Database: amadeus_system (localhost:3306) -- separate schema from the
Travelport system's `final_travels_system` database.
"""

import os
import json
from datetime import datetime
import mysql.connector
from mysql.connector import pooling
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "amadeus_system")


def _ensure_database_exists():
    conn = mysql.connector.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD)
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cur.close()
    conn.close()


_ensure_database_exists()

_pool = pooling.MySQLConnectionPool(
    pool_name="amadeus_pool",
    pool_size=5,
    host=MYSQL_HOST,
    port=MYSQL_PORT,
    user=MYSQL_USER,
    password=MYSQL_PASSWORD,
    database=MYSQL_DATABASE,
    autocommit=False,
    charset="utf8mb4",
    collation="utf8mb4_unicode_ci",
)


def get_db_connection():
    return _pool.get_connection()


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            order_id VARCHAR(64) UNIQUE,
            origin VARCHAR(8),
            destination VARCHAR(8),
            departure_date DATE,
            return_date DATE,
            passenger_name VARCHAR(255),
            passenger_email VARCHAR(255),
            raw_offer JSON,
            raw_order JSON,
            status VARCHAR(32) DEFAULT 'confirmed',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # 12/11/2026: ticket numbers get their own column (they used to be looked
    # for inside raw_order, where nothing ever wrote them, so a lookup by
    # ticket number always said "not found").
    for ddl in ("ALTER TABLE bookings ADD COLUMN ticket_numbers VARCHAR(255) NULL",
                "ALTER TABLE bookings ADD COLUMN ticketed_at DATETIME NULL",
                "ALTER TABLE bookings ADD COLUMN emd_numbers VARCHAR(500) NULL",
                # 12/11/2026: staff cancel / void / refund (services/servicing_service.py)
                "ALTER TABLE bookings ADD COLUMN servicing_at DATETIME NULL",
                "ALTER TABLE bookings ADD COLUMN servicing_note VARCHAR(500) NULL"):
        try:
            cur.execute(ddl)
        except mysql.connector.Error:
            pass  # column already there
    conn.commit()
    cur.close()
    conn.close()


def save_booking(order_id: str, origin: str, destination: str, departure_date: str,
                  return_date: str | None, passenger_name: str, passenger_email: str,
                  raw_offer: dict, raw_order: dict):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO bookings
           (order_id, origin, destination, departure_date, return_date,
            passenger_name, passenger_email, raw_offer, raw_order)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (order_id, origin, destination, departure_date, return_date,
         passenger_name, passenger_email, json.dumps(raw_offer), json.dumps(raw_order)),
    )
    conn.commit()
    cur.close()
    conn.close()


def amadeus_date_to_iso(value: str | None) -> str | None:
    """Amadeus dates are DDMMYY ("101226" = 10 Dec 2026). MySQL read them as
    YYMMDD and saved 2010-12-26 -- 55 of 59 bookings had a wrong departure
    date until 12/11/2026. ISO input passes through unchanged."""
    if not value:
        return None
    value = str(value).strip()
    if len(value) == 6 and value.isdigit():
        return f"20{value[4:6]}-{value[2:4]}-{value[0:2]}"
    return value[:10]


def mark_ticketed(order_id: str, ticket_numbers: list[str]) -> None:
    numbers = ",".join(dict.fromkeys(n for n in ticket_numbers if n))
    if not numbers:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET ticket_numbers=%s, ticketed_at=COALESCE(ticketed_at, NOW()), status='ticketed' "
                "WHERE order_id=%s", (numbers, order_id))
    conn.commit()
    cur.close()
    conn.close()


def mark_emds(order_id: str, emd_numbers: list[str]) -> None:
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT emd_numbers FROM bookings WHERE order_id=%s", (order_id,))
    row = cur.fetchone() or {}
    have = [n for n in (row.get("emd_numbers") or "").split(",") if n]
    merged = ",".join(dict.fromkeys(have + [n for n in emd_numbers if n]))
    cur.execute("UPDATE bookings SET emd_numbers=%s WHERE order_id=%s", (merged, order_id))
    conn.commit()
    cur.close()
    conn.close()


def mark_servicing(order_id: str, status: str, note: str) -> None:
    """After a staff cancel / void / refund: status cancelled, voided or
    refunded, with who did it and the amounts (servicing_service.py)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE bookings SET status=%s, servicing_at=NOW(), servicing_note=%s WHERE order_id=%s",
                (status, (note or "")[:500], order_id))
    conn.commit()
    cur.close()
    conn.close()


def list_bookings():
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, order_id, origin, destination, departure_date, return_date, "
                "passenger_name, passenger_email, status, ticket_numbers, ticketed_at, emd_numbers, "
                "servicing_at, servicing_note, created_at "
                "FROM bookings ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def find_booking_by_pnr_or_ticket(query: str) -> dict | None:
    """Used by the "Check My Ticket Status" lookup (GET /api/bookings/lookup)
    to answer "does this PNR/ticket number belong to Amadeus?" -- purely a
    local-DB index lookup, never the source of the displayed data (the
    caller still re-fetches live via PNR_Retrieve once this resolves a
    locator). Ticket numbers aren't a dedicated column -- they only ever
    exist inside raw_order's ticketNumbers array (see
    flight_booking_service.issue_ticket), so JSON_SEARCH is the only way to
    find a row by one."""
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    digits = "".join(ch for ch in query if ch.isdigit())
    cur.execute(
        "SELECT * FROM bookings WHERE order_id=%s OR JSON_SEARCH(raw_order, 'one', %s) IS NOT NULL "
        "OR (%s <> '' AND REPLACE(COALESCE(ticket_numbers, ''), '-', '') LIKE %s) "
        "ORDER BY created_at DESC LIMIT 1",
        (query, query, digits, f"%{digits}%" if len(digits) >= 10 else "__no_match__"),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

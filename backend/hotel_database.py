"""
hotel_database.py
==================
Hotel booking storage — a separate module and a separate table
(hotel_bookings) from the flight `bookings` table in database.py, kept
fully independent per instruction not to touch flight-side code. Uses the
SAME MySQL database (final_travels_system) and the same pooled connection
(database.get_db_connection()) — no new connection pool is created.

Table:
    hotel_bookings -- issued hotel reservation records
"""

import json
from datetime import datetime
from database import get_db_connection, MYSQL_DATABASE


def init_hotel_db():
    """Create the hotel_bookings table if it doesn't exist. Safe to call on
    every import (mirrors database.py's own init_db() pattern)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hotel_bookings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                locator_code VARCHAR(50) NOT NULL UNIQUE,
                confirmation_number VARCHAR(50),
                status VARCHAR(30) DEFAULT 'Confirmed',
                customer_id INT NULL,
                guest_name VARCHAR(150),
                guest_email VARCHAR(200),
                guest_phone VARCHAR(20),
                property_name VARCHAR(200),
                chain_code VARCHAR(5),
                property_code VARCHAR(10),
                city VARCHAR(100),
                country_code VARCHAR(5),
                check_in_date DATE NULL,
                check_out_date DATE NULL,
                rooms INT DEFAULT 1,
                room_description VARCHAR(255),
                total_price DOUBLE DEFAULT 0.0,
                currency VARCHAR(5) DEFAULT 'USD',
                booking_date DATETIME,
                payment_method VARCHAR(50) DEFAULT 'Credit Card',
                raw_reservation_json LONGTEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # customer_id FK, added via a guarded check since MySQL lacks a
        # universal ADD CONSTRAINT IF NOT EXISTS (same pattern as database.py).
        cursor.execute("""SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME='hotel_bookings'
            AND CONSTRAINT_NAME='fk_hotel_bookings_customer'""", (MYSQL_DATABASE,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("""SELECT COUNT(*) FROM information_schema.TABLES
                WHERE TABLE_SCHEMA=%s AND TABLE_NAME='customers'""", (MYSQL_DATABASE,))
            if cursor.fetchone()[0] > 0:
                cursor.execute("""ALTER TABLE hotel_bookings ADD CONSTRAINT fk_hotel_bookings_customer
                    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL""")

        conn.commit()
    finally:
        cursor.close()
        conn.close()


def save_hotel_booking(booking: dict) -> dict:
    """Save or update a hotel booking. Uses ON DUPLICATE KEY UPDATE, same
    pattern as database.save_booking() for flight tickets."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        booking_date = booking.get("booking_date") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = """
            INSERT INTO hotel_bookings (
                locator_code, confirmation_number, status, customer_id,
                guest_name, guest_email, guest_phone,
                property_name, chain_code, property_code, city, country_code,
                check_in_date, check_out_date, rooms, room_description,
                total_price, currency, booking_date, payment_method, raw_reservation_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                confirmation_number=VALUES(confirmation_number), status=VALUES(status),
                customer_id=VALUES(customer_id), guest_name=VALUES(guest_name),
                guest_email=VALUES(guest_email), guest_phone=VALUES(guest_phone),
                property_name=VALUES(property_name), chain_code=VALUES(chain_code),
                property_code=VALUES(property_code), city=VALUES(city), country_code=VALUES(country_code),
                check_in_date=VALUES(check_in_date), check_out_date=VALUES(check_out_date),
                rooms=VALUES(rooms), room_description=VALUES(room_description),
                total_price=VALUES(total_price), currency=VALUES(currency),
                payment_method=VALUES(payment_method), raw_reservation_json=VALUES(raw_reservation_json),
                updated_at=CURRENT_TIMESTAMP
        """
        values = (
            booking.get("locator_code", ""), booking.get("confirmation_number", ""),
            booking.get("status", "Confirmed"), booking.get("customer_id"),
            booking.get("guest_name", ""), booking.get("guest_email", ""), booking.get("guest_phone", ""),
            booking.get("property_name", ""), booking.get("chain_code", ""), booking.get("property_code", ""),
            booking.get("city", ""), booking.get("country_code", ""),
            booking.get("check_in_date") or None, booking.get("check_out_date") or None,
            booking.get("rooms", 1), booking.get("room_description", ""),
            booking.get("total_price", 0.0), booking.get("currency", "USD"),
            booking_date, booking.get("payment_method", "Credit Card"),
            json.dumps(booking.get("raw", {})),
        )
        cursor.execute(sql, values)
        conn.commit()
        cursor.execute("SELECT * FROM hotel_bookings WHERE locator_code=%s", (booking.get("locator_code", ""),))
        row = cursor.fetchone()
        return row if row else booking
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_hotel_booking_by_locator(locator_code: str) -> dict | None:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM hotel_bookings WHERE locator_code=%s", (locator_code,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def get_all_hotel_bookings(email: str | None = None) -> list[dict]:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if email:
        cursor.execute("SELECT * FROM hotel_bookings WHERE guest_email=%s ORDER BY booking_date DESC", (email,))
    else:
        cursor.execute("SELECT * FROM hotel_bookings ORDER BY booking_date DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_hotel_bookings_for_customer(customer_id: int) -> list[dict]:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM hotel_bookings WHERE customer_id=%s ORDER BY booking_date DESC", (customer_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def cancel_hotel_booking(locator_code: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE hotel_bookings SET status='Cancelled' WHERE locator_code=%s", (locator_code,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        cursor.close()
        conn.close()


def get_hotel_sales_summary(start_date: str | None = None, end_date: str | None = None) -> dict:
    """Mirrors database.get_sales_summary() for the admin reports screen,
    but scoped to hotel_bookings only — kept separate from flight totals."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        where = []
        params: list = []
        if start_date:
            where.append("booking_date >= %s")
            params.append(start_date)
        if end_date:
            where.append("booking_date <= %s")
            params.append(end_date)
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        cursor.execute(
            f"""SELECT currency, COUNT(*) AS booking_count,
                       SUM(CASE WHEN status='Cancelled' THEN 0 ELSE total_price END) AS total_sales
                FROM hotel_bookings {clause}
                GROUP BY currency""",
            tuple(params),
        )
        by_currency = cursor.fetchall()

        cursor.execute(
            f"""SELECT COUNT(*) AS total_bookings,
                       SUM(CASE WHEN status='Cancelled' THEN 1 ELSE 0 END) AS total_cancelled
                FROM hotel_bookings {clause}""",
            tuple(params),
        )
        totals = cursor.fetchone()

        return {
            "by_currency": by_currency,
            "total_bookings": totals["total_bookings"] or 0,
            "total_cancelled": totals["total_cancelled"] or 0,
        }
    finally:
        cursor.close()
        conn.close()


init_hotel_db()

"""
database.py
===========
MySQL Cache for Issued Tickets -- XAMPP MySQL Backend
Database: final_travels_system  (localhost:3306, user: root, no password by default)

All function signatures are identical to the previous SQLite version so that
main.py requires zero changes.

Tables:
    bookings  -- issued PNR records
    airports  -- IATA airport lookup cache
"""

import os
import json
from datetime import datetime
import mysql.connector
from mysql.connector import pooling
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

# MySQL connection settings (read from .env, fallback to XAMPP defaults)
MYSQL_HOST     = os.getenv("MYSQL_HOST",     "localhost")
MYSQL_PORT     = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER     = os.getenv("MYSQL_USER",     "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "final_travels_system")

# Connection pool (avoids creating a new connection per request)
_pool = pooling.MySQLConnectionPool(
    pool_name="travels_pool",
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
    """Get a pooled MySQL connection."""
    return _pool.get_connection()


def init_db():
    """Create all tables if they do not already exist."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            locator_code      VARCHAR(20)   NOT NULL UNIQUE,
            pnr               VARCHAR(20)   NOT NULL,
            ticket_number     VARCHAR(50),
            status            VARCHAR(30)   NOT NULL DEFAULT 'Confirmed',
            passenger_name    VARCHAR(150)  NOT NULL,
            passenger_email   VARCHAR(200)  NOT NULL,
            passport_number   VARCHAR(30)   NOT NULL,
            flight_number     VARCHAR(20)   NOT NULL,
            airline           VARCHAR(100)  NOT NULL,
            departure_airport VARCHAR(10)   NOT NULL,
            arrival_airport   VARCHAR(10)   NOT NULL,
            departure_time    VARCHAR(30)   NOT NULL,
            arrival_time      VARCHAR(30)   NOT NULL,
            cabin_class       VARCHAR(30)   NOT NULL DEFAULT 'Economy',
            seat_number       VARCHAR(10),
            total_fare        DOUBLE        NOT NULL DEFAULT 0.0,
            currency          VARCHAR(5)    NOT NULL DEFAULT 'USD',
            booking_date      VARCHAR(30)   NOT NULL,
            offer_id          VARCHAR(100),
            raw_ticket_json   MEDIUMTEXT,
            payment_method    VARCHAR(50)   DEFAULT 'Credit Card',
            fare_source       VARCHAR(10)   DEFAULT 'GDS',
            created_at        DATETIME      DEFAULT CURRENT_TIMESTAMP,
            updated_at        DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS airports (
            iata    VARCHAR(3)    PRIMARY KEY,
            name    VARCHAR(200)  NOT NULL,
            city    VARCHAR(100),
            state   VARCHAR(100),
            country VARCHAR(10),
            lat     DOUBLE,
            lon     DOUBLE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    conn.commit()
    cursor.close()
    populate_airports_table(conn)
    conn.close()


def save_booking(ticket: dict) -> dict:
    """Save or update an issued ticket. Uses ON DUPLICATE KEY UPDATE."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        booking_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sql = """
            INSERT INTO bookings (
                locator_code, pnr, ticket_number, status,
                passenger_name, passenger_email, passport_number,
                flight_number, airline, departure_airport, arrival_airport,
                departure_time, arrival_time, cabin_class, seat_number,
                total_fare, currency, booking_date, offer_id, raw_ticket_json,
                payment_method, fare_source
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                pnr=VALUES(pnr), ticket_number=VALUES(ticket_number),
                status=VALUES(status), passenger_name=VALUES(passenger_name),
                passenger_email=VALUES(passenger_email), passport_number=VALUES(passport_number),
                flight_number=VALUES(flight_number), airline=VALUES(airline),
                departure_airport=VALUES(departure_airport), arrival_airport=VALUES(arrival_airport),
                departure_time=VALUES(departure_time), arrival_time=VALUES(arrival_time),
                cabin_class=VALUES(cabin_class), seat_number=VALUES(seat_number),
                total_fare=VALUES(total_fare), currency=VALUES(currency),
                offer_id=VALUES(offer_id), raw_ticket_json=VALUES(raw_ticket_json),
                payment_method=VALUES(payment_method), fare_source=VALUES(fare_source),
                updated_at=CURRENT_TIMESTAMP
        """
        values = (
            ticket.get("locator_code",""), ticket.get("pnr",""),
            ticket.get("ticket_number",""), ticket.get("status","Confirmed"),
            ticket.get("passenger_name",""), ticket.get("email",""),
            ticket.get("passport_number",""), ticket.get("flight_number",""),
            ticket.get("airline",""), ticket.get("departure_airport",""),
            ticket.get("arrival_airport",""), ticket.get("departure_time",""),
            ticket.get("arrival_time",""), ticket.get("cabin_class","Economy"),
            ticket.get("seat_number",""), ticket.get("total_fare",0.0),
            ticket.get("currency","USD"), booking_date, ticket.get("offer_id",""),
            json.dumps(ticket), ticket.get("payment_method","Credit Card"),
            ticket.get("fare_source","GDS"),
        )
        cursor.execute(sql, values)
        conn.commit()
        cursor.execute("SELECT * FROM bookings WHERE locator_code=%s",(ticket.get("locator_code",""),))
        row = cursor.fetchone()
        return row if row else ticket
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_all_bookings(email: str | None = None) -> list[dict]:
    """Retrieve all cached bookings, optionally filtered by passenger email."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if email:
        cursor.execute("SELECT * FROM bookings WHERE passenger_email=%s ORDER BY booking_date DESC",(email,))
    else:
        cursor.execute("SELECT * FROM bookings ORDER BY booking_date DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    bookings = []
    for b in rows:
        if b.get("raw_ticket_json"):
            try:
                raw_data = json.loads(b["raw_ticket_json"])
                bookings.append({**raw_data, **b})
            except Exception:
                bookings.append(b)
        else:
            bookings.append(b)
    return bookings


def get_booking_by_locator(locator_code: str) -> dict | None:
    """Retrieve a single cached booking by PNR locator code."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM bookings WHERE locator_code=%s",(locator_code,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        if row.get("raw_ticket_json"):
            try:
                raw_data = json.loads(row["raw_ticket_json"])
                return {**raw_data, **row}
            except Exception:
                return row
        return row
    return None


def cancel_booking(locator_code: str) -> bool:
    """Mark a cached booking as Cancelled. Returns True if updated."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT status FROM bookings WHERE locator_code=%s",(locator_code,))
        row = cursor.fetchone()
        if not row or row["status"] == "Cancelled":
            return False
        cursor.execute("UPDATE bookings SET status='Cancelled' WHERE locator_code=%s",(locator_code,))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def search_airports(query: str, limit: int = 15) -> list[dict]:
    """Search airports by IATA, city, or name. Exact IATA match ranked first."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    pattern = f"%{query}%"
    upper   = query.upper()
    prefix  = f"{query}%"
    cursor.execute("""
        SELECT * FROM airports
        WHERE iata LIKE %s OR city LIKE %s OR name LIKE %s
        ORDER BY
            CASE
                WHEN iata = %s    THEN 1
                WHEN iata LIKE %s THEN 2
                WHEN city LIKE %s THEN 3
                ELSE 4
            END, iata ASC
        LIMIT %s
    """, (pattern, pattern, pattern, upper, prefix, prefix, limit))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_bookings_in_date_range(start_dt: str, end_dt: str) -> list[dict]:
    """Return bookings whose booking_date falls in [start_dt, end_dt]."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT locator_code FROM bookings WHERE booking_date >= %s AND booking_date <= %s ORDER BY booking_date ASC",
        (start_dt, end_dt)
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def populate_airports_table(conn):
    """Populate airports from GitHub dataset if the table is empty."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM airports")
    count = cursor.fetchone()[0]
    if count > 0:
        cursor.close()
        return

    print("Populating airports table from raw.githubusercontent.com...")
    import urllib.request

    airports_to_insert = []
    try:
        url = "https://raw.githubusercontent.com/mwgg/Airports/master/airports.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        for key, val in data.items():
            iata = val.get("iata","").strip().upper()
            if not iata or len(iata) != 3:
                continue
            airports_to_insert.append((iata, val.get("name",""), val.get("city",""),
                val.get("state",""), val.get("country",""), val.get("lat"), val.get("lon")))
    except Exception as e:
        print(f"Failed to fetch airports online: {e}. Using fallback list.")
        airports_to_insert = [
            ("CMB","Bandaranaike International Airport","Colombo","Western Province","LK",7.174112,79.8865),
            ("DXB","Dubai International Airport","Dubai","Dubai","AE",25.248665,55.352917),
            ("LHR","London Heathrow Airport","London","England","GB",51.469604,-0.453566),
            ("SIN","Singapore Changi Airport","Singapore","","SG",1.36442,103.98934),
            ("JFK","John F Kennedy International Airport","New York","New York","US",40.639751,-73.778925),
            ("LAX","Los Angeles International Airport","Los Angeles","California","US",33.942536,-118.408074),
            ("DOH","Hamad International Airport","Doha","Doha","QA",25.273056,51.608056),
            ("BKK","Suvarnabhumi Airport","Bangkok","Samut Prakan","TH",13.681108,100.747283),
            ("KUL","Kuala Lumpur International Airport","Kuala Lumpur","Selangor","MY",2.745578,101.709917),
            ("IST","Istanbul Airport","Istanbul","Istanbul","TR",41.276111,28.741944),
        ]

    if airports_to_insert:
        cursor.executemany(
            "INSERT IGNORE INTO airports (iata, name, city, state, country, lat, lon) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            airports_to_insert
        )
        conn.commit()
        print(f"Inserted {len(airports_to_insert)} airports into MySQL.")
    cursor.close()


# Auto-init on import
init_db()

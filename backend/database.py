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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            username      VARCHAR(100)  NOT NULL UNIQUE,
            password_hash VARCHAR(255)  NOT NULL,
            full_name     VARCHAR(150),
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            email         VARCHAR(200)  NOT NULL UNIQUE,
            password_hash VARCHAR(255)  NOT NULL,
            full_name     VARCHAR(150)  NOT NULL,
            phone         VARCHAR(20),
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    # Single-row config table — application code always reads/writes id=1.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pricing_settings (
            id                    INT PRIMARY KEY,
            ticket_markup_mode    VARCHAR(10) NOT NULL DEFAULT 'percent',
            ticket_markup_percent DOUBLE NOT NULL DEFAULT 0.0,
            ticket_markup_fixed   DOUBLE NOT NULL DEFAULT 0.0,
            seat_markup_mode      VARCHAR(10) NOT NULL DEFAULT 'percent',
            seat_markup_percent   DOUBLE NOT NULL DEFAULT 0.0,
            seat_markup_fixed     DOUBLE NOT NULL DEFAULT 0.0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cursor.execute("INSERT IGNORE INTO pricing_settings (id) VALUES (1)")

    # bookings.customer_id — added via a guarded ALTER since init_db() runs on
    # every import and MySQL's ADD COLUMN IF NOT EXISTS isn't universally available.
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME='bookings' AND COLUMN_NAME='customer_id'
    """, (MYSQL_DATABASE,))
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE bookings ADD COLUMN customer_id INT NULL AFTER passenger_email")
        cursor.execute("""
            ALTER TABLE bookings ADD CONSTRAINT fk_bookings_customer
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL
        """)

    # Index on passenger_email — the auto-link-by-email query (every customer
    # login) and admin email searches both filter on this column; at scale
    # (thousands of accounts/bookings) this keeps those lookups fast.
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME='bookings' AND INDEX_NAME='idx_bookings_passenger_email'
    """, (MYSQL_DATABASE,))
    if cursor.fetchone()[0] == 0:
        cursor.execute("CREATE INDEX idx_bookings_passenger_email ON bookings(passenger_email)")

    # ── Loyalty Program ──────────────────────────────────────────────────────
    # Single-row config table (same pattern as pricing_settings) — points
    # accrual rate per currency (fares are LKR or USD, never blended) and the
    # point thresholds that define each tier.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS loyalty_settings (
            id                     INT PRIMARY KEY,
            points_per_lkr         DOUBLE NOT NULL DEFAULT 0.01,
            points_per_usd         DOUBLE NOT NULL DEFAULT 1.0,
            tier_silver_threshold  INT NOT NULL DEFAULT 1000,
            tier_gold_threshold    INT NOT NULL DEFAULT 5000,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cursor.execute("INSERT IGNORE INTO loyalty_settings (id) VALUES (1)")

    # loyalty_settings.tier_platinum_threshold — added via a guarded ALTER,
    # same reasoning as bookings.customer_id above (init_db() runs on every
    # import; MySQL's ADD COLUMN IF NOT EXISTS isn't universally available).
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME='loyalty_settings' AND COLUMN_NAME='tier_platinum_threshold'
    """, (MYSQL_DATABASE,))
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE loyalty_settings ADD COLUMN tier_platinum_threshold INT NOT NULL DEFAULT 15000 AFTER tier_gold_threshold")

    # Loyalty balances are keyed by email, not customer_id — mirrors how
    # bookings are earned (by whichever email was on the booking) and let
    # into an account automatically once a customer signs up with that email,
    # with no separate "claiming" step: the account holder's own email IS
    # the lookup key when they view their own balance.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS loyalty_accounts (
            email          VARCHAR(200) PRIMARY KEY,
            points_balance INT NOT NULL DEFAULT 0,
            created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    # Ledger of every points change (booking accrual, admin manual
    # adjustment, or a merge during an email-change approval) — audit trail,
    # and what points_balance is derived from.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS loyalty_transactions (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            email         VARCHAR(200) NOT NULL,
            locator_code  VARCHAR(50) NULL,
            points_change INT NOT NULL,
            reason        VARCHAR(255) NOT NULL,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_loyalty_txn_email (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    # ── Email Change Requests ───────────────────────────────────────────────
    # Self-service email changes aren't safe here (no email verification
    # capability at all), so a customer's request goes into a queue an admin
    # must explicitly approve or reject before the account email changes.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_change_requests (
            id               INT AUTO_INCREMENT PRIMARY KEY,
            customer_id      INT NOT NULL,
            old_email        VARCHAR(200) NOT NULL,
            new_email        VARCHAR(200) NOT NULL,
            status           VARCHAR(20) NOT NULL DEFAULT 'pending',
            admin_note       VARCHAR(500),
            requested_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_at      DATETIME NULL,
            reviewed_by_admin_id INT NULL,
            CONSTRAINT fk_email_change_customer FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
            INDEX idx_email_change_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    # ── Cancellation Requests (B2C manual-review flow) ──────────────────────
    # Self-service Travelport cancellation is deliberately NOT exposed to B2C
    # customers for security reasons — a customer's request goes into this
    # queue instead, and an admin manually verifies + cancels it via the
    # existing Admin Portal tools. customer_id is nullable because the
    # request form is also reachable without signing in (booking locator +
    # email entered manually).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cancellation_requests (
            id                    INT AUTO_INCREMENT PRIMARY KEY,
            customer_id           INT NULL,
            booking_locator       VARCHAR(20) NOT NULL,
            travel_date           DATE NOT NULL,
            requester_name        VARCHAR(150) NOT NULL,
            email                 VARCHAR(200) NOT NULL,
            phone                 VARCHAR(30) NOT NULL,
            all_passengers_cancelling TINYINT(1) NOT NULL DEFAULT 1,
            status                VARCHAR(20) NOT NULL DEFAULT 'pending',
            admin_note            VARCHAR(500),
            requested_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_at           DATETIME NULL,
            reviewed_by_admin_id  INT NULL,
            CONSTRAINT fk_cancellation_request_customer FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
            INDEX idx_cancellation_request_status (status),
            INDEX idx_cancellation_request_locator (booking_locator)
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
                passenger_name, passenger_email, customer_id, passport_number,
                flight_number, airline, departure_airport, arrival_airport,
                departure_time, arrival_time, cabin_class, seat_number,
                total_fare, currency, booking_date, offer_id, raw_ticket_json,
                payment_method, fare_source
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                pnr=VALUES(pnr), ticket_number=VALUES(ticket_number),
                status=VALUES(status), passenger_name=VALUES(passenger_name),
                passenger_email=VALUES(passenger_email), customer_id=VALUES(customer_id),
                passport_number=VALUES(passport_number),
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
            ticket.get("customer_id"),
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


def _hydrate_bookings(rows: list[dict]) -> list[dict]:
    """Merge each row's raw_ticket_json (full ticket dict, incl. per-traveler
    detail) under the flat DB columns, which take precedence on conflict."""
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
    return _hydrate_bookings(rows)


def get_bookings_for_customer(customer_id: int) -> list[dict]:
    """Retrieve bookings tied to this customer_id — either stamped while the
    customer was logged in at booking time, or linked afterwards by
    link_guest_bookings_by_email() when a matching guest booking is found."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM bookings WHERE customer_id=%s ORDER BY booking_date DESC", (customer_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return _hydrate_bookings(rows)


def link_guest_bookings_by_email(customer_id: int, email: str) -> int:
    """Claim any unclaimed guest bookings (customer_id IS NULL) whose
    passenger_email matches this customer's account email. Called on every
    login so bookings made as a guest before — or between — logins get
    picked up automatically. Returns the number of bookings linked.

    NOTE: this system has no email verification, so this is a deliberate
    trust decision — anyone who can log into an account with a given email
    absorbs every guest booking made under that same email address."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE bookings SET customer_id=%s WHERE customer_id IS NULL AND passenger_email=%s",
            (customer_id, email),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        cursor.close()
        conn.close()


def assign_booking_to_customer(locator_code: str, customer_id: int) -> dict | None:
    """Admin-only manual override: link a specific booking to a specific
    customer account regardless of email match — for cases where the
    booking was made under a different email than the account (typo,
    alternate address, etc.) and auto-linking can't catch it. Overwrites
    any existing customer_id on the booking. Returns the updated booking,
    or None if the locator code doesn't exist."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("UPDATE bookings SET customer_id=%s WHERE locator_code=%s", (customer_id, locator_code))
        conn.commit()
        cursor.execute("SELECT * FROM bookings WHERE locator_code=%s", (locator_code,))
        row = cursor.fetchone()
        return _hydrate_bookings([row])[0] if row else None
    finally:
        cursor.close()
        conn.close()


# ── Loyalty Program ──────────────────────────────────────────────────────────

def get_loyalty_settings() -> dict:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM loyalty_settings WHERE id=1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row or {
        "points_per_lkr": 0.01, "points_per_usd": 1.0,
        "tier_silver_threshold": 1000, "tier_gold_threshold": 5000, "tier_platinum_threshold": 15000,
    }


def update_loyalty_settings(points_per_lkr: float, points_per_usd: float,
                             tier_silver_threshold: int, tier_gold_threshold: int,
                             tier_platinum_threshold: int) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """UPDATE loyalty_settings SET points_per_lkr=%s, points_per_usd=%s,
               tier_silver_threshold=%s, tier_gold_threshold=%s, tier_platinum_threshold=%s WHERE id=1""",
            (points_per_lkr, points_per_usd, tier_silver_threshold, tier_gold_threshold, tier_platinum_threshold),
        )
        conn.commit()
        cursor.execute("SELECT * FROM loyalty_settings WHERE id=1")
        return cursor.fetchone()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def award_loyalty_points(email: str, points: int, locator_code: str | None, reason: str) -> int:
    """Record a points change and update the running balance. points may be
    negative (e.g. a future redemption or a correction). Returns the new
    balance. No-ops (returns current balance) if points == 0."""
    if not email:
        return 0
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        if points != 0:
            cursor.execute(
                "INSERT INTO loyalty_transactions (email, locator_code, points_change, reason) VALUES (%s,%s,%s,%s)",
                (email, locator_code, points, reason),
            )
            cursor.execute(
                """INSERT INTO loyalty_accounts (email, points_balance) VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE points_balance = points_balance + VALUES(points_balance)""",
                (email, points),
            )
            conn.commit()
        cursor.execute("SELECT points_balance FROM loyalty_accounts WHERE email=%s", (email,))
        row = cursor.fetchone()
        return row["points_balance"] if row else 0
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_loyalty_account(email: str) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM loyalty_accounts WHERE email=%s", (email,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row or {"email": email, "points_balance": 0}


def get_loyalty_transactions(email: str, limit: int = 25) -> list[dict]:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM loyalty_transactions WHERE email=%s ORDER BY created_at DESC LIMIT %s",
        (email, limit),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def rekey_loyalty_account(old_email: str, new_email: str) -> None:
    """Move a loyalty balance from old_email to new_email — used when an
    email-change request is approved. Merges into an existing new_email
    account if one already exists (e.g. from guest bookings/points already
    earned under that address), rather than overwriting it."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT points_balance FROM loyalty_accounts WHERE email=%s", (old_email,))
        old_row = cursor.fetchone()
        if not old_row:
            return
        cursor.execute(
            """INSERT INTO loyalty_accounts (email, points_balance) VALUES (%s, %s)
               ON DUPLICATE KEY UPDATE points_balance = points_balance + VALUES(points_balance)""",
            (new_email, old_row["points_balance"]),
        )
        cursor.execute("DELETE FROM loyalty_accounts WHERE email=%s", (old_email,))
        cursor.execute("UPDATE loyalty_transactions SET email=%s WHERE email=%s", (new_email, old_email))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


# ── Email Change Requests ─────────────────────────────────────────────────────

def create_email_change_request(customer_id: int, old_email: str, new_email: str) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "INSERT INTO email_change_requests (customer_id, old_email, new_email) VALUES (%s,%s,%s)",
            (customer_id, old_email, new_email),
        )
        conn.commit()
        cursor.execute("SELECT * FROM email_change_requests WHERE id=%s", (cursor.lastrowid,))
        return cursor.fetchone()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_pending_email_change_request(customer_id: int) -> dict | None:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM email_change_requests WHERE customer_id=%s AND status='pending' ORDER BY requested_at DESC LIMIT 1",
        (customer_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def get_email_change_requests(status: str | None = None) -> list[dict]:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if status:
        cursor.execute("SELECT * FROM email_change_requests WHERE status=%s ORDER BY requested_at DESC", (status,))
    else:
        cursor.execute("SELECT * FROM email_change_requests ORDER BY requested_at DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_email_change_request_by_id(request_id: int) -> dict | None:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM email_change_requests WHERE id=%s", (request_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def resolve_email_change_request(request_id: int, status: str, admin_id: int, admin_note: str | None) -> dict:
    """status must be 'approved' or 'rejected'."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """UPDATE email_change_requests SET status=%s, admin_note=%s,
               reviewed_at=CURRENT_TIMESTAMP, reviewed_by_admin_id=%s WHERE id=%s""",
            (status, admin_note, admin_id, request_id),
        )
        conn.commit()
        cursor.execute("SELECT * FROM email_change_requests WHERE id=%s", (request_id,))
        return cursor.fetchone()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def update_customer_email(customer_id: int, new_email: str) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("UPDATE customers SET email=%s WHERE id=%s", (new_email, customer_id))
        conn.commit()
        cursor.execute("SELECT * FROM customers WHERE id=%s", (customer_id,))
        return cursor.fetchone()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_booking_by_locator(locator_code: str) -> dict | None:
    """Retrieve a single cached booking by PNR locator code."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM bookings WHERE locator_code=%s",(locator_code,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return _hydrate_bookings([row])[0]
    return None


def get_booking_by_ticket_number(ticket_number: str) -> dict | None:
    """Resolve a booking by its issued ticket number (used by invoice lookup
    to let a caller search by ticket number instead of PNR/locator)."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM bookings WHERE ticket_number=%s", (ticket_number,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return _hydrate_bookings([row])[0]
    return None


def create_cancellation_request(
    customer_id: int | None, booking_locator: str, travel_date: str,
    requester_name: str, email: str, phone: str, all_passengers_cancelling: bool,
) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """INSERT INTO cancellation_requests
               (customer_id, booking_locator, travel_date, requester_name, email, phone, all_passengers_cancelling)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (customer_id, booking_locator, travel_date, requester_name, email, phone, all_passengers_cancelling),
        )
        conn.commit()
        cursor.execute("SELECT * FROM cancellation_requests WHERE id=%s", (cursor.lastrowid,))
        return cursor.fetchone()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_cancellation_requests(status: str | None = None) -> list[dict]:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if status:
        cursor.execute("SELECT * FROM cancellation_requests WHERE status=%s ORDER BY requested_at DESC", (status,))
    else:
        cursor.execute("SELECT * FROM cancellation_requests ORDER BY requested_at DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_cancellation_request_by_id(request_id: int) -> dict | None:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM cancellation_requests WHERE id=%s", (request_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def resolve_cancellation_request(request_id: int, status: str, admin_id: int, admin_note: str | None) -> dict:
    """status must be 'resolved' or 'rejected'."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """UPDATE cancellation_requests SET status=%s, admin_note=%s,
               reviewed_at=CURRENT_TIMESTAMP, reviewed_by_admin_id=%s WHERE id=%s""",
            (status, admin_note, admin_id, request_id),
        )
        conn.commit()
        cursor.execute("SELECT * FROM cancellation_requests WHERE id=%s", (request_id,))
        return cursor.fetchone()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


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


# ---------------------------------------------------------------------------
# Admin accounts
# ---------------------------------------------------------------------------

def create_admin_if_not_exists(username: str, password_hash: str, full_name: str | None = None) -> bool:
    """Insert the admin row only if the username doesn't already exist.
    Returns True if a new row was created, False if it already existed."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM admins WHERE username=%s", (username,))
        if cursor.fetchone():
            return False
        cursor.execute(
            "INSERT INTO admins (username, password_hash, full_name) VALUES (%s,%s,%s)",
            (username, password_hash, full_name),
        )
        conn.commit()
        return True
    finally:
        cursor.close()
        conn.close()


def get_admin_by_username(username: str) -> dict | None:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM admins WHERE username=%s", (username,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Customer accounts
# ---------------------------------------------------------------------------

def create_customer(email: str, password_hash: str, full_name: str, phone: str | None = None) -> dict:
    """Create a customer account. Raises mysql.connector.IntegrityError on
    duplicate email (caller maps this to HTTP 409)."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "INSERT INTO customers (email, password_hash, full_name, phone) VALUES (%s,%s,%s,%s)",
            (email, password_hash, full_name, phone),
        )
        conn.commit()
        cursor.execute("SELECT * FROM customers WHERE id=%s", (cursor.lastrowid,))
        return cursor.fetchone()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


def get_customer_by_email(email: str) -> dict | None:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM customers WHERE email=%s", (email,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Pricing / markup settings (single row, id=1)
# ---------------------------------------------------------------------------

def get_pricing_settings() -> dict:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM pricing_settings WHERE id=1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return row
    return {
        "ticket_markup_mode": "percent", "ticket_markup_percent": 0.0, "ticket_markup_fixed": 0.0,
        "seat_markup_mode": "percent", "seat_markup_percent": 0.0, "seat_markup_fixed": 0.0,
    }


def update_pricing_settings(
    ticket_markup_mode: str, ticket_markup_percent: float, ticket_markup_fixed: float,
    seat_markup_mode: str, seat_markup_percent: float, seat_markup_fixed: float,
) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """UPDATE pricing_settings SET
                ticket_markup_mode=%s, ticket_markup_percent=%s, ticket_markup_fixed=%s,
                seat_markup_mode=%s, seat_markup_percent=%s, seat_markup_fixed=%s
               WHERE id=1""",
            (ticket_markup_mode, ticket_markup_percent, ticket_markup_fixed,
             seat_markup_mode, seat_markup_percent, seat_markup_fixed),
        )
        conn.commit()
        cursor.execute("SELECT * FROM pricing_settings WHERE id=1")
        return cursor.fetchone()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# Admin reporting
# ---------------------------------------------------------------------------

def get_sales_summary(start_date: str | None = None, end_date: str | None = None) -> dict:
    """KPI totals (total sales, tickets issued, bookings) grouped by currency,
    optionally filtered to a booking_date range. Cancelled bookings are
    counted separately via total_cancelled, not silently excluded."""
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
                       SUM(CASE WHEN status='Cancelled' THEN 0 ELSE total_fare END) AS total_sales,
                       SUM(CASE WHEN ticket_number IS NOT NULL AND ticket_number != '' THEN 1 ELSE 0 END) AS ticket_count
                FROM bookings {clause}
                GROUP BY currency""",
            tuple(params),
        )
        by_currency = cursor.fetchall()

        cursor.execute(
            f"""SELECT COUNT(*) AS total_bookings,
                       SUM(CASE WHEN status='Cancelled' THEN 1 ELSE 0 END) AS total_cancelled
                FROM bookings {clause}""",
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

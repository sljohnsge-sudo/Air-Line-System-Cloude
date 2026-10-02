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


def list_bookings():
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, order_id, origin, destination, departure_date, return_date, "
                "passenger_name, passenger_email, status, created_at FROM bookings ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

"""
Customer profile (11/11/2026): saved travellers + other details the customer
keeps up to date themselves.

Saved travellers are filled automatically from what the customer typed when
booking (name, date of birth, gender, nationality, passport number / expiry /
issuing country, mobile, email) — every booking made while signed in, and the
customer's earlier bookings the first time the profile is opened. The customer
can edit, add or remove them. Other details: NIC / national ID, address,
emergency contact, meal and seat preference, frequent-flyer numbers, visas.

The account's own email / mobile are NOT changed here — that needs a one-time
code (services/contact_verification.py). Only the signed-in customer sees
their profile. Own tables, created on first use: customer_travellers,
customer_profile_details. Saving from a booking never raises.
"""
import json
import logging
import re
import threading
from datetime import datetime

import database

logger = logging.getLogger(__name__)

TRAVELLER_FIELDS = ("first_name", "last_name", "date_of_birth", "gender", "nationality", "passport_number",
                    "passport_expiry", "passport_issue_country", "phone", "email", "passenger_type")
DETAIL_FIELDS = ("nic_number", "address", "city", "country", "emergency_name", "emergency_phone",
                 "meal_preference", "seat_preference", "notes")
MAX_TRAVELLERS = 20

_ready = False
_lock = threading.Lock()


class ProfileError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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
                CREATE TABLE IF NOT EXISTS customer_travellers (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    customer_id INT NOT NULL,
                    first_name VARCHAR(50) NOT NULL,
                    last_name VARCHAR(50) NOT NULL,
                    date_of_birth VARCHAR(10) NULL,
                    gender VARCHAR(10) NULL,
                    nationality VARCHAR(3) NULL,
                    passport_number VARCHAR(20) NULL,
                    passport_expiry VARCHAR(10) NULL,
                    passport_issue_country VARCHAR(3) NULL,
                    phone VARCHAR(20) NULL,
                    email VARCHAR(190) NULL,
                    passenger_type VARCHAR(3) NULL,
                    source VARCHAR(10) NOT NULL DEFAULT 'booking',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_ct_customer (customer_id)
                )""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customer_profile_details (
                    customer_id INT PRIMARY KEY,
                    nic_number VARCHAR(20) NULL,
                    address VARCHAR(255) NULL,
                    city VARCHAR(80) NULL,
                    country VARCHAR(60) NULL,
                    emergency_name VARCHAR(120) NULL,
                    emergency_phone VARCHAR(20) NULL,
                    meal_preference VARCHAR(40) NULL,
                    seat_preference VARCHAR(20) NULL,
                    frequent_flyer_json TEXT NULL,
                    visas_json TEXT NULL,
                    notes VARCHAR(500) NULL,
                    backfilled TINYINT(1) NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
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
            return cur.lastrowid if one else cur.rowcount
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows
    finally:
        cur.close()
        conn.close()


def _clean_traveller(t: dict) -> dict:
    out = {}
    for f in TRAVELLER_FIELDS:
        v = t.get(f)
        v = str(v).strip() if v not in (None, "") else None
        if v and f in ("nationality", "passport_issue_country"):
            v = v.upper()[:3]
        if v and f == "passport_number":
            v = re.sub(r"\s", "", v).upper()[:20]
        if v and f in ("first_name", "last_name"):
            v = v[:50]
        out[f] = v
    return out


def _date_ok(v):
    if not v:
        return True
    try:
        datetime.strptime(v, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _validate(t: dict) -> dict:
    t = _clean_traveller(t)
    if not t["first_name"] or not t["last_name"] or len(t["first_name"]) < 2 or len(t["last_name"]) < 2:
        raise ProfileError("First and last name are needed (at least 2 letters each), as in the passport.")
    for f in ("date_of_birth", "passport_expiry"):
        if not _date_ok(t[f]):
            raise ProfileError("Dates must be in the form YYYY-MM-DD.")
    if t["gender"] and t["gender"] not in ("Male", "Female"):
        raise ProfileError("Gender must be Male or Female (as in the passport).")
    if t["passport_number"] and not re.fullmatch(r"[A-Z0-9]{5,20}", t["passport_number"]):
        raise ProfileError("Passport number: 5–20 letters or numbers, no spaces.")
    if t["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", t["email"]):
        raise ProfileError("Please enter a valid email address.")
    if t["passenger_type"] and t["passenger_type"] not in ("ADT", "CNN", "INF", "STU"):
        t["passenger_type"] = None
    return t


def _find_same(customer_id: int, t: dict):
    return _q("""SELECT id FROM customer_travellers WHERE customer_id=%s AND LOWER(first_name)=LOWER(%s)
                 AND LOWER(last_name)=LOWER(%s) AND (date_of_birth <=> %s OR date_of_birth IS NULL OR %s IS NULL)
                 ORDER BY id LIMIT 1""",
              (customer_id, t["first_name"], t["last_name"], t["date_of_birth"], t["date_of_birth"]), one=True)


def _upsert_from_booking(customer_id: int, t: dict) -> None:
    t = _clean_traveller(t)
    if not t["first_name"] or not t["last_name"]:
        return
    same = _find_same(customer_id, t)
    if same:
        # Newer booking details fill in / refresh what was saved (e.g. a renewed passport).
        sets = [f"{f}=COALESCE(%s, {f})" for f in TRAVELLER_FIELDS if f not in ("first_name", "last_name")]
        _q(f"UPDATE customer_travellers SET {', '.join(sets)} WHERE id=%s",
           (*[t[f] for f in TRAVELLER_FIELDS if f not in ("first_name", "last_name")], same["id"]), write=True)
    else:
        count = _q("SELECT COUNT(*) n FROM customer_travellers WHERE customer_id=%s", (customer_id,), one=True)["n"]
        if count >= MAX_TRAVELLERS:
            return
        cols = ", ".join(TRAVELLER_FIELDS)
        _q(f"INSERT INTO customer_travellers (customer_id, {cols}, source) VALUES (%s, {', '.join(['%s'] * len(TRAVELLER_FIELDS))}, 'booking')",
           (customer_id, *[t[f] for f in TRAVELLER_FIELDS]), write=True)


def save_from_booking(customer_id, travelers: list) -> None:
    """Called after a signed-in customer's booking is made. Never raises."""
    if not customer_id or not travelers:
        return
    try:
        for t in travelers:
            _upsert_from_booking(int(customer_id), t if isinstance(t, dict) else dict(t))
    except Exception as e:
        logger.warning(f"Customer {customer_id}: travellers not saved to profile: {e}")


def _backfill(customer_id: int) -> None:
    """First time the profile opens: travellers from the customer's earlier bookings."""
    row = _q("SELECT backfilled FROM customer_profile_details WHERE customer_id=%s", (customer_id,), one=True)
    if row and row["backfilled"]:
        return
    try:
        bookings = _q("SELECT raw_ticket_json FROM bookings WHERE customer_id=%s ORDER BY id", (customer_id,))
        for b in bookings:
            try:
                raw = json.loads(b["raw_ticket_json"] or "{}") or {}
            except ValueError:
                continue
            for t in raw.get("travelers") or []:
                if isinstance(t, dict) and t.get("first_name") and t.get("last_name"):
                    _upsert_from_booking(customer_id, t)
    except Exception as e:
        logger.warning(f"Customer {customer_id}: earlier bookings not read for the profile: {e}")
    _q("""INSERT INTO customer_profile_details (customer_id, backfilled) VALUES (%s, 1)
          ON DUPLICATE KEY UPDATE backfilled=1""", (customer_id,), write=True)


def _list_json(v):
    try:
        x = json.loads(v or "[]")
        return x if isinstance(x, list) else []
    except ValueError:
        return []


def get(customer_id: int) -> dict:
    _ensure_tables()
    _backfill(customer_id)
    travellers = _q(f"SELECT id, {', '.join(TRAVELLER_FIELDS)}, source, updated_at FROM customer_travellers "
                    f"WHERE customer_id=%s ORDER BY id", (customer_id,))
    for t in travellers:
        t["updated_at"] = t["updated_at"].isoformat(sep=" ") if t.get("updated_at") else None
    d = _q("SELECT * FROM customer_profile_details WHERE customer_id=%s", (customer_id,), one=True) or {}
    details = {f: d.get(f) or "" for f in DETAIL_FIELDS}
    details["frequent_flyer"] = _list_json(d.get("frequent_flyer_json"))
    details["visas"] = _list_json(d.get("visas_json"))
    return {"travellers": travellers, "details": details}


def add_traveller(customer_id: int, t: dict) -> dict:
    t = _validate(t)
    count = _q("SELECT COUNT(*) n FROM customer_travellers WHERE customer_id=%s", (customer_id,), one=True)["n"]
    if count >= MAX_TRAVELLERS:
        raise ProfileError(f"You can save up to {MAX_TRAVELLERS} travellers.")
    cols = ", ".join(TRAVELLER_FIELDS)
    _q(f"INSERT INTO customer_travellers (customer_id, {cols}, source) VALUES (%s, {', '.join(['%s'] * len(TRAVELLER_FIELDS))}, 'customer')",
       (customer_id, *[t[f] for f in TRAVELLER_FIELDS]), write=True)
    return get(customer_id)


def update_traveller(customer_id: int, traveller_id: int, t: dict) -> dict:
    t = _validate(t)
    n = _q(f"UPDATE customer_travellers SET {', '.join(f'{f}=%s' for f in TRAVELLER_FIELDS)}, source='customer' "
           f"WHERE id=%s AND customer_id=%s", (*[t[f] for f in TRAVELLER_FIELDS], traveller_id, customer_id), write=True)
    if not n and not _q("SELECT id FROM customer_travellers WHERE id=%s AND customer_id=%s", (traveller_id, customer_id), one=True):
        raise ProfileError("Traveller not found.", 404)
    return get(customer_id)


def delete_traveller(customer_id: int, traveller_id: int) -> dict:
    _q("DELETE FROM customer_travellers WHERE id=%s AND customer_id=%s", (traveller_id, customer_id), write=True)
    return get(customer_id)


def update_details(customer_id: int, values: dict) -> dict:
    clean = {}
    limits = {"nic_number": 20, "address": 255, "city": 80, "country": 60, "emergency_name": 120,
              "emergency_phone": 20, "meal_preference": 40, "seat_preference": 20, "notes": 500}
    for f in DETAIL_FIELDS:
        v = values.get(f)
        clean[f] = (str(v).strip()[:limits[f]] or None) if v not in (None, "") else None
    ff = []
    for x in (values.get("frequent_flyer") or [])[:10]:
        airline, number = str(x.get("airline") or "").strip()[:40], str(x.get("number") or "").strip()[:30]
        if airline and number:
            ff.append({"airline": airline, "number": number})
    visas = []
    for x in (values.get("visas") or [])[:10]:
        country = str(x.get("country") or "").strip()[:60]
        number = str(x.get("number") or "").strip()[:30]
        expiry = str(x.get("expiry") or "").strip()[:10]
        if country and _date_ok(expiry):
            visas.append({"country": country, "number": number, "expiry": expiry, "type": str(x.get("type") or "").strip()[:40]})
    _ensure_tables()
    cols = list(clean) + ["frequent_flyer_json", "visas_json"]
    vals = list(clean.values()) + [json.dumps(ff), json.dumps(visas)]
    _q(f"INSERT INTO customer_profile_details (customer_id, {', '.join(cols)}) VALUES (%s, {', '.join(['%s'] * len(cols))}) "
       f"ON DUPLICATE KEY UPDATE {', '.join(f'{c}=VALUES({c})' for c in cols)}", (customer_id, *vals), write=True)
    return get(customer_id)

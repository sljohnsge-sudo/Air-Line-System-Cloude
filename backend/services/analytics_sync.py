"""
Analytics database for the Tick and Book business dashboard (/analytics).

A separate MySQL schema (ANALYTICS_DATABASE, default "tickbook_analytics") on
the same server, built from the main database by a full rebuild every
ANALYTICS_SYNC_MINUTES (default 30, 0 = off) and on demand from the
dashboard / Admin Console. The main database is only ever READ here: nothing
in it is altered or written.

If the MySQL user may not create a database, the same tables are kept in the
main database instead, each prefixed "an_" (new tables of their own — still
no existing table is touched). storage_info() says which one is in use.

Tables (created on first use):
  customers_360   one row per customer email: bookings, spend per currency,
                  first/last booking, last/next trip, airlines, extras,
                  searches, hotel/visa/package requests (phone masked, no
                  passport data at all)
  booking_facts   one row per booking: airline, route, airports + countries,
                  GDS/NDC, fare, status, booking date, payment method
  extras_facts    one row per seat/extra item: booked with the ticket
                  (addon_results) or bought after ticketing (addon_payments)
  search_facts    one row per customer flight search (search_log, kind
                  "search" only — no IP or browser details)
  vertical_facts  hotel bookings, visa consultations, tour package requests
  sync_runs       when each rebuild ran and what it built
  analytics_users the dashboard's own sign-in accounts (analytics_service)

Only real rows are copied — no sample or made-up data.
"""
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime

import database

logger = logging.getLogger(__name__)

_DB_NAME = os.getenv("ANALYTICS_DATABASE", "tickbook_analytics").strip() or "tickbook_analytics"
if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", _DB_NAME):
    _DB_NAME = "tickbook_analytics"

_state = {"mode": None, "note": None}     # mode: "schema" | "prefix"
_resolve_lock = threading.Lock()
_sync_lock = threading.Lock()

_TABLES = {
    "sync_runs": """
        id INT AUTO_INCREMENT PRIMARY KEY,
        started_at DATETIME NOT NULL,
        finished_at DATETIME NULL,
        ok TINYINT(1) NOT NULL DEFAULT 0,
        triggered_by VARCHAR(100) NULL,
        counts_json TEXT NULL,
        error VARCHAR(1000) NULL,
        KEY idx_started (started_at)""",
    "customers_360": """
        email VARCHAR(200) NOT NULL PRIMARY KEY,
        customer_id INT NULL,
        full_name VARCHAR(150) NULL,
        phone_masked VARCHAR(40) NULL,
        has_account TINYINT(1) NOT NULL DEFAULT 0,
        account_since DATETIME NULL,
        bookings INT NOT NULL DEFAULT 0,
        ticketed INT NOT NULL DEFAULT 0,
        cancelled INT NOT NULL DEFAULT 0,
        spend_json TEXT NULL,
        spend_lkr DOUBLE NOT NULL DEFAULT 0,
        first_booking DATETIME NULL,
        last_booking DATETIME NULL,
        last_trip_route VARCHAR(20) NULL,
        last_trip_date DATE NULL,
        next_trip_date DATE NULL,
        airlines_json TEXT NULL,
        extras_items INT NOT NULL DEFAULT 0,
        extras_spend_json TEXT NULL,
        searches INT NOT NULL DEFAULT 0,
        last_search DATETIME NULL,
        hotel_requests INT NOT NULL DEFAULT 0,
        visa_requests INT NOT NULL DEFAULT 0,
        package_requests INT NOT NULL DEFAULT 0,
        loyalty_points INT NULL,
        synced_at DATETIME NOT NULL,
        KEY idx_name (full_name)""",
    "booking_facts": """
        booking_id INT NOT NULL PRIMARY KEY,
        locator_code VARCHAR(20) NOT NULL,
        customer_email VARCHAR(200) NULL,
        customer_id INT NULL,
        passenger_name VARCHAR(150) NULL,
        pax_count INT NOT NULL DEFAULT 1,
        airline_code VARCHAR(3) NULL,
        airline_name VARCHAR(100) NULL,
        flight_number VARCHAR(20) NULL,
        origin VARCHAR(3) NULL,
        destination VARCHAR(3) NULL,
        origin_city VARCHAR(100) NULL,
        destination_city VARCHAR(100) NULL,
        origin_country VARCHAR(10) NULL,
        destination_country VARCHAR(10) NULL,
        trip_type VARCHAR(12) NULL,
        segment_count INT NOT NULL DEFAULT 1,
        cabin_class VARCHAR(30) NULL,
        fare_source VARCHAR(10) NULL,
        total_fare DOUBLE NOT NULL DEFAULT 0,
        currency VARCHAR(5) NULL,
        status VARCHAR(30) NULL,
        is_ticketed TINYINT(1) NOT NULL DEFAULT 0,
        ever_ticketed TINYINT(1) NOT NULL DEFAULT 0,
        refund_amount DOUBLE NULL,
        booking_date DATETIME NULL,
        departure_at DATETIME NULL,
        payment_method VARCHAR(50) NULL,
        KEY idx_email (customer_email),
        KEY idx_airline (airline_code),
        KEY idx_dest (destination),
        KEY idx_date (booking_date)""",
    "extras_facts": """
        id INT AUTO_INCREMENT PRIMARY KEY,
        source VARCHAR(12) NOT NULL,
        ref_id VARCHAR(64) NULL,
        locator_code VARCHAR(20) NULL,
        customer_email VARCHAR(200) NULL,
        airline_code VARCHAR(3) NULL,
        airline_name VARCHAR(100) NULL,
        kind VARCHAR(10) NOT NULL,
        item_name VARCHAR(200) NULL,
        amount DOUBLE NOT NULL DEFAULT 0,
        currency VARCHAR(10) NULL,
        outcome VARCHAR(12) NOT NULL,
        raw_status VARCHAR(30) NULL,
        created_at DATETIME NULL,
        KEY idx_locator (locator_code),
        KEY idx_airline (airline_code)""",
    "search_facts": """
        id BIGINT NOT NULL PRIMARY KEY,
        searched_at DATETIME NOT NULL,
        origin VARCHAR(3) NULL,
        destination VARCHAR(3) NULL,
        origin_country VARCHAR(10) NULL,
        destination_country VARCHAR(10) NULL,
        destination_city VARCHAR(100) NULL,
        trip_type VARCHAR(12) NULL,
        pax INT NOT NULL DEFAULT 1,
        cabin VARCHAR(20) NULL,
        result_count INT NULL,
        cheapest_price DOUBLE NULL,
        cheapest_currency VARCHAR(5) NULL,
        cheapest_airline VARCHAR(100) NULL,
        has_error TINYINT(1) NOT NULL DEFAULT 0,
        customer_id INT NULL,
        KEY idx_route (origin, destination),
        KEY idx_at (searched_at)""",
    "vertical_facts": """
        id INT AUTO_INCREMENT PRIMARY KEY,
        vertical VARCHAR(10) NOT NULL,
        source_id INT NOT NULL,
        status VARCHAR(30) NULL,
        customer_email VARCHAR(200) NULL,
        customer_name VARCHAR(150) NULL,
        title VARCHAR(200) NULL,
        place VARCHAR(150) NULL,
        travellers INT NULL,
        amount DOUBLE NULL,
        currency VARCHAR(10) NULL,
        event_date DATE NULL,
        requested_at DATETIME NULL,
        KEY idx_vertical (vertical),
        KEY idx_email (customer_email)""",
    "analytics_users": """
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(100) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        full_name VARCHAR(150) NULL,
        active TINYINT(1) NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_by VARCHAR(100) NULL,
        updated_at DATETIME NULL,
        updated_by VARCHAR(100) NULL,
        last_login_at DATETIME NULL""",
}


# ── Where the analytics tables live ───────────────────────────────────────────

def _exec_ddl(sql: str) -> None:
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _create_tables() -> None:
    for name, cols in _TABLES.items():
        _exec_ddl(f"CREATE TABLE IF NOT EXISTS {t(name)} ({cols}\n) "
                  "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci")


def ensure_storage() -> str:
    """Creates the analytics schema (or the an_ tables) once. Returns the mode."""
    if _state.get("ready"):
        return _state["mode"]
    with _resolve_lock:
        if _state.get("ready"):
            return _state["mode"]
        try:
            _exec_ddl(f"CREATE DATABASE IF NOT EXISTS `{_DB_NAME}` "
                      "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            _state["mode"] = "schema"
            _create_tables()
            _state["note"] = f"Separate database `{_DB_NAME}`"
        except Exception as e:
            logger.warning(f"Analytics: separate database `{_DB_NAME}` not available ({e}) - "
                           f"using an_ tables in the main database instead")
            _state["mode"] = "prefix"
            _create_tables()
            _state["note"] = f"Tables prefixed an_ in the main database ({database.MYSQL_DATABASE}) - " \
                             f"the MySQL user may not create `{_DB_NAME}`"
        _state["ready"] = True
        logger.info(f"Analytics storage: {_state['note']}")
        return _state["mode"]


def t(name: str) -> str:
    """Fully qualified analytics table name."""
    if _state["mode"] == "prefix":
        return f"`an_{name}`"
    return f"`{_DB_NAME}`.`{name}`"


def storage_info() -> dict:
    ensure_storage()
    return {"mode": _state["mode"], "database": _DB_NAME if _state["mode"] == "schema" else database.MYSQL_DATABASE,
            "note": _state["note"]}


def q(sql: str, params: tuple = (), *, one: bool = False):
    """Read query (analytics or main tables)."""
    ensure_storage()
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()
    finally:
        cur.close()
        conn.close()


def write(sql: str, params: tuple = ()) -> int:
    """Write to an analytics table only (callers pass t(...) names)."""
    ensure_storage()
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid or cur.rowcount
    finally:
        cur.close()
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _main_rows(sql: str, params: tuple = ()) -> list[dict]:
    """Read from the main database; a table that doesn't exist reads as empty."""
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception as e:
        if "1146" in str(e) or "doesn't exist" in str(e):
            return []
        raise
    finally:
        cur.close()
        conn.close()


def _json(text, default):
    if not text:
        return default
    try:
        v = json.loads(text)
        return v if v is not None else default
    except (ValueError, TypeError):
        return default


def _dt(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = str(v or "").strip().replace("T", " ")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def mask_phone(phone) -> str | None:
    """Only the last 3 digits are shown."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) < 4:
        return None
    return "•" * (len(digits) - 3) + digits[-3:]


def _cut(v, n):
    if v is None:
        return None
    s = str(v)
    return s[:n]


def _email(v) -> str:
    return (v or "").strip().lower()


# ── Rebuild ───────────────────────────────────────────────────────────────────

def _airports() -> dict:
    return {r["iata"]: r for r in _main_rows("SELECT iata, city, country FROM airports")}


def _build_bookings(ap: dict) -> tuple[list[tuple], list[dict]]:
    rows = _main_rows("""SELECT id, locator_code, ticket_number, status, passenger_name, passenger_email, customer_id,
                                flight_number, airline, departure_airport, arrival_airport, departure_time,
                                cabin_class, total_fare, currency, booking_date, raw_ticket_json, payment_method,
                                fare_source, refund_amount, created_at
                         FROM bookings ORDER BY id""")
    facts, enriched = [], []
    for r in rows:
        raw = _json(r.get("raw_ticket_json"), {})
        if not isinstance(raw, dict):
            raw = {}
        legs = raw.get("legs") if isinstance(raw.get("legs"), list) else []
        first = legs[0] if legs and isinstance(legs[0], dict) else {}
        origin = (first.get("departure_airport") or r.get("departure_airport") or "").upper()[:3] or None
        dest = (first.get("arrival_airport") or r.get("arrival_airport") or "").upper()[:3] or None
        segs = sum(len(l.get("segments") or []) for l in legs if isinstance(l, dict)) or \
            len(raw.get("segments") or []) or 1
        trip = "oneway" if len(legs) <= 1 else ("return" if len(legs) == 2 and
                                                  (legs[1] or {}).get("arrival_airport") == origin else "multicity")
        code = (raw.get("airline_code") or first.get("airline_code") or
                re.sub(r"[^A-Z0-9]", "", (r.get("flight_number") or "").upper())[:2] or None)
        travelers = raw.get("travelers") if isinstance(raw.get("travelers"), list) else []
        status = r.get("status") or ""
        booked = _dt(r.get("booking_date")) or r.get("created_at")
        o, d = ap.get(origin) or {}, ap.get(dest) or {}
        fact = {
            "booking_id": r["id"], "locator_code": r["locator_code"], "customer_email": _email(r.get("passenger_email")) or None,
            "customer_id": r.get("customer_id"), "passenger_name": _cut(r.get("passenger_name"), 150),
            "pax_count": max(1, len(travelers)), "airline_code": _cut(code, 3), "airline_name": _cut(r.get("airline"), 100),
            "flight_number": _cut(r.get("flight_number"), 20), "origin": origin, "destination": dest,
            "origin_city": _cut(o.get("city"), 100), "destination_city": _cut(d.get("city"), 100),
            "origin_country": _cut(o.get("country"), 10), "destination_country": _cut(d.get("country"), 10),
            "trip_type": trip, "segment_count": segs, "cabin_class": _cut(r.get("cabin_class"), 30),
            "fare_source": _cut(r.get("fare_source") or "GDS", 10), "total_fare": _num(r.get("total_fare")),
            "currency": _cut(r.get("currency"), 5), "status": _cut(status, 30),
            "is_ticketed": 1 if status.lower() == "ticketed" else 0,
            "ever_ticketed": 1 if (r.get("ticket_number") or "").strip() or status.lower() == "ticketed" else 0,
            "refund_amount": r.get("refund_amount"), "booking_date": booked,
            "departure_at": _dt(first.get("departure_time") or r.get("departure_time")),
            "payment_method": _cut(r.get("payment_method"), 50),
        }
        facts.append(fact)
        enriched.append({**fact, "phone": raw.get("phone"), "addon_results": raw.get("addon_results")})
    return facts, enriched


_PAYMENT_OUTCOME = {"completed": "paid", "emd_failed": "failed", "awaiting_airline": "open",
                    "awaiting_payment": "open", "pending": "open", "expired": "not_sold", "superseded": "not_sold"}


def _build_extras(bookings: list[dict]) -> list[dict]:
    by_loc = {b["locator_code"]: b for b in bookings}
    out = []
    # Seats/extras chosen in the booking flow and charged with the ticket.
    for b in bookings:
        ar = b.get("addon_results")
        if not isinstance(ar, dict):
            continue
        for group, kind in (("seats", "seat"), ("extras", "extra")):
            for it in ar.get(group) or []:
                if not isinstance(it, dict):
                    continue
                st = (it.get("status") or "").lower()
                if st == "confirmed":
                    outcome = "paid" if b["ever_ticketed"] else "open"
                elif st in ("pending", "requested"):
                    outcome = "open"
                else:
                    outcome = "failed"
                name = it.get("name") or it.get("description") or it.get("airline_name") or \
                    (f"Seat {it.get('seat_number')}" if it.get("seat_number") else kind.title())
                out.append({"source": "booking", "ref_id": b["locator_code"], "locator_code": b["locator_code"],
                            "customer_email": b["customer_email"], "airline_code": b["airline_code"],
                            "airline_name": b["airline_name"], "kind": kind, "item_name": _cut(name, 200),
                            "amount": _num(it.get("price")), "currency": _cut(it.get("currency") or b["currency"], 10),
                            "outcome": outcome, "raw_status": _cut(st or None, 30), "created_at": b["booking_date"]})
    # Seats/extras bought after ticketing (Manage My Booking / Booking Status).
    for p in _main_rows("SELECT addon_id, locator_code, items_json, amount, currency, status, created_at "
                        "FROM addon_payments ORDER BY created_at"):
        b = by_loc.get(p.get("locator_code")) or {}
        status = (p.get("status") or "").lower()
        items = _json(p.get("items_json"), [])
        if not isinstance(items, list):
            items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            kind = it.get("kind") or ("seat" if it.get("seat_number") else "extra")
            name = it.get("name") or it.get("description") or it.get("airline_name") or \
                (f"Seat {it.get('seat_number')}" if it.get("seat_number") else kind.title())
            out.append({"source": "after_ticket", "ref_id": p.get("addon_id"), "locator_code": p.get("locator_code"),
                        "customer_email": b.get("customer_email"), "airline_code": b.get("airline_code"),
                        "airline_name": b.get("airline_name"), "kind": "seat" if kind == "seat" else "extra",
                        "item_name": _cut(name, 200), "amount": _num(it.get("price")),
                        "currency": _cut(it.get("currency") or p.get("currency"), 10),
                        "outcome": _PAYMENT_OUTCOME.get(status, "open"), "raw_status": _cut(status, 30),
                        "created_at": p.get("created_at")})
    return out


def _build_searches(ap: dict) -> list[dict]:
    rows = _main_rows("""SELECT id, searched_at, customer_id, trip_type, origin, destination, adults, children,
                                infants, students, cabin, result_count, cheapest_price, cheapest_currency,
                                cheapest_airline, error_message
                         FROM search_log WHERE search_kind='search' ORDER BY id""")
    out = []
    for r in rows:
        o, d = (r.get("origin") or "").upper() or None, (r.get("destination") or "").upper() or None
        out.append({
            "id": r["id"], "searched_at": r["searched_at"], "origin": o, "destination": d,
            "origin_country": _cut((ap.get(o) or {}).get("country"), 10),
            "destination_country": _cut((ap.get(d) or {}).get("country"), 10),
            "destination_city": _cut((ap.get(d) or {}).get("city"), 100),
            "trip_type": _cut(r.get("trip_type"), 12),
            "pax": max(1, sum(int(r.get(k) or 0) for k in ("adults", "children", "infants", "students"))),
            "cabin": _cut(r.get("cabin"), 20), "result_count": r.get("result_count"),
            "cheapest_price": r.get("cheapest_price"), "cheapest_currency": _cut(r.get("cheapest_currency"), 5),
            "cheapest_airline": _cut(r.get("cheapest_airline"), 100),
            "has_error": 1 if (r.get("error_message") or "").strip() else 0, "customer_id": r.get("customer_id"),
        })
    return out


def _build_verticals() -> list[dict]:
    out = []
    for h in _main_rows("""SELECT id, status, guest_name, guest_email, property_name, city, country_code, rooms,
                                  total_price, currency, check_in_date, booking_date, created_at FROM hotel_bookings"""):
        out.append({"vertical": "hotel", "source_id": h["id"], "status": _cut(h.get("status"), 30),
                    "customer_email": _email(h.get("guest_email")) or None, "customer_name": _cut(h.get("guest_name"), 150),
                    "title": _cut(h.get("property_name"), 200),
                    "place": _cut(", ".join(x for x in (h.get("city"), h.get("country_code")) if x), 150),
                    "travellers": h.get("rooms"), "amount": h.get("total_price"), "currency": _cut(h.get("currency"), 10),
                    "event_date": h.get("check_in_date"), "requested_at": h.get("booking_date") or h.get("created_at")})
    for v in _main_rows("""SELECT id, status, full_name, email, nationality, destination, slot_date, requested_at
                           FROM visa_consultations"""):
        out.append({"vertical": "visa", "source_id": v["id"], "status": _cut(v.get("status"), 30),
                    "customer_email": _email(v.get("email")) or None, "customer_name": _cut(v.get("full_name"), 150),
                    "title": _cut(f"Visa consultation - {v.get('destination') or '?'}", 200),
                    "place": _cut(v.get("destination"), 150), "travellers": 1, "amount": None, "currency": None,
                    "event_date": v.get("slot_date"), "requested_at": v.get("requested_at")})
    packages = {p["id"]: p for p in _main_rows("SELECT id, title, destination, price, currency FROM tour_packages")}
    for r in _main_rows("""SELECT id, package_id, status, full_name, email, num_travelers, preferred_date, requested_at
                           FROM package_booking_requests"""):
        p = packages.get(r.get("package_id")) or {}
        n = r.get("num_travelers") or 1
        out.append({"vertical": "package", "source_id": r["id"], "status": _cut(r.get("status"), 30),
                    "customer_email": _email(r.get("email")) or None, "customer_name": _cut(r.get("full_name"), 150),
                    "title": _cut(p.get("title") or f"Package #{r.get('package_id')}", 200),
                    "place": _cut(p.get("destination"), 150), "travellers": n,
                    "amount": (_num(p.get("price")) * n) if p.get("price") is not None else None,
                    "currency": _cut(p.get("currency"), 10), "event_date": r.get("preferred_date"),
                    "requested_at": r.get("requested_at")})
    return out


def _build_customers(bookings: list[dict], extras: list[dict], searches: list[dict], verticals: list[dict],
                     now: datetime) -> list[dict]:
    people: dict[str, dict] = {}

    def person(email: str) -> dict:
        return people.setdefault(email, {
            "email": email, "customer_id": None, "full_name": None, "phone": None, "has_account": 0,
            "account_since": None, "bookings": 0, "ticketed": 0, "cancelled": 0, "spend": {},
            "first_booking": None, "last_booking": None, "last_trip_route": None, "last_trip_date": None,
            "next_trip_date": None, "airlines": {}, "extras_items": 0, "extras_spend": {}, "searches": 0,
            "last_search": None, "hotel_requests": 0, "visa_requests": 0, "package_requests": 0,
            "loyalty_points": None})

    today = now.date()
    for b in sorted(bookings, key=lambda x: x["booking_id"]):
        email = b["customer_email"]
        if not email:
            continue
        p = person(email)
        p["full_name"] = b["passenger_name"] or p["full_name"]          # latest booking wins
        p["phone"] = b.get("phone") or p["phone"]
        p["customer_id"] = b["customer_id"] or p["customer_id"]
        p["bookings"] += 1
        st = (b["status"] or "").lower()
        if b["is_ticketed"]:
            p["ticketed"] += 1
            cur = b["currency"] or "?"
            p["spend"][cur] = p["spend"].get(cur, 0) + b["total_fare"]
        if any(w in st for w in ("cancel", "void", "refund")):
            p["cancelled"] += 1
        if b["airline_code"] or b["airline_name"]:
            key = b["airline_name"] or b["airline_code"]
            p["airlines"][key] = p["airlines"].get(key, 0) + 1
        bd = b["booking_date"]
        if bd:
            p["first_booking"] = min(p["first_booking"] or bd, bd)
            p["last_booking"] = max(p["last_booking"] or bd, bd)
        dep = b["departure_at"]
        if dep and b["ever_ticketed"]:
            route = f"{b['origin'] or '?'}-{b['destination'] or '?'}"
            if dep.date() <= today and (not p["last_trip_date"] or dep.date() >= p["last_trip_date"]):
                p["last_trip_date"], p["last_trip_route"] = dep.date(), route
            if dep.date() > today and b["is_ticketed"] and (not p["next_trip_date"] or dep.date() < p["next_trip_date"]):
                p["next_trip_date"] = dep.date()
    for e in extras:
        if e["customer_email"] and e["outcome"] == "paid":
            p = person(e["customer_email"])
            p["extras_items"] += 1
            cur = e["currency"] or "?"
            p["extras_spend"][cur] = p["extras_spend"].get(cur, 0) + e["amount"]
    for c in _main_rows("SELECT id, email, full_name, phone, created_at FROM customers"):
        email = _email(c.get("email"))
        if not email:
            continue
        p = person(email)
        p["customer_id"], p["has_account"], p["account_since"] = c["id"], 1, c.get("created_at")
        p["full_name"] = c.get("full_name") or p["full_name"]
        p["phone"] = c.get("phone") or p["phone"]
    for v in verticals:
        email = v["customer_email"]
        if not email:
            continue
        p = person(email)
        p[f"{v['vertical']}_requests"] += 1
        p["full_name"] = p["full_name"] or v["customer_name"]
    for r in _main_rows("SELECT email, points_balance FROM loyalty_accounts"):
        email = _email(r.get("email"))
        if email in people:
            people[email]["loyalty_points"] = r.get("points_balance")
    by_cid = {p["customer_id"]: p for p in people.values() if p["customer_id"]}
    for s in searches:
        p = by_cid.get(s["customer_id"])
        if p:
            p["searches"] += 1
            p["last_search"] = max(p["last_search"] or s["searched_at"], s["searched_at"])
    out = []
    for p in people.values():
        out.append({
            "email": _cut(p["email"], 200), "customer_id": p["customer_id"], "full_name": _cut(p["full_name"], 150),
            "phone_masked": mask_phone(p["phone"]), "has_account": p["has_account"], "account_since": p["account_since"],
            "bookings": p["bookings"], "ticketed": p["ticketed"], "cancelled": p["cancelled"],
            "spend_json": json.dumps({k: round(v, 2) for k, v in p["spend"].items()}),
            "spend_lkr": round(p["spend"].get("LKR", 0), 2), "first_booking": p["first_booking"],
            "last_booking": p["last_booking"], "last_trip_route": p["last_trip_route"],
            "last_trip_date": p["last_trip_date"], "next_trip_date": p["next_trip_date"],
            "airlines_json": json.dumps(dict(sorted(p["airlines"].items(), key=lambda kv: -kv[1]))),
            "extras_items": p["extras_items"],
            "extras_spend_json": json.dumps({k: round(v, 2) for k, v in p["extras_spend"].items()}),
            "searches": p["searches"], "last_search": p["last_search"], "hotel_requests": p["hotel_requests"],
            "visa_requests": p["visa_requests"], "package_requests": p["package_requests"],
            "loyalty_points": p["loyalty_points"], "synced_at": now,
        })
    return out


def _replace(cur, table: str, rows: list[dict], cols: list[str]) -> None:
    cur.execute(f"DELETE FROM {t(table)}")
    if not rows:
        return
    sql = f"INSERT INTO {t(table)} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    data = [tuple(r.get(c) for c in cols) for r in rows]
    for i in range(0, len(data), 500):
        cur.executemany(sql, data[i:i + 500])


_BOOKING_COLS = ["booking_id", "locator_code", "customer_email", "customer_id", "passenger_name", "pax_count",
                 "airline_code", "airline_name", "flight_number", "origin", "destination", "origin_city",
                 "destination_city", "origin_country", "destination_country", "trip_type", "segment_count",
                 "cabin_class", "fare_source", "total_fare", "currency", "status", "is_ticketed", "ever_ticketed",
                 "refund_amount", "booking_date", "departure_at", "payment_method"]
_EXTRA_COLS = ["source", "ref_id", "locator_code", "customer_email", "airline_code", "airline_name", "kind",
               "item_name", "amount", "currency", "outcome", "raw_status", "created_at"]
_SEARCH_COLS = ["id", "searched_at", "origin", "destination", "origin_country", "destination_country",
                "destination_city", "trip_type", "pax", "cabin", "result_count", "cheapest_price",
                "cheapest_currency", "cheapest_airline", "has_error", "customer_id"]
_VERTICAL_COLS = ["vertical", "source_id", "status", "customer_email", "customer_name", "title", "place",
                  "travellers", "amount", "currency", "event_date", "requested_at"]
_CUSTOMER_COLS = ["email", "customer_id", "full_name", "phone_masked", "has_account", "account_since", "bookings",
                  "ticketed", "cancelled", "spend_json", "spend_lkr", "first_booking", "last_booking",
                  "last_trip_route", "last_trip_date", "next_trip_date", "airlines_json", "extras_items",
                  "extras_spend_json", "searches", "last_search", "hotel_requests", "visa_requests",
                  "package_requests", "loyalty_points", "synced_at"]


def run_sync(triggered_by: str = "schedule") -> dict:
    """Full rebuild of the analytics tables from the main database. Never
    raises: returns {"ok", "counts" | "error", "busy"?}."""
    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "error": "A refresh is already running - try again in a moment."}
    run_id = None
    try:
        ensure_storage()
        started = datetime.now()
        try:
            run_id = write(f"INSERT INTO {t('sync_runs')} (started_at, triggered_by) VALUES (%s, %s)",
                           (started, _cut(triggered_by, 100)))
        except Exception as e:
            logger.warning(f"Analytics sync: could not log the run ({e})")
        ap = _airports()
        facts, enriched = _build_bookings(ap)
        extras = _build_extras(enriched)
        searches = _build_searches(ap)
        verticals = _build_verticals()
        customers = _build_customers(enriched, extras, searches, verticals, started)
        conn = database.get_db_connection()
        cur = conn.cursor()
        try:
            # One transaction (autocommit is off): the dashboard keeps reading
            # the previous build until this one commits.
            _replace(cur, "booking_facts", facts, _BOOKING_COLS)
            _replace(cur, "extras_facts", extras, _EXTRA_COLS)
            _replace(cur, "search_facts", searches, _SEARCH_COLS)
            _replace(cur, "vertical_facts", verticals, _VERTICAL_COLS)
            _replace(cur, "customers_360", customers, _CUSTOMER_COLS)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        counts = {"bookings": len(facts), "extras_items": len(extras), "searches": len(searches),
                  "customers": len(customers), "hotels": sum(v["vertical"] == "hotel" for v in verticals),
                  "visa": sum(v["vertical"] == "visa" for v in verticals),
                  "packages": sum(v["vertical"] == "package" for v in verticals),
                  "seconds": round((datetime.now() - started).total_seconds(), 2)}
        if run_id:
            write(f"UPDATE {t('sync_runs')} SET finished_at=%s, ok=1, counts_json=%s WHERE id=%s",
                  (datetime.now(), json.dumps(counts), run_id))
        logger.info(f"Analytics sync ({triggered_by}): {counts}")
        return {"ok": True, "counts": counts}
    except Exception as e:
        logger.error(f"Analytics sync failed: {e}", exc_info=True)
        if run_id:
            try:
                write(f"UPDATE {t('sync_runs')} SET finished_at=%s, ok=0, error=%s WHERE id=%s",
                      (datetime.now(), str(e)[:1000], run_id))
            except Exception:
                pass
        return {"ok": False, "error": "The analytics refresh failed - see the server log."}
    finally:
        _sync_lock.release()


def last_run() -> dict | None:
    """Latest successful rebuild (and the latest attempt if it failed)."""
    ok = q(f"SELECT id, started_at, finished_at, triggered_by, counts_json FROM {t('sync_runs')} "
           "WHERE ok=1 ORDER BY id DESC LIMIT 1", one=True)
    latest = q(f"SELECT id, started_at, finished_at, ok, error FROM {t('sync_runs')} ORDER BY id DESC LIMIT 1", one=True)
    out = None
    if ok:
        out = {"refreshed_at": ok["finished_at"], "triggered_by": ok["triggered_by"],
               "counts": _json(ok.get("counts_json"), {})}
    if latest and not latest["ok"] and latest["finished_at"]:
        out = {**(out or {}), "last_failed_at": latest["finished_at"]}
    return out


def start_scheduler(interval_minutes: int) -> None:
    """Daemon thread: rebuild shortly after start-up, then every interval."""
    if interval_minutes <= 0:
        logger.info("Analytics sync is off (ANALYTICS_SYNC_MINUTES=0)")
        return

    def loop():
        time.sleep(90)  # let the app finish starting
        while True:
            try:
                run_sync("schedule")
            except Exception as e:  # run_sync never raises; belt and braces
                logger.error(f"Analytics sync loop error: {e}")
            time.sleep(interval_minutes * 60)

    threading.Thread(target=loop, name="analytics-sync", daemon=True).start()
    logger.info(f"Analytics sync every {interval_minutes} min")

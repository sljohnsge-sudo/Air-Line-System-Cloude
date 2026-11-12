"""
Super-admin go-live switches (11/11/2026): turn flight search, creating new
bookings (PNRs) and ticketing on or off while go-live testing, and the staff
void/refund switch (provider_settings.ticketed_cancel_enabled, which already
existed) — only a super admin may change them.

Every switch only ever stops a customer BEFORE they pay: search, creating a
new booking, and starting a payment for a flight ticket. A customer who has
already paid is never blocked — their booking and ticket go ahead as usual.

All switches default ON (and void/refund keeps its current value), so turning
this in changes nothing until a super admin flips one. Own tables, created on
first use: go_live_controls (one row) and super_admins (seeded with the
'admin' Administrator account).
"""
import logging
import threading
import time

import database

logger = logging.getLogger(__name__)

SWITCHES = {
    "flight_search_enabled": "Flight search results",
    "pnr_create_enabled": "Create new bookings (PNR)",
    "ticketing_enabled": "Issue tickets (take payment for new tickets)",
}

CUSTOMER_MESSAGES = {
    "flight_search_enabled": "Flight search is paused for a short while. Please try again later.",
    "pnr_create_enabled": "New bookings are paused for a short while. Nothing has been booked or charged — please try again later.",
    "ticketing_enabled": "Ticketing is paused for a short while, so we can't take payment right now. "
                         "Nothing has been charged — please try again later.",
}

# 12/11/2026: staff switches for Amadeus bookings (Admin > Amadeus Bookings).
# They start OFF: nothing on Amadeus can be cancelled, voided or refunded
# until someone turns the switch on. Unlike the customer switches above they
# fail CLOSED (off) if the switches can't be read.
AMADEUS_SWITCHES = {
    "amadeus_cancel_enabled": "Amadeus: cancel bookings",
    "amadeus_void_enabled": "Amadeus: void tickets & EMDs",
    "amadeus_refund_enabled": "Amadeus: refund tickets",
}

_ready = False
_lock = threading.Lock()
_cache = {"at": 0.0, "value": None}
_CACHE_SECONDS = 5


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
                CREATE TABLE IF NOT EXISTS go_live_controls (
                    id TINYINT PRIMARY KEY,
                    flight_search_enabled TINYINT(1) NOT NULL DEFAULT 1,
                    pnr_create_enabled TINYINT(1) NOT NULL DEFAULT 1,
                    ticketing_enabled TINYINT(1) NOT NULL DEFAULT 1,
                    updated_by VARCHAR(100) NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )""")
            cur.execute("INSERT IGNORE INTO go_live_controls (id) VALUES (1)")
            # 11/11/2026: for now every staff account may change the switches
            # (owner's request); a super admin can turn this off later.
            cur.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
                           AND TABLE_NAME='go_live_controls' AND COLUMN_NAME='all_admins_can_switch'""")
            if cur.fetchone()[0] == 0:
                cur.execute("ALTER TABLE go_live_controls ADD COLUMN all_admins_can_switch TINYINT(1) NOT NULL DEFAULT 1")
            for col in AMADEUS_SWITCHES:
                cur.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()
                               AND TABLE_NAME='go_live_controls' AND COLUMN_NAME=%s""", (col,))
                if cur.fetchone()[0] == 0:
                    cur.execute(f"ALTER TABLE go_live_controls ADD COLUMN {col} TINYINT(1) NOT NULL DEFAULT 0")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS super_admins (
                    admin_id INT PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            cur.execute("SELECT COUNT(*) FROM super_admins")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT IGNORE INTO super_admins (admin_id) SELECT id FROM admins WHERE username='admin'")
            conn.commit()
            _ready = True
        finally:
            cur.close()
            conn.close()


def get() -> dict:
    """{flight_search_enabled, pnr_create_enabled, ticketing_enabled,
    ticketed_cancel_enabled, updated_by, updated_at}. Cached a few seconds —
    it is read on every search and booking."""
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < _CACHE_SECONDS:
        return dict(_cache["value"])
    _ensure_tables()
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT flight_search_enabled, pnr_create_enabled, ticketing_enabled, all_admins_can_switch, "
                    f"{', '.join(AMADEUS_SWITCHES)}, updated_by, updated_at FROM go_live_controls WHERE id=1")
        row = cur.fetchone() or {}
    finally:
        cur.close()
        conn.close()
    value = {k: bool(row.get(k, 1)) for k in SWITCHES}
    value.update({k: bool(row.get(k, 0)) for k in AMADEUS_SWITCHES})
    value["ticketed_cancel_enabled"] = bool(database.get_provider_settings().get("ticketed_cancel_enabled", False))
    value["all_admins_can_switch"] = bool(row.get("all_admins_can_switch", 1))
    value["updated_by"] = row.get("updated_by")
    value["updated_at"] = row["updated_at"].isoformat(sep=" ") if row.get("updated_at") else None
    _cache.update(at=now, value=value)
    return dict(value)


def is_on(name: str) -> bool:
    """Fails open: if the switches can't be read, nothing is blocked (the
    system keeps working exactly as before)."""
    try:
        return get().get(name, True)
    except Exception as e:
        logger.warning(f"Go-live switch {name} could not be read ({e}) — treating it as ON")
        return True


def staff_switch_on(name: str) -> bool:
    """Amadeus staff switches: fail closed — if they can't be read, the
    action stays off."""
    try:
        return bool(get().get(name, False))
    except Exception as e:
        logger.warning(f"Staff switch {name} could not be read ({e}) — treating it as OFF")
        return False


def is_super_admin(admin_id) -> bool:
    if not admin_id:
        return False
    _ensure_tables()
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM super_admins WHERE admin_id=%s", (admin_id,))
        return cur.fetchone() is not None
    finally:
        cur.close()
        conn.close()


def can_change(admin_id) -> bool:
    """Who may change the switches (go-live, payment methods, void/refund):
    a super admin always; every staff account while all_admins_can_switch is
    on (the owner's choice for now)."""
    if is_super_admin(admin_id):
        return True
    try:
        return bool(admin_id) and get().get("all_admins_can_switch", True)
    except Exception:
        return False


def update(values: dict, admin_username: str | None) -> dict:
    _ensure_tables()
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        cols = {k: (1 if values[k] else 0) for k in (*SWITCHES, *AMADEUS_SWITCHES, "all_admins_can_switch")
                if k in values and values[k] is not None}
        if cols:
            sets = ", ".join(f"{k}=%s" for k in cols)
            cur.execute(f"UPDATE go_live_controls SET {sets}, updated_by=%s WHERE id=1", (*cols.values(), admin_username))
        if values.get("ticketed_cancel_enabled") is not None:
            cur.execute("UPDATE provider_settings SET ticketed_cancel_enabled=%s WHERE id=1",
                        (1 if values["ticketed_cancel_enabled"] else 0,))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    _cache.update(at=0.0, value=None)
    logger.warning(f"Go-live switches changed by {admin_username}: {values}")
    return get()

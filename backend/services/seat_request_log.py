"""
Every seat a customer asks for, with what the airline answered.

A seat the airline refuses never reaches the PNR, so the booking overview
(live from Travelport) could only say "No seats on the booking yet" — the
refusal and its reason were shown once, on the screen where the seat was
chosen, and then lost. Each request's outcome is kept here (its own table,
created on first use) and shown next to the PNR's live seats.
"""
import logging
import threading

import database

logger = logging.getLogger(__name__)

_ready = False
_lock = threading.Lock()


def _ensure_table() -> None:
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
                CREATE TABLE IF NOT EXISTS seat_requests (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    locator_code VARCHAR(12) NOT NULL,
                    passenger VARCHAR(120) NULL,
                    flight_number VARCHAR(12) NULL,
                    seat_number VARCHAR(8) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    reason VARCHAR(500) NULL,
                    price DECIMAL(12,2) NULL,
                    currency VARCHAR(3) NULL,
                    source VARCHAR(20) NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_seat_requests_locator (locator_code)
                )""")
            conn.commit()
            _ready = True
        finally:
            cur.close()
            conn.close()


def record(locator: str, travelers: list, seats: list, source: str) -> None:
    """seats: add_seats_and_extras_to_pnr()'s seat results (status
    confirmed / pending / failed, reason). Never raises — logging a request
    must not break the booking."""
    rows = []
    for s in seats or []:
        seat = str(s.get("seat_number") or "").strip()
        if not seat:
            continue
        idx = s.get("passenger_idx")
        t = travelers[idx] if isinstance(idx, int) and 0 <= idx < len(travelers or []) else {}
        name = t.get("name") or " ".join(x for x in (t.get("first_name"), t.get("last_name")) if x) or None
        rows.append((locator.upper(), (name or "")[:120] or None, (s.get("flight_number") or "")[:12] or None, seat[:8],
                     (s.get("status") or "failed")[:20], (s.get("reason") or "")[:500] or None,
                     s.get("price") or None, s.get("currency"), source))
    if not rows:
        return
    try:
        _ensure_table()
        conn = database.get_db_connection()
        cur = conn.cursor()
        try:
            cur.executemany("""INSERT INTO seat_requests (locator_code, passenger, flight_number, seat_number, status,
                               reason, price, currency, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.warning(f"{locator}: seat request not logged: {e}")


def for_locator(locator: str) -> list:
    try:
        _ensure_table()
        conn = database.get_db_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM seat_requests WHERE locator_code=%s ORDER BY created_at, id", (locator.upper(),))
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.warning(f"{locator}: seat requests not read: {e}")
        return []


def _norm(seat: str) -> str:
    return (seat or "").upper().lstrip("0")


def view(locator: str, live: dict) -> list:
    """The customer's seat requests, newest answer per seat, each with where
    it stands now. A seat still on the PNR takes the airline's live status;
    one that isn't was refused (with the reason) or has since been dropped."""
    latest = {}
    for r in for_locator(locator):
        latest[(r["flight_number"] or "", _norm(r["seat_number"]), r["passenger"] or "")] = r
    on_pnr = live.get("seats") or []
    out = []
    for (flight, seat, passenger), r in latest.items():
        live_seat = next((s for s in on_pnr if _norm(s.get("seat")) == seat
                          and (not flight or any(flight in (f or "") for f in s.get("flights") or []))), None)
        if live_seat:
            continue  # shown with the PNR's own seats, with the airline's status
        if r["status"] == "failed":
            state, label = "rejected", "Not confirmed"
        else:
            state, label = "released", "No longer on the booking"
        out.append({"seat": r["seat_number"], "flight": flight, "passenger": passenger or None, "state": state,
                    "label": label, "reason": r["reason"],
                    "requested_at": r["created_at"].isoformat() if r.get("created_at") else None})
    return out

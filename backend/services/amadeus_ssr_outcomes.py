"""
What airlines answer to special requests on Amadeus bookings (14/11/2026).

Every Amadeus booking with a special meal or wheelchair request records the
airline's answer here, per airline, flight and request code, for staff:
confirmed / requested / refused from the airline's own status code in the
booking, "not_sent" when it could not be added (an Amadeus or our error -
not an airline answer), "missing" when it was not on the booking after it
was saved, or "other" with the airline's code as it came.

13/11/2026 audit: these answers are NOT used to grey options out for other
bookings - each customer sees the airline's answer for their own booking.
Answers from the Amadeus TEST system are kept apart from live ones (system).

Own table, created on first use: amadeus_ssr_outcomes.
"""
import logging
import threading

import database

logger = logging.getLogger(__name__)

_ready = False
_lock = threading.Lock()


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
                CREATE TABLE IF NOT EXISTS amadeus_ssr_outcomes (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    carrier VARCHAR(3) NOT NULL,
                    code VARCHAR(4) NOT NULL,
                    state VARCHAR(10) NOT NULL,
                    status VARCHAR(4) NULL,
                    flight VARCHAR(10) NULL,
                    reason VARCHAR(300) NULL,
                    locator VARCHAR(12) NULL,
                    system VARCHAR(4) NOT NULL DEFAULT 'TEST',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_aso (system, carrier, code, id)
                )""")
            conn.commit()
            _ready = True
        finally:
            cur.close()
            conn.close()


def record(outcomes: list[dict], locator: str | None, system: str) -> None:
    """Keeps each meal / wheelchair answer (the address is not an airline
    yes/no, so it isn't kept)."""
    rows = [o for o in outcomes or [] if o.get("kind") in ("meal", "wheelchair") and o.get("carrier") and o.get("code")]
    if not rows or not system:
        return
    _ensure_tables()
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        cur.executemany(
            "INSERT INTO amadeus_ssr_outcomes (carrier, code, state, status, flight, reason, locator, system) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            [(o["carrier"][:3], o["code"][:4], (o.get("state") or "")[:10], (o.get("status") or None),
              (o.get("flight") or None), (o.get("reason") or "")[:300] or None, locator, system[:4])
             for o in rows])
        conn.commit()
    finally:
        cur.close()
        conn.close()


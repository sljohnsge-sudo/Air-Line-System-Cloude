"""
What airlines answer to special requests on Amadeus bookings (14/11/2026).

Every Amadeus booking with a special meal or wheelchair request records the
airline's real answer here, per airline and request code: confirmed (HK/KK),
requested (NN/HN - not answered yet) or refused (UN/NO/UC/..., or an error
when it was added). The passenger form greys out a request that an airline
on the chosen fare refused last time ("not provided by the airline"); a
later confirmation for that airline and code makes it available again.
Answers from the Amadeus TEST system are kept apart from live ones (system
column) - test answers can differ from live.

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
    if not rows:
        return
    _ensure_tables()
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        cur.executemany(
            "INSERT INTO amadeus_ssr_outcomes (carrier, code, state, status, flight, reason, locator, system) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            [(o["carrier"][:3], o["code"][:4], (o.get("state") or "")[:10], (o.get("status") or None),
              (o.get("flight") or None), (o.get("reason") or "")[:300] or None, locator, (system or "TEST")[:4])
             for o in rows])
        conn.commit()
    finally:
        cur.close()
        conn.close()


def not_provided(carriers: list[str], system: str) -> list[str]:
    """Request codes the latest answer from any of these airlines refused
    (on this Amadeus system). Codes nobody has asked for yet stay available."""
    carriers = [c.upper()[:3] for c in carriers if c]
    if not carriers:
        return []
    _ensure_tables()
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        marks = ",".join(["%s"] * len(carriers))
        cur.execute(f"""SELECT o.carrier, o.code, o.state FROM amadeus_ssr_outcomes o
                        JOIN (SELECT carrier, code, MAX(id) AS last_id FROM amadeus_ssr_outcomes
                              WHERE system=%s AND carrier IN ({marks}) AND state IN ('confirmed','refused')
                              GROUP BY carrier, code) l ON l.last_id = o.id""",
                    (system, *carriers))
        return sorted({r["code"] for r in cur.fetchall() if r["state"] == "refused"})
    finally:
        cur.close()
        conn.close()

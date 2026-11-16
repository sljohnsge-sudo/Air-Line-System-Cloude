"""
Website contacts and customer-facing texts that staff edit in the Admin
Portal (Administration › Website Contacts & Texts), 16/11/2026.

Each item belongs to a section (Contact details, Review & Pay page,
E-ticket...). The default below is what the site showed before; an empty
saved value means "use the default". Contacts fall back to the old
SUPPORT_* settings in .env until staff save them here.

To add a new editable text: add an item to SECTIONS, then use
siteText('<key>') in the frontend (or site_content.value('<key>') here).
Own table site_content, created on first use.
"""
import logging
import re
import threading
import time

import database
from config.email_config import EmailConfig

logger = logging.getLogger(__name__)

# kind: phone | email | text (one line) | paragraph | list (one item per line)
SECTIONS = [
    {"id": "contacts", "title": "Contact details",
     "note": "Shown on e-tickets, customer emails and help messages. Leave a field empty to hide it.",
     "items": [
         {"key": "support_phone", "label": "Customer support phone", "kind": "phone", "default": EmailConfig.SUPPORT_PHONE},
         {"key": "support_email", "label": "Customer support email", "kind": "email", "default": EmailConfig.SUPPORT_EMAIL},
         {"key": "support_whatsapp", "label": "WhatsApp number", "kind": "phone", "default": ""},
         {"key": "support_address", "label": "Office address", "kind": "text", "default": EmailConfig.SUPPORT_ADDRESS},
     ]},
    {"id": "review", "title": "Review & Pay page",
     "note": "The last page before the customer pays.",
     "items": [
         {"key": "review_passenger_confirmation", "label": "Passenger details confirmation (tick box)", "kind": "paragraph",
          "default": "I confirm that all passenger details entered (names, passport information, dates of birth) are "
                     "accurate and match my travel documents exactly. I understand that incorrect information may "
                     "result in denied boarding, additional airline fees, or ticket cancellation with no refund.",
          "help": "The customer must tick this before they can continue to payment."},
         {"key": "review_bank_note", "label": "Bank transfer note", "kind": "paragraph",
          "default": "Your booking is confirmed with the airline first and held for you. You then pay by bank transfer "
                     "or deposit using your booking reference. Your ticket is issued and emailed as soon as our team "
                     "confirms your payment has arrived."},
         {"key": "review_cash_note", "label": "Cash payment note", "kind": "paragraph",
          "default": "Your booking is confirmed with the airline first and held for you. Pay in cash at a Tick and "
                     "Book office using your booking reference; your ticket is issued and emailed as soon as we "
                     "receive it."},
     ]},
    {"id": "eticket", "title": "E-ticket",
     "note": "The information box at the bottom of every e-ticket.",
     "items": [
         {"key": "eticket_info_title", "label": "Box title", "kind": "text", "default": "Important Travel Information"},
         {"key": "eticket_info_items", "label": "Information points (one per line)", "kind": "list",
          "default": "\n".join([
              "Please carry a printout of this e-ticket receipt or have it ready on your mobile device during check-in.",
              "Most international flights require passengers to arrive at the airport check-in counter at least 3 hours "
              "before departure. Check-in gates close exactly 60 minutes prior.",
              "Passport Validity: Ensure your passport is valid for at least 6 months from the date of your journey. "
              "Many destinations require specific entry visas.",
              "Baggage policies may vary. Please contact Tick and Book support or the airline directly to purchase extra allowance.",
              "For support, flight updates, changes or cancellations, please contact the Tick and Book desk {support}.",
          ]),
          "help": "{support} is replaced with the support phone and email from Contact details."},
     ]},
]

ITEMS = {i["key"]: i for s in SECTIONS for i in s["items"]}
_MAX_LEN = {"phone": 40, "email": 120, "text": 300, "paragraph": 2000, "list": 4000}
_PHONE_RE = re.compile(r"^\+?[0-9 ()\-]{6,40}$")
_EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")

_ready = False
_lock = threading.Lock()
_cache = {"at": 0.0, "value": None}
_CACHE_SECONDS = 30


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
                CREATE TABLE IF NOT EXISTS site_content (
                    content_key VARCHAR(80) PRIMARY KEY,
                    content_value TEXT NOT NULL,
                    updated_by VARCHAR(100) NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
            conn.commit()
            _ready = True
        finally:
            cur.close()
            conn.close()


def _saved() -> dict:
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < _CACHE_SECONDS:
        return _cache["value"]
    _ensure_table()
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT content_key, content_value, updated_by, updated_at FROM site_content")
        rows = {r["content_key"]: r for r in cur.fetchall()}
    finally:
        cur.close()
        conn.close()
    _cache.update(at=now, value=rows)
    return rows


def values() -> dict:
    """{key: text} for every item: the saved text, or the default. Never
    raises — if the table can't be read the defaults are used."""
    try:
        saved = _saved()
    except Exception as e:
        logger.warning(f"Website texts could not be read, using defaults: {e}")
        saved = {}
    out = {}
    for key, item in ITEMS.items():
        row = saved.get(key)
        out[key] = row["content_value"] if row and row["content_value"].strip() else (item["default"] or "")
    return out


def value(key: str) -> str:
    return values().get(key, "")


def admin_view() -> dict:
    """Sections with each item's current text, default and last change."""
    saved = _saved()
    current = values()
    sections = []
    for s in SECTIONS:
        items = []
        for i in s["items"]:
            row = saved.get(i["key"])
            items.append({**i, "value": current[i["key"]], "customised": bool(row and row["content_value"].strip()),
                          "updated_by": row["updated_by"] if row else None,
                          "updated_at": row["updated_at"].isoformat(sep=" ") if row and row.get("updated_at") else None})
        sections.append({**s, "items": items})
    return {"sections": sections}


def update(changes: dict, admin_username: str | None) -> dict:
    """Save {key: text}. An empty text (or the default itself) goes back to
    the default. Raises ValueError with a plain message for bad input."""
    clean = {}
    for key, text in (changes or {}).items():
        item = ITEMS.get(key)
        if not item:
            raise ValueError(f"Unknown item: {key}")
        text = (text or "").replace("\r\n", "\n").strip()
        if len(text) > _MAX_LEN[item["kind"]]:
            raise ValueError(f"{item['label']}: too long (max {_MAX_LEN[item['kind']]} characters).")
        if text and item["kind"] == "phone" and not _PHONE_RE.match(text):
            raise ValueError(f"{item['label']}: use digits, spaces, + ( ) - only, e.g. +94 11 234 5678.")
        if text and item["kind"] == "email" and not _EMAIL_RE.match(text):
            raise ValueError(f"{item['label']}: this is not a valid email address.")
        if item["kind"] in ("phone", "email", "text") and "\n" in text:
            raise ValueError(f"{item['label']}: must be one line.")
        clean[key] = "" if text == (item["default"] or "").strip() else text
    if not clean:
        return admin_view()
    _ensure_table()
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        for key, text in clean.items():
            if text:
                cur.execute("INSERT INTO site_content (content_key, content_value, updated_by) VALUES (%s,%s,%s) "
                            "ON DUPLICATE KEY UPDATE content_value=VALUES(content_value), updated_by=VALUES(updated_by)",
                            (key, text, admin_username))
            else:
                cur.execute("DELETE FROM site_content WHERE content_key=%s", (key,))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    _cache.update(at=0.0, value=None)
    logger.warning(f"Website texts changed by {admin_username}: {sorted(clean)}")
    return admin_view()


# ── Contacts, for emails and e-tickets ───────────────────────────────────────

def support_phone() -> str:
    return value("support_phone").strip()


def support_email() -> str:
    return value("support_email").strip()


def support_address() -> str:
    return value("support_address").strip()


def support_whatsapp() -> str:
    return value("support_whatsapp").strip()

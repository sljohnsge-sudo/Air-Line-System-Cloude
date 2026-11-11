"""
Offers & notifications (11/11/2026): customers say in My Profile whether they
want special notifications (offers, airline deals, hotel deals, fare / rate
drops), how (email, SMS, WhatsApp) and what they like: favourite destinations
(countries / cities), airlines, hotels and anything else. Staff find the
customers interested in, say, Dubai or Emirates (Admin > Customer Interests)
and send them a special offer.

Only customers who switched notifications ON are ever listed or messaged
(their explicit choice). Own table customer_interests, created on first use.
"""
import json
import logging
import re
import threading

import database

logger = logging.getLogger(__name__)

TOPICS = {"offers": "Special offers", "airlines": "Airline deals", "hotels": "Hotel deals", "rates": "Fare & rate drops"}
CHANNELS = {"email": "Email", "sms": "SMS", "whatsapp": "WhatsApp"}
LIST_FIELDS = ("destinations", "airlines", "hotels")
MAX_ITEMS = 15

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
                CREATE TABLE IF NOT EXISTS customer_interests (
                    customer_id INT PRIMARY KEY,
                    opt_in TINYINT(1) NOT NULL DEFAULT 0,
                    channels_json TEXT NULL,
                    topics_json TEXT NULL,
                    destinations_json TEXT NULL,
                    airlines_json TEXT NULL,
                    hotels_json TEXT NULL,
                    other VARCHAR(300) NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS customer_offer_messages (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    sent_by VARCHAR(100) NULL,
                    subject VARCHAR(200) NOT NULL,
                    message TEXT NOT NULL,
                    filters_json TEXT NULL,
                    recipients INT NOT NULL DEFAULT 0,
                    sent_ok INT NOT NULL DEFAULT 0,
                    sent_failed INT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.commit()
            _ready = True
        finally:
            cur.close()
            conn.close()


def _q(sql, args=(), one=False, write=False):
    _ensure_table()
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, args)
        if write:
            conn.commit()
            return cur.lastrowid
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows
    finally:
        cur.close()
        conn.close()


def _list(v):
    try:
        x = json.loads(v or "[]")
        return x if isinstance(x, list) else []
    except ValueError:
        return []


def _clean_list(values, allowed=None, max_len=60):
    out = []
    for v in values or []:
        v = re.sub(r"\s+", " ", str(v or "")).strip()[:max_len]
        if not v or (allowed and v not in allowed):
            continue
        if v.lower() not in [x.lower() for x in out]:
            out.append(v)
    return out[:MAX_ITEMS]


def _row_to_dict(r: dict | None) -> dict:
    r = r or {}
    return {
        "opt_in": bool(r.get("opt_in")),
        "channels": _list(r.get("channels_json")) or ["email"],
        "topics": _list(r.get("topics_json")) or list(TOPICS),
        "destinations": _list(r.get("destinations_json")),
        "airlines": _list(r.get("airlines_json")),
        "hotels": _list(r.get("hotels_json")),
        "other": r.get("other") or "",
    }


def get(customer_id: int) -> dict:
    return {**_row_to_dict(_q("SELECT * FROM customer_interests WHERE customer_id=%s", (customer_id,), one=True)),
            "topic_labels": TOPICS, "channel_labels": CHANNELS}


def update(customer_id: int, v: dict) -> dict:
    data = {
        "opt_in": 1 if v.get("opt_in") else 0,
        "channels_json": json.dumps(_clean_list(v.get("channels"), CHANNELS)),
        "topics_json": json.dumps(_clean_list(v.get("topics"), TOPICS)),
        "destinations_json": json.dumps(_clean_list(v.get("destinations"))),
        "airlines_json": json.dumps(_clean_list(v.get("airlines"))),
        "hotels_json": json.dumps(_clean_list(v.get("hotels"), max_len=80)),
        "other": (str(v.get("other") or "").strip()[:300] or None),
    }
    cols = list(data)
    _q(f"INSERT INTO customer_interests (customer_id, {', '.join(cols)}) VALUES (%s, {', '.join(['%s'] * len(cols))}) "
       f"ON DUPLICATE KEY UPDATE {', '.join(f'{c}=VALUES({c})' for c in cols)}", (customer_id, *data.values()), write=True)
    logger.info(f"Customer {customer_id}: notification preferences saved (opt_in={data['opt_in']})")
    return get(customer_id)


def _matches(i: dict, q: str, topic: str | None, channel: str | None) -> bool:
    if topic and topic not in i["topics"]:
        return False
    if channel and channel not in i["channels"]:
        return False
    if not q:
        return True
    q = q.lower()
    hay = " ".join(i["destinations"] + i["airlines"] + i["hotels"] + [i["other"]]).lower()
    return all(word in hay for word in q.split())


def search(q: str = "", topic: str | None = None, channel: str | None = None) -> dict:
    """Customers who switched notifications ON, matching the filters, plus the
    most popular favourites (for the admin page)."""
    rows = _q("""SELECT ci.*, c.id AS cid, c.full_name, c.email, c.phone FROM customer_interests ci
                 JOIN customers c ON c.id = ci.customer_id WHERE ci.opt_in = 1 ORDER BY ci.updated_at DESC""")
    out, tally = [], {k: {} for k in LIST_FIELDS}
    for r in rows:
        i = _row_to_dict(r)
        for k in LIST_FIELDS:
            for x in i[k]:
                key = x.strip().title()
                tally[k][key] = tally[k].get(key, 0) + 1
        if not _matches(i, (q or "").strip(), topic, channel):
            continue
        out.append({"customer_id": r["cid"], "name": r["full_name"], "email": r["email"], "phone": r["phone"],
                    **i, "updated_at": r["updated_at"].isoformat(sep=" ") if r.get("updated_at") else None})
    top = {k: sorted(({"name": n, "count": c} for n, c in tally[k].items()), key=lambda x: -x["count"])[:10] for k in LIST_FIELDS}
    return {"customers": out, "opted_in_total": len(rows), "top": top, "topic_labels": TOPICS, "channel_labels": CHANNELS}


def send(subject: str, message: str, customer_ids: list, sent_by: str | None, filters: dict) -> dict:
    """Email (and SMS where chosen and set up) the special offer to the chosen
    opted-in customers. Never messages anyone who switched notifications off."""
    subject = (subject or "").strip()[:200]
    message = (message or "").strip()[:4000]
    if not subject or not message:
        raise ValueError("Write a subject and a message.")
    ids = [int(x) for x in customer_ids or []][:500]
    if not ids:
        raise ValueError("Choose at least one customer.")
    rows = _q(f"""SELECT ci.*, c.full_name, c.email, c.phone FROM customer_interests ci JOIN customers c ON c.id = ci.customer_id
                  WHERE ci.opt_in = 1 AND ci.customer_id IN ({', '.join(['%s'] * len(ids))})""", tuple(ids))
    from services import email_service, sms_service
    ok = failed = 0
    errors = set()
    for r in rows:
        i = _row_to_dict(r)
        delivered = False
        if "email" in i["channels"] and r.get("email"):
            sent, err = email_service._send(r["email"], subject,
                                            f"Dear {r.get('full_name') or 'traveller'},\n\n{message}\n\n"
                                            "You receive this because you asked for special offers in your Tick and Book profile. "
                                            "You can switch them off any time in My Profile.\n\nTick and Book")
            delivered = delivered or sent
            if not sent and err:
                errors.add(str(err)[:160])
        if "sms" in i["channels"] and r.get("phone"):
            sent, err = sms_service.send_sms(r["phone"], f"Tick and Book: {subject}. {message}"[:300])
            delivered = delivered or sent
            if not sent and err:
                errors.add(str(err)[:160])
        if delivered:
            ok += 1
        else:
            failed += 1
    _q("""INSERT INTO customer_offer_messages (sent_by, subject, message, filters_json, recipients, sent_ok, sent_failed)
          VALUES (%s,%s,%s,%s,%s,%s,%s)""", (sent_by, subject, message, json.dumps(filters or {}), len(rows), ok, failed), write=True)
    logger.info(f"Special offer '{subject}' by {sent_by}: {ok} sent, {failed} not sent")
    return {"recipients": len(rows), "sent": ok, "not_sent": failed, "problems": sorted(errors)[:3]}


def history(limit: int = 20) -> list:
    rows = _q("SELECT id, sent_by, subject, recipients, sent_ok, sent_failed, created_at FROM customer_offer_messages "
              "ORDER BY id DESC LIMIT %s", (limit,))
    for r in rows:
        r["created_at"] = r["created_at"].isoformat(sep=" ") if r.get("created_at") else None
    return rows

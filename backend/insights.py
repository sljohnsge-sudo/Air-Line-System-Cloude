"""
insights.py
===========
Admin insight records — everything here is what customers actually entered
and what the providers (Travelport today, Amadeus later) actually returned.
Nothing is generated or estimated.

  * search_log           every flight search: who (IP / account), what, which
                         providers were asked and what each returned, and the
                         full ranked result list exactly as shown (cheapest
                         first) — proof of what a provider's fares looked like.
  * booking_journeys /   one row per customer journey (search → ticket) plus
    booking_journey_steps  each step passed or failed, with the message the
                         customer saw and the real underlying error.
  * blocked_ips          IPs an admin has blocked from searching/booking.
  * customer_messages    birthday / passport reminders already sent (so one is
                         never sent twice).

A journey is identified by the X-Journey-Id header the website sends with
every booking-flow request (a new id per search, kept across the payment
page redirect). The current request's journey id lives in a ContextVar so
deep code (payment check, ticketing) can record a step without passing ids.
"""

import contextvars
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta

import database

logger = logging.getLogger(__name__)

_journey: contextvars.ContextVar[str | None] = contextvars.ContextVar("journey_id", default=None)
_client_ip: contextvars.ContextVar[str | None] = contextvars.ContextVar("client_ip", default=None)

TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() in ("1", "true", "yes")
_JOURNEY_RE = re.compile(r"^[A-Za-z0-9-]{8,40}$")


# ── Tables ────────────────────────────────────────────────────────────────────

def ensure_tables() -> None:
    conn = database.get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS search_log (
            id               BIGINT AUTO_INCREMENT PRIMARY KEY,
            searched_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            journey_id       VARCHAR(40),
            search_kind      VARCHAR(20) NOT NULL DEFAULT 'search',
            ip               VARCHAR(45),
            user_agent       VARCHAR(255),
            customer_id      INT NULL,
            trip_type        VARCHAR(12),
            origin           VARCHAR(3),
            destination      VARCHAR(3),
            departure_date   VARCHAR(10),
            return_date      VARCHAR(10),
            legs_json        TEXT,
            adults           TINYINT, children TINYINT, infants TINYINT, students TINYINT,
            cabin            VARCHAR(20),
            providers_json   TEXT,
            result_count     INT NOT NULL DEFAULT 0,
            cheapest_price   DOUBLE NULL,
            cheapest_currency VARCHAR(5),
            cheapest_airline VARCHAR(100),
            duration_ms      INT,
            error_message    VARCHAR(500),
            offers_json      MEDIUMTEXT,
            INDEX idx_search_time (searched_at),
            INDEX idx_search_ip (ip),
            INDEX idx_search_customer (customer_id),
            INDEX idx_search_route (origin, destination)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS booking_journeys (
            journey_id     VARCHAR(40) PRIMARY KEY,
            started_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            ip             VARCHAR(45),
            customer_id    INT NULL,
            route          VARCHAR(120),
            travel_date    VARCHAR(10),
            airline        VARCHAR(100),
            flight         VARCHAR(80),
            fare_source    VARCHAR(10),
            amount         DOUBLE NULL,
            currency       VARCHAR(5),
            customer_name  VARCHAR(150),
            customer_email VARCHAR(200),
            customer_phone VARCHAR(30),
            locator_code   VARCHAR(20),
            status         VARCHAR(20) NOT NULL DEFAULT 'searching',
            last_step      VARCHAR(60),
            failed_step    VARCHAR(60),
            failure_seen_at DATETIME NULL,
            INDEX idx_journey_updated (updated_at),
            INDEX idx_journey_status (status),
            INDEX idx_journey_ip (ip)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS booking_journey_steps (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY,
            journey_id  VARCHAR(40) NOT NULL,
            at          DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
            step        VARCHAR(60) NOT NULL,
            status      VARCHAR(10) NOT NULL,
            detail      VARCHAR(1000),
            raw_error   TEXT,
            INDEX idx_steps_journey (journey_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS blocked_ips (
            ip          VARCHAR(45) PRIMARY KEY,
            reason      VARCHAR(300),
            blocked_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            blocked_by  VARCHAR(100)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_messages (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            email       VARCHAR(200) NOT NULL,
            kind        VARCHAR(30) NOT NULL,
            ref_key     VARCHAR(40) NOT NULL,
            channel     VARCHAR(10) NOT NULL,
            sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            ok          TINYINT(1) NOT NULL DEFAULT 1,
            error       VARCHAR(300),
            sent_by     VARCHAR(100),
            UNIQUE KEY uq_customer_message (email, kind, ref_key, channel)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    # Per supplier: status, fare count and cheapest fare of each search (Search Log columns).
    cur.execute("SHOW COLUMNS FROM search_log LIKE 'provider_best_json'")
    col = cur.fetchall()
    if not col:
        cur.execute("ALTER TABLE search_log ADD COLUMN provider_best_json TEXT NULL AFTER providers_json")
    elif "varchar" in str(col[0][1]).lower():  # per-channel detail (GDS / NDC) needs more room
        cur.execute("ALTER TABLE search_log MODIFY provider_best_json TEXT NULL")
    # Airline Rates Logs: what each supplier said besides its fares (Travelport warnings).
    cur.execute("SHOW COLUMNS FROM search_log LIKE 'supplier_messages_json'")
    if not cur.fetchall():
        cur.execute("ALTER TABLE search_log ADD COLUMN supplier_messages_json TEXT NULL AFTER provider_best_json")
    # Supplier alerts: the search (journey) that raised it, and who the email went to.
    for name, ddl in (("journey_id", "VARCHAR(40) NULL"), ("delivery_json", "TEXT NULL")):
        cur.execute(f"SHOW COLUMNS FROM provider_notifications LIKE '{name}'")
        if not cur.fetchall():
            cur.execute(f"ALTER TABLE provider_notifications ADD COLUMN {name} {ddl}")
    conn.commit()
    cur.close()
    conn.close()
    threading.Thread(target=_backfill_provider_best, name="search-log-backfill", daemon=True).start()


def _q(sql: str, params: tuple = (), *, one: bool = False, write: bool = False):
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        if write:
            conn.commit()
            return cur.lastrowid
        return cur.fetchone() if one else cur.fetchall()
    finally:
        cur.close()
        conn.close()


# ── Request context ───────────────────────────────────────────────────────────

def client_ip_of(request) -> str:
    if TRUST_PROXY_HEADERS:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


def bind_request(request):
    """Called by the main.py middleware for every request. Returns the tokens
    to reset afterwards."""
    jid = (request.headers.get("x-journey-id") or "").strip()
    return (_journey.set(jid if _JOURNEY_RE.match(jid) else None), _client_ip.set(client_ip_of(request)))


def unbind_request(tokens) -> None:
    _journey.reset(tokens[0])
    _client_ip.reset(tokens[1])


def current_journey() -> str | None:
    return _journey.get()


def current_ip() -> str | None:
    return _client_ip.get()


# ── Blocked IPs ───────────────────────────────────────────────────────────────

_blocked_cache = {"at": None, "ips": set()}


def is_blocked(ip: str) -> bool:
    now = datetime.now()
    if not _blocked_cache["at"] or (now - _blocked_cache["at"]).total_seconds() > 30:
        try:
            _blocked_cache["ips"] = {r["ip"] for r in _q("SELECT ip FROM blocked_ips")}
            _blocked_cache["at"] = now
        except Exception as e:
            logger.warning(f"blocked_ips lookup failed: {e}")
    return ip in _blocked_cache["ips"]


def block_ip(ip: str, reason: str, by: str) -> None:
    _q("INSERT INTO blocked_ips (ip, reason, blocked_by) VALUES (%s,%s,%s) "
       "ON DUPLICATE KEY UPDATE reason=VALUES(reason), blocked_by=VALUES(blocked_by), blocked_at=NOW()",
       (ip, (reason or "")[:300], by), write=True)
    _blocked_cache["at"] = None


def unblock_ip(ip: str) -> None:
    _q("DELETE FROM blocked_ips WHERE ip=%s", (ip,), write=True)
    _blocked_cache["at"] = None


# ── Journeys ──────────────────────────────────────────────────────────────────

_INFO_COLUMNS = ("customer_id", "route", "travel_date", "airline", "flight", "fare_source", "amount",
                 "currency", "customer_name", "customer_email", "customer_phone", "locator_code")


def journey_info(**fields) -> None:
    """Fills in what the journey is about (route, flight, customer...).
    Silently does nothing outside a journey; never breaks the booking."""
    jid = current_journey()
    if not jid:
        return
    fields = {k: v for k, v in fields.items() if k in _INFO_COLUMNS and v not in (None, "")}
    try:
        _ensure_journey(jid)
        if fields:
            sets = ", ".join(f"{k}=%s" for k in fields)
            _q(f"UPDATE booking_journeys SET {sets} WHERE journey_id=%s", (*fields.values(), jid), write=True)
    except Exception as e:
        logger.warning(f"journey_info failed for {jid}: {e}")


def _ensure_journey(jid: str) -> None:
    _q("INSERT IGNORE INTO booking_journeys (journey_id, ip) VALUES (%s,%s)", (jid, current_ip()), write=True)


def mark(step: str, status: str, detail: str | None = None, raw_error: str | None = None,
         journey_id: str | None = None) -> None:
    """Records one step of the journey as 'pass' or 'fail'."""
    jid = journey_id or current_journey()
    if not jid:
        return
    try:
        _ensure_journey(jid)
        _q("INSERT INTO booking_journey_steps (journey_id, step, status, detail, raw_error) VALUES (%s,%s,%s,%s,%s)",
           (jid, step[:60], status, (detail or "")[:1000] or None, raw_error), write=True)
        if status == "fail":
            _q("UPDATE booking_journeys SET status='failed', last_step=%s, failed_step=%s, failure_seen_at=NULL "
               "WHERE journey_id=%s", (step[:60], step[:60], jid), write=True)
        else:
            new_status = "ticketed" if step == "Ticket issued" else (
                "searching" if step == "Search" else "booking")
            _q("UPDATE booking_journeys SET last_step=%s, "
               "status=IF(status IN ('ticketed'), status, %s) WHERE journey_id=%s",
               (step[:60], new_status, jid), write=True)
    except Exception as e:
        logger.warning(f"journey step '{step}' not recorded for {jid}: {e}")


# Customer-facing endpoints → the step they represent. The middleware marks
# each one pass/fail from the HTTP result (failures keep the exact message
# the customer saw); code inside adds finer steps (payment verified, ticket).
ENDPOINT_STEPS = [
    (re.compile(r"^/api/flights/search$"), "Search"),
    (re.compile(r"^/api/bookings/initiate$"), "Passenger details"),
    (re.compile(r"^/api/bookings/confirm$"), "Booking created (PNR)"),
    (re.compile(r"^/api/ndc/book$"), "Price confirmed / booking prepared"),
    (re.compile(r"^/api/payments/init$"), "Payment page opened"),
    (re.compile(r"^/api/bookings/[A-Za-z0-9]+/issue-ticket$"), "Payment & ticketing"),
    (re.compile(r"^/api/ndc/instant-pay/complete$"), "Payment & ticketing"),
]


def endpoint_step(method: str, path: str) -> str | None:
    if method != "POST":
        return None
    for rx, name in ENDPOINT_STEPS:
        if rx.match(path):
            return name
    return None


# Steps the website itself reports (things that happen only in the browser).
CLIENT_STEPS = {"Flight selected", "Payment returned", "Payment cancelled", "Left the payment page",
                "Fare re-checked", "Continued with new fare"}


# ── Search log ────────────────────────────────────────────────────────────────

def _offer_summary(o: dict, rank: int) -> dict:
    legs = o.get("legs") or [o]
    flights, times = [], []
    for leg in legs:
        segs = leg.get("segments") or []
        flights.append("/".join(s.get("flight_number") or "" for s in segs) or leg.get("flight_number"))
        times.append(f"{(leg.get('departure_time') or '')[:16]}→{(leg.get('arrival_time') or '')[:16]}")
    return {
        "rank": rank, "source": o.get("source"), "airline": o.get("airline"), "code": o.get("airline_code"),
        "flights": flights, "times": times, "stops": [leg.get("stops", 0) for leg in legs],
        "price": o.get("price"), "currency": o.get("currency"), "fare_source": o.get("fare_source"),
        "cabin": o.get("cabin_class"),
        "brand": ((o.get("fare_options") or [{}])[0] or {}).get("brand_name"),
    }


SUPPLIERS = ("TP", "AD")
CHANNELS = ("GDS", "NDC")


def _cheapest(offers: list) -> dict:
    priced = [o for o in offers if isinstance(o.get("price"), (int, float))]
    best = min(priced, key=lambda o: o["price"]) if priced else None
    return {"count": len(offers), "price": best["price"] if best else None,
            "currency": best.get("currency") if best else None,
            "airline": (best.get("airline") or "")[:60] if best else None}


def provider_best(providers: dict, offers: list) -> dict:
    """Per supplier, from what it actually returned: status ('ok' fares came
    back, 'empty' no fares and no error, 'error', 'off' not asked because it
    is switched off / not wired for this trip), fare count and its cheapest
    fare — overall and per channel (GDS / NDC). Works on the live offers and
    on the stored offer summaries."""
    out = {}
    for tag in SUPPLIERS:
        state = str((providers or {}).get(tag) or "not queried")
        mine = [o for o in offers if o.get("source") == tag]
        status = ("off" if state == "not queried" else "error" if state.startswith("error")
                  else "ok" if mine else "empty")
        out[tag] = {"status": status, **_cheapest(mine),
                    "channels": {ch: _cheapest([o for o in mine if (o.get("fare_source") or "GDS").upper() == ch])
                                 for ch in CHANNELS}}
        if status == "error":
            out[tag]["error"] = state[len("error: "):][:200]
    return out


def _backfill_provider_best() -> None:
    """Fills provider_best_json for searches logged before the column existed
    (from their stored providers and ranked results). Runs once in the
    background; new searches get it when they are logged."""
    done = 0
    try:
        while True:
            rows = _q("SELECT id, providers_json, offers_json FROM search_log "
                      "WHERE provider_best_json IS NULL OR provider_best_json NOT LIKE '%%\"channels\"%%' "
                      "ORDER BY id DESC LIMIT 200")
            if not rows:
                break
            for r in rows:
                try:
                    best = provider_best(json.loads(r["providers_json"] or "{}"), json.loads(r["offers_json"] or "[]"))
                except Exception:
                    best = provider_best({}, [])
                _q("UPDATE search_log SET provider_best_json=%s WHERE id=%s", (json.dumps(best), r["id"]), write=True)
            done += len(rows)
        if done:
            logger.info(f"Search Log: supplier status / cheapest fare filled in for {done} earlier searches")
    except Exception as e:
        logger.warning(f"Search Log backfill stopped after {done} rows: {e}")


def log_search(req, offers: list, providers: dict, duration_ms: int, error: str | None,
               customer_id: int | None, user_agent: str | None, kind: str = "search",
               messages: dict | None = None) -> None:
    """One row per search with the ranked results exactly as returned.
    messages: {"TP": [warning, ...]} — what a supplier said besides its fares."""
    try:
        legs = [l.model_dump() for l in req.legs] if getattr(req, "legs", None) else None
        trip = "multicity" if legs and len(legs) > 2 else "roundtrip" if legs and len(legs) == 2 else "oneway"
        origin = (req.origin or (legs[0]["origin"] if legs else "") or "").upper()[:3]
        dest = (req.destination or (legs[0]["destination"] if legs else "") or "").upper()[:3]
        dep = req.departure_date or (legs[0]["departure_date"] if legs else None)
        ret = legs[1]["departure_date"] if trip == "roundtrip" else None
        cheapest = offers[0] if offers else None
        _q("""INSERT INTO search_log (journey_id, search_kind, ip, user_agent, customer_id, trip_type, origin,
                destination, departure_date, return_date, legs_json, adults, children, infants, students, cabin,
                providers_json, provider_best_json, supplier_messages_json, result_count, cheapest_price,
                cheapest_currency, cheapest_airline, duration_ms, error_message, offers_json)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
           (current_journey() if kind in ("search", "recheck") else None, kind, current_ip(), (user_agent or "")[:255],
            customer_id, trip, origin, dest, dep, ret, json.dumps(legs) if legs else None,
            req.adult_count, req.child_count, req.infant_count, getattr(req, "student_count", 0),
            req.cabin_preference or "All", json.dumps(providers), json.dumps(provider_best(providers, offers)),
            json.dumps(messages) if messages else None, len(offers),
            cheapest.get("price") if cheapest else None, cheapest.get("currency") if cheapest else None,
            cheapest.get("airline") if cheapest else None, duration_ms, (error or "")[:500] or None,
            json.dumps([_offer_summary(o, i + 1) for i, o in enumerate(offers)])),
           write=True)
        if kind == "search" and current_journey():
            journey_info(customer_id=customer_id, route=f"{origin} → {dest}" + (" (return)" if ret else ""),
                         travel_date=dep)
    except Exception as e:
        logger.warning(f"search_log insert failed: {e}")


# ── Admin queries ─────────────────────────────────────────────────────────────

def _search_filters(days: int, ip: str | None, route: str | None, customer_id: int | None):
    where, params = ["searched_at >= %s"], [datetime.now() - timedelta(days=days)]
    if ip:
        where.append("ip=%s"); params.append(ip)
    if customer_id:
        where.append("customer_id=%s"); params.append(customer_id)
    if route:
        parts = [p.strip().upper() for p in re.split(r"[-→> ]+", route) if p.strip()]
        if parts:
            where.append("origin=%s"); params.append(parts[0])
        if len(parts) > 1:
            where.append("destination=%s"); params.append(parts[1])
    return where, params


def list_searches(days: int = 7, ip: str | None = None, route: str | None = None,
                  customer_id: int | None = None, limit: int = 200) -> list[dict]:
    where, params = _search_filters(days, ip, route, customer_id)
    return _q(f"""SELECT id, searched_at, journey_id, search_kind, ip, customer_id, trip_type, origin, destination,
                         departure_date, return_date, adults, children, infants, students, cabin, providers_json,
                         provider_best_json, result_count, cheapest_price, cheapest_currency, cheapest_airline,
                         duration_ms, error_message
                  FROM search_log WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s""",
              (*params, min(limit, 1000)))


def supplier_summary(days: int = 7, ip: str | None = None, route: str | None = None,
                     customer_id: int | None = None) -> dict:
    """Customer searches (not ±day checks or re-checks) in the period, per
    supplier: how many brought fares, came back empty, failed or didn't ask
    that supplier — the same filters as the list."""
    where, params = _search_filters(days, ip, route, customer_id)
    rows = _q(f"SELECT provider_best_json FROM search_log WHERE {' AND '.join(where)} AND search_kind='search'",
              tuple(params))
    out = {tag: {"ok": 0, "empty": 0, "error": 0, "off": 0} for tag in SUPPLIERS}
    for r in rows:
        try:
            best = json.loads(r["provider_best_json"] or "{}")
        except ValueError:
            continue
        for tag in SUPPLIERS:
            status = (best.get(tag) or {}).get("status")
            if status in out[tag]:
                out[tag][status] += 1
    return {"searches": len(rows), **out}


# ── Airline Rates Logs ────────────────────────────────────────────────────────
# One row per customer search: what each supplier sent per channel (GDS / NDC),
# the lowest fare, and the supplier alerts it raised — so staff can show a
# supplier exactly what it returned, and see when a channel stops coming.

def channel_seen_recently(origin: str, destination: str, supplier: str, channel: str, days: int = 7) -> bool:
    """True when this supplier's channel had fares on this route in the last
    `days` days (customer searches and ±day checks)."""
    if not origin or not destination:
        return False
    rows = _q("""SELECT provider_best_json FROM search_log
                 WHERE origin=%s AND destination=%s AND searched_at >= %s AND search_kind IN ('search','nearby')
                   AND provider_best_json LIKE %s
                 ORDER BY id DESC LIMIT 60""",
              (origin, destination, datetime.now() - timedelta(days=days), '%"channels"%'))
    for r in rows:
        try:
            if (((json.loads(r["provider_best_json"]) or {}).get(supplier) or {}).get("channels") or {}).get(channel, {}).get("count"):
                return True
        except (ValueError, AttributeError):
            continue
    return False


def _channel_states(best: dict, alerts: list) -> dict:
    """{"TP": {"GDS": "ok"|"none"|"missing"|"error"|"empty"|"off", "NDC": ...}, "AD": ...}"""
    out = {}
    for tag in SUPPLIERS:
        b = best.get(tag) or {}
        st = b.get("status") or "off"
        chans = b.get("channels") or {}
        row = {}
        for ch in CHANNELS:
            if st in ("off", "error", "empty"):
                row[ch] = st
            elif (chans.get(ch) or {}).get("count"):
                row[ch] = "ok"
            else:
                missing = any(a["provider"] == tag and f"NO {ch} FARES" in (a.get("error_message") or "").upper()
                              for a in alerts)
                row[ch] = "missing" if missing else "none"
        out[tag] = row
    return out


def _lowest(best: dict) -> dict | None:
    found = []
    for tag in SUPPLIERS:
        for ch, c in ((best.get(tag) or {}).get("channels") or {}).items():
            if isinstance(c.get("price"), (int, float)):
                found.append({"price": c["price"], "currency": c.get("currency"), "airline": c.get("airline"),
                              "supplier": tag, "channel": ch})
    return min(found, key=lambda x: x["price"]) if found else None


def _alert_matches(alert: dict, s: dict) -> bool:
    from services import supplier_alerts
    at, when = alert.get("created_at"), s.get("searched_at")
    if not at or not when or not (when - timedelta(minutes=5) <= at <= when + timedelta(seconds=60)):
        return False
    if alert.get("journey_id") and s.get("journey_id"):
        return alert["journey_id"] == s["journey_id"]
    route, dates = supplier_alerts._route(alert.get("route_info"))
    codes = (route or "").split("–")
    return bool(codes) and codes[0] == s.get("origin") and len(codes) > 1 and codes[1] == s.get("destination") \
        and (not dates or dates[0] == (s.get("departure_date") or "")[:10])


def rates_logs(days: int = 7, problems_only: bool = False, supplier: str | None = None, limit: int = 300) -> dict:
    since = datetime.now() - timedelta(days=days)
    searches = _q("""SELECT id, searched_at, journey_id, origin, destination, departure_date, return_date, trip_type,
                            adults, children, infants, students, cabin, provider_best_json, result_count,
                            cheapest_price, cheapest_currency, cheapest_airline, error_message
                     FROM search_log WHERE searched_at >= %s AND search_kind='search' ORDER BY id DESC LIMIT 3000""",
                  (since,))
    alerts = _q("SELECT * FROM provider_notifications WHERE created_at >= %s ORDER BY id DESC", (since - timedelta(minutes=5),))
    used, rows = set(), []
    for s in searches:
        mine = [a for a in alerts if a["id"] not in used and _alert_matches(a, s)]
        used.update(a["id"] for a in mine)
        try:
            best = json.loads(s.pop("provider_best_json") or "{}") or {}
        except ValueError:
            best = {}
        states = _channel_states(best, mine)
        problem = bool(mine) or any(v in ("error", "empty", "missing") for st in states.values() for v in st.values())
        rows.append({**s, "channels": states, "lowest": _lowest(best),
                     "alerts": [{"id": a["id"], "provider": a["provider"], "status": a["status"]} for a in mine],
                     "problem": problem})
    # Alerts that match no logged search (older than the search log, or a
    # search that failed before it was logged) still get a row each.
    from services import supplier_alerts
    for a in supplier_alerts.review([a for a in alerts if a["id"] not in used]):
        route, dates = a.get("route"), a.get("travel_dates") or []
        codes = (route or "").split("–")
        rows.append({"id": None, "alert_only": True, "searched_at": a["created_at"], "origin": codes[0] if codes else None,
                     "destination": codes[1] if len(codes) > 1 else None, "departure_date": dates[0] if dates else None,
                     "return_date": dates[1] if len(dates) > 1 else None,
                     "channels": {t: ({ch: "error" for ch in CHANNELS} if t == a["provider"] else {ch: "unknown" for ch in CHANNELS})
                                  for t in SUPPLIERS},
                     "lowest": None, "problem": True, "summary": a.get("summary"),
                     "alerts": [{"id": a["id"], "provider": a["provider"], "status": a["status"]}]})
    rows.sort(key=lambda r: r["searched_at"] or datetime.min, reverse=True)
    for r in rows:
        st = [a["status"] for a in r["alerts"]]
        r["action"] = "pending" if "new" in st else ("acknowledged" if st else None)
    if problems_only:
        rows = [r for r in rows if r["problem"]]
    if supplier in SUPPLIERS:
        rows = [r for r in rows if any(a["provider"] == supplier for a in r["alerts"])
                or any(v not in ("off", "unknown") for v in r["channels"][supplier].values())]
    counts = {"searches": len(searches), "problems": sum(1 for r in rows if r["problem"]),
              "pending_alerts": sum(1 for a in alerts if a["status"] == "new")}
    return {"rows": rows[:limit], "counts": counts}


def rates_log_detail(search_id: int) -> dict | None:
    """Everything about one customer search for a supplier dispute or a fix:
    per-airline fares by supplier and channel, what the customer was shown and
    picked, each supplier's status/messages, and the alerts with delivery."""
    from services import supplier_alerts
    s = get_search(search_id)
    if not s:
        return None
    offers = s.pop("offers")
    best = s["provider_best"]
    # Airline-wise comparison: cheapest fare per supplier+channel for each airline.
    table = {}
    for o in offers:
        key = o.get("code") or o.get("airline") or "?"
        row = table.setdefault(key, {"airline": o.get("airline") or key, "code": o.get("code"), "fares": {}})
        cell = f"{o.get('source') or '?'}_{(o.get('fare_source') or 'GDS').upper()}"
        if isinstance(o.get("price"), (int, float)) and (cell not in row["fares"] or o["price"] < row["fares"][cell]["price"]):
            row["fares"][cell] = {"price": o["price"], "currency": o.get("currency"), "rank": o.get("rank")}
    comparison = []
    for row in table.values():
        cheapest = min(((k, v) for k, v in row["fares"].items()), key=lambda kv: kv[1]["price"], default=None)
        row["lowest"] = {"cell": cheapest[0], **cheapest[1]} if cheapest else None
        comparison.append(row)
    comparison.sort(key=lambda r: (r["lowest"] or {}).get("price") or float("inf"))
    # What the customer saw first, and what they picked.
    priced = [o for o in offers if isinstance(o.get("price"), (int, float))]
    lowest = min(priced, key=lambda o: o["price"]) if priced else None
    first = offers[0] if offers else None
    display = {"first_shown": first, "lowest": lowest,
               "lowest_shown_first": bool(first and lowest and first.get("price") == lowest.get("price"))}
    picked = None
    if s.get("journey_id"):
        j = _q("SELECT airline, flight, fare_source, amount, currency, status, locator_code FROM booking_journeys "
               "WHERE journey_id=%s", (s["journey_id"],), one=True)
        step = _q("SELECT detail, at FROM booking_journey_steps WHERE journey_id=%s AND step='Flight selected' "
                  "AND at >= %s ORDER BY id LIMIT 1", (s["journey_id"], s["searched_at"]), one=True)
        if j and (j.get("flight") or step):
            picked = {**j, "selected": (step or {}).get("detail")}
    display["customer_picked"] = picked
    # Supplier technical details and the alerts this search raised.
    try:
        messages = json.loads(s.pop("supplier_messages_json", None) or "{}") or {}
    except ValueError:
        messages = {}
    alerts = _q("SELECT * FROM provider_notifications WHERE created_at BETWEEN %s AND %s ORDER BY id",
                (s["searched_at"] - timedelta(minutes=5), s["searched_at"] + timedelta(seconds=60)))
    alerts = [a for a in alerts if _alert_matches(a, s)]
    reviewed = supplier_alerts.review(alerts)
    for a in reviewed:
        try:
            a["delivery"] = json.loads(a.pop("delivery_json") or "null")
        except ValueError:
            a["delivery"] = None
    s.pop("user_agent", None)
    return {"search": s, "channels": _channel_states(best, alerts), "comparison": comparison,
            "display": display, "messages": messages, "alerts": reviewed,
            "recipients_now": [r["email"] for r in database.get_notification_recipients(active_only=True)]}


def get_search(search_id: int) -> dict | None:
    row = _q("SELECT * FROM search_log WHERE id=%s", (search_id,), one=True)
    if row:
        row["offers"] = json.loads(row.pop("offers_json") or "[]")
        row["providers"] = json.loads(row.pop("providers_json") or "{}")
        row["provider_best"] = provider_best(row["providers"], row["offers"])
        row["legs"] = json.loads(row.pop("legs_json") or "null")
    return row


def list_journeys(days: int = 7, status: str | None = None, limit: int = 200) -> list[dict]:
    where, params = ["updated_at >= %s"], [datetime.now() - timedelta(days=days)]
    if status:
        where.append("status=%s"); params.append(status)
    rows = _q(f"""SELECT * FROM booking_journeys WHERE {' AND '.join(where)}
                  AND status <> 'searching' ORDER BY updated_at DESC LIMIT %s""", (*params, min(limit, 1000)))
    # Failed ones get their short reason for the list (one query for all).
    failed = [r["journey_id"] for r in rows if r.get("status") == "failed"]
    if failed:
        marks = ",".join(["%s"] * len(failed))
        steps = _q(f"SELECT journey_id, step, status, detail, raw_error FROM booking_journey_steps "
                   f"WHERE journey_id IN ({marks}) AND status='fail' ORDER BY id", tuple(failed))
        by = {}
        for s in steps:
            by.setdefault(s["journey_id"], []).append(s)
        for r in rows:
            if r["journey_id"] in by:
                r["why_failed"] = explain_failure(by[r["journey_id"]])["title"]
    return rows


# Why a booking journey failed, in plain words for staff: what happened,
# whether the customer was charged, and what to do. Read from the failed
# steps' customer message and real error (the last failed step often only
# carries the customer message; the real error sits on an earlier one).
_FAILURE_RULES = [
    (("CONSUMER CANCELLED", "PAYMENT CANCELLED", "CODE=CANCEL", "CODE CANCEL"),
     "Customer cancelled on the payment page", "No",
     "Nothing to fix. The unpaid booking is released automatically; you may call the customer to help them finish."),
    (("DECLINE", "AUTHORIZATION DECLINED", "CODE=REVIEW", "NOT AUTHORI"),
     "The customer's bank declined the card", "No",
     "Nothing to fix on our side. The customer can try another card."),
    (("DID NOT CONFIRM YOUR TICKET", "PAID BUT NOT TICKETED", "NOT TICKETED", "AUTOMATIC FULL REFUND"),
     "Paid, but the airline didn't issue the ticket", "Yes — refund request created automatically",
     "Check Ticketing Exceptions and Cancellations: issue the ticket manually or complete the refund."),
    (("CHANGED THE TIMING", "CHANGED THE SCHEDULE", "SCHEDULE CHANGE"),
     "The airline changed the flight's schedule", "No",
     "Nothing to fix. The customer was asked to choose another flight; it can be booked again once the airline confirms the new timing."),
    (("FARE HAS CHANGED TO", "FARE HAS CHANGED AT THE AIRLINE"),
     "The price went up at the airline before payment", "No",
     "Nothing to fix. The customer was shown the new price; you may call them if they didn't continue."),
    (("NO AVAILABLE FARES", "NO LONGER AVAILABLE", "FARE OR AVAILABILITY HAS CHANGED", "UNABLE TO SELL", "SOLD OUT"),
     "The fare was no longer available at the airline", "No",
     "Nothing to fix. The seats at that price were gone; the customer needs another fare or flight."),
    (("JUST SUBMITTED", "ALREADY BEING", "ALREADY PRESENT"),
     "The same booking was submitted twice (double click or retry)", "No",
     "Check the customer's other attempt in this list — usually the first one went through."),
    (("504", "GATEWAY TIME", "TIMED OUT", "TIMEOUT", "FORCIBLY CLOSED", "WINERROR", "DIDN'T ANSWER IN TIME"),
     "The airline / Travelport didn't answer in time", "No",
     "Usually temporary — the customer can try again. If it keeps happening for one airline, tell Travelport."),
    (("MINIMUM CONNECTING TIME", "DATE SEQUENCE", "FLIGHT DOES NOT EXIST", "DOES NOT OPERATE"),
     "The airline rejected this flight combination (connection time, dates or a flight not operating)", "No",
     "Nothing to fix for this customer — they need another itinerary. If search keeps offering it, tell Travelport."),
    (("SYSTEM ERROR", "INTERNAL SERVER ERROR"),
     "Travelport had a system error", "No",
     "Usually temporary — the customer can try again. If it repeats, send the real error to Travelport."),
    (("PASSPORT", "DATE OF BIRTH", "NAME IS REQUIRED", "INVALID NAME", "NAME TOO"),
     "The airline rejected the passenger details", "No",
     "Check the passenger's name, date of birth and passport details with the customer."),
]


def explain_failure(fail_steps: list) -> dict:
    """{title, charged, action, error} for a failed journey's fail steps."""
    text = " ".join(f"{s.get('detail') or ''} {s.get('raw_error') or ''}" for s in fail_steps).upper()
    real = next((s.get("raw_error") for s in reversed(fail_steps) if s.get("raw_error")), None) \
        or next((s.get("detail") for s in reversed(fail_steps) if s.get("detail")), "")
    for markers, title, charged, action in _FAILURE_RULES:
        if any(m in text for m in markers):
            return {"title": title, "charged": charged, "action": action, "error": (real or "")[:600]}
    return {"title": "The airline / Travelport refused the booking", "charged": "No",
            "action": "Read the real error below; if it isn't clear, send it to Travelport with the journey time.",
            "error": (real or "")[:600]}


def get_journey(jid: str) -> dict | None:
    j = _q("SELECT * FROM booking_journeys WHERE journey_id=%s", (jid,), one=True)
    if j:
        j["steps"] = _q("SELECT at, step, status, detail, raw_error FROM booking_journey_steps "
                        "WHERE journey_id=%s ORDER BY id", (jid,))
        j["searches"] = _q("SELECT id, searched_at, origin, destination, departure_date, return_date, result_count, "
                           "cheapest_price, cheapest_currency FROM search_log WHERE journey_id=%s ORDER BY id", (jid,))
        fails = [s for s in j["steps"] if s.get("status") == "fail"]
        if j.get("status") == "failed" and fails:
            j["why_failed"] = explain_failure(fails)
    return j


def mark_failures_seen() -> None:
    _q("UPDATE booking_journeys SET failure_seen_at=NOW() WHERE status='failed' AND failure_seen_at IS NULL",
       write=True)


# IP watch thresholds (searches are real customer searches; nothing inferred
# beyond counting them).
WATCH_SAME_ROUTE = 3        # same route searched this many times in the window, no ticket → price watcher
WATCH_VOLUME_24H = 60       # searches from one IP in 24h → unusual volume
WATCH_ROUTES_24H = 15       # different routes from one IP in 24h → unusual spread
WATCH_FAILED = 3            # failed booking journeys from one IP in the window


def ip_watch(days: int = 7) -> list[dict]:
    since = datetime.now() - timedelta(days=days)
    day = datetime.now() - timedelta(hours=24)
    rows = _q("""SELECT ip, COUNT(*) searches, COUNT(DISTINCT CONCAT(origin,'-',destination)) routes,
                        SUM(searched_at >= %s) searches_24h,
                        COUNT(DISTINCT IF(searched_at >= %s, CONCAT(origin,'-',destination), NULL)) routes_24h,
                        MIN(searched_at) first_seen, MAX(searched_at) last_seen,
                        MAX(customer_id) customer_id
                 FROM search_log WHERE searched_at >= %s AND search_kind='search' AND ip IS NOT NULL
                 GROUP BY ip ORDER BY searches DESC LIMIT 500""", (day, day, since))
    if not rows:
        return []
    ips = [r["ip"] for r in rows]
    marks = ",".join(["%s"] * len(ips))
    top = _q(f"""SELECT ip, origin, destination, COUNT(*) n, MIN(cheapest_price) lowest_seen,
                        MAX(cheapest_currency) currency, MAX(departure_date) last_date, MAX(return_date) last_return
                 FROM search_log WHERE searched_at >= %s AND search_kind='search' AND ip IN ({marks})
                 GROUP BY ip, origin, destination ORDER BY n DESC""", (since, *ips))
    jstats = _q(f"""SELECT ip, SUM(status='ticketed') ticketed, SUM(status='failed') failed,
                           MAX(customer_email) email, MAX(customer_name) name
                    FROM booking_journeys WHERE updated_at >= %s AND ip IN ({marks}) GROUP BY ip""", (since, *ips))
    blocked = {r["ip"]: r for r in _q("SELECT * FROM blocked_ips")}
    by_ip_top, by_ip_j = {}, {r["ip"]: r for r in jstats}
    for t in top:
        by_ip_top.setdefault(t["ip"], []).append(t)
    out = []
    for r in rows:
        tops = by_ip_top.get(r["ip"], [])
        j = by_ip_j.get(r["ip"], {})
        ticketed, failed = int(j.get("ticketed") or 0), int(j.get("failed") or 0)
        flags = []
        if tops and tops[0]["n"] >= WATCH_SAME_ROUTE and not ticketed:
            flags.append({"kind": "price_watcher",
                          "text": f"Searched {tops[0]['origin']}→{tops[0]['destination']} {tops[0]['n']} times without booking"})
        if int(r["searches_24h"] or 0) >= WATCH_VOLUME_24H:
            flags.append({"kind": "volume", "text": f"{r['searches_24h']} searches in the last 24 hours"})
        if int(r["routes_24h"] or 0) >= WATCH_ROUTES_24H:
            flags.append({"kind": "spread", "text": f"{r['routes_24h']} different routes in the last 24 hours"})
        if failed >= WATCH_FAILED:
            flags.append({"kind": "failures", "text": f"{failed} failed booking attempts"})
        email = j.get("email")
        if not email and r.get("customer_id"):
            c = _q("SELECT email, full_name FROM customers WHERE id=%s", (r["customer_id"],), one=True)
            if c:
                email, j = c["email"], {**j, "name": c["full_name"]}
        out.append({**r, "top_routes": tops[:5], "ticketed": ticketed, "failed": failed, "email": email,
                    "name": j.get("name"), "flags": flags,
                    "blocked": blocked.get(r["ip"])})
    return out


# ── Customer 360 ──────────────────────────────────────────────────────────────

def _bookings_with_travelers(where: str = "", params: tuple = ()) -> list[dict]:
    rows = _q(f"""SELECT id, locator_code, status, passenger_name, passenger_email, customer_id, airline,
                         departure_airport, arrival_airport, departure_time, cabin_class, total_fare, currency,
                         booking_date, created_at, fare_source, raw_ticket_json
                  FROM bookings {where} ORDER BY id DESC""", params)
    for r in rows:
        try:
            raw = json.loads(r.pop("raw_ticket_json") or "{}")
        except ValueError:
            raw = {}
        r["travelers"] = raw.get("travelers") or []
        r["legs"] = raw.get("legs") or []
        r["phone"] = raw.get("phone")
    return rows


def customers_360(search: str | None = None) -> list[dict]:
    """Every person who booked (lead passenger email) or has an account,
    with what their own bookings and searches say about them."""
    bookings = _bookings_with_travelers()
    people: dict[str, dict] = {}
    for b in bookings:
        email = (b.get("passenger_email") or "").strip().lower()
        if not email:
            continue
        p = people.setdefault(email, {"email": email, "name": b.get("passenger_name"), "phone": b.get("phone"),
                                      "customer_id": b.get("customer_id"), "bookings": 0, "ticketed": 0,
                                      "cancelled": 0, "spend": {}, "routes": {}, "airlines": {}, "cabins": {},
                                      "months": {}, "lead_days": [], "last_travel": None, "first_booking": None,
                                      "date_of_birth": None, "passport_expiry": None, "nationality": None,
                                      "companions": set()})
        p["bookings"] += 1
        status = (b.get("status") or "").lower()
        if "ticket" in status:
            p["ticketed"] += 1
            p["spend"][b.get("currency") or ""] = p["spend"].get(b.get("currency") or "", 0) + float(b.get("total_fare") or 0)
        if "cancel" in status or "void" in status or "refund" in status:
            p["cancelled"] += 1
        out_leg = (b.get("legs") or [{}])[0] or {}
        route = f"{out_leg.get('departure_airport') or b.get('departure_airport')}→"                 f"{out_leg.get('arrival_airport') or b.get('arrival_airport')}" + (" (return)" if len(b.get("legs") or []) == 2 else "")
        p["routes"][route] = p["routes"].get(route, 0) + 1
        if b.get("airline"):
            p["airlines"][b["airline"]] = p["airlines"].get(b["airline"], 0) + 1
        if b.get("cabin_class"):
            p["cabins"][b["cabin_class"]] = p["cabins"].get(b["cabin_class"], 0) + 1
        dep = str(b.get("departure_time") or "")[:10]
        if dep:
            month = datetime.strptime(dep, "%Y-%m-%d").strftime("%b") if re.match(r"\d{4}-\d\d-\d\d", dep) else None
            if month:
                p["months"][month] = p["months"].get(month, 0) + 1
            p["last_travel"] = max(p["last_travel"] or dep, dep)
            booked = b.get("created_at")
            if booked and re.match(r"\d{4}-\d\d-\d\d", dep):
                p["lead_days"].append((datetime.strptime(dep, "%Y-%m-%d") - booked).days)
        created = str(b.get("created_at") or "")[:10]
        p["first_booking"] = min(p["first_booking"] or created, created) if created else p["first_booking"]
        for t in b["travelers"]:
            if (t.get("email") or "").strip().lower() == email or len(b["travelers"]) == 1:
                p["date_of_birth"] = p["date_of_birth"] or t.get("date_of_birth")
                p["passport_expiry"] = max(p["passport_expiry"] or "", t.get("passport_expiry") or "") or None
                p["nationality"] = p["nationality"] or t.get("nationality")
            else:
                p["companions"].add(f"{t.get('first_name', '')} {t.get('last_name', '')}".strip())
    for c in _q("SELECT id, email, full_name, phone, created_at FROM customers"):
        email = (c["email"] or "").strip().lower()
        p = people.setdefault(email, {"email": email, "name": c["full_name"], "phone": c["phone"], "bookings": 0,
                                      "ticketed": 0, "cancelled": 0, "spend": {}, "routes": {}, "airlines": {},
                                      "cabins": {}, "months": {}, "lead_days": [], "last_travel": None,
                                      "first_booking": None, "date_of_birth": None, "passport_expiry": None,
                                      "nationality": None, "companions": set()})
        p["customer_id"] = c["id"]
        p["account_since"] = str(c["created_at"])[:10]
    searches = {r["customer_id"]: r for r in _q(
        "SELECT customer_id, COUNT(*) n, MAX(searched_at) last FROM search_log "
        "WHERE customer_id IS NOT NULL GROUP BY customer_id")}
    out = []
    for p in people.values():
        top = lambda d: sorted(d.items(), key=lambda kv: -kv[1])
        s = searches.get(p.get("customer_id")) or {}
        row = {**p, "companions": sorted(x for x in p["companions"] if x)[:8],
               "routes": top(p["routes"])[:5], "airlines": top(p["airlines"])[:5], "cabins": top(p["cabins"]),
               "months": top(p["months"]),
               "avg_lead_days": round(sum(p["lead_days"]) / len(p["lead_days"])) if p["lead_days"] else None,
               "searches": s.get("n", 0), "last_search": str(s.get("last") or "")[:16] or None}
        row.pop("lead_days")
        out.append(row)
    if search:
        q = search.strip().lower()
        out = [r for r in out if q in (r["email"] or "") or q in (r.get("name") or "").lower()
               or q in (r.get("phone") or "")]
    out.sort(key=lambda r: (-r["ticketed"], -r["bookings"], r["email"]))
    return out


def customer_detail(email: str) -> dict:
    email = email.strip().lower()
    summary = next((c for c in customers_360() if c["email"] == email), None)
    bookings = _bookings_with_travelers("WHERE LOWER(passenger_email)=%s", (email,))
    for b in bookings:
        b.pop("travelers", None)
    cid = summary.get("customer_id") if summary else None
    journeys = _q("SELECT journey_id, started_at, route, airline, amount, currency, status, failed_step, locator_code "
                  "FROM booking_journeys WHERE LOWER(customer_email)=%s OR (customer_id IS NOT NULL AND customer_id=%s) "
                  "ORDER BY started_at DESC LIMIT 50", (email, cid or -1))
    searches = list_searches(days=365, customer_id=cid, limit=100) if cid else []
    messages = _q("SELECT kind, ref_key, channel, sent_at, ok, error FROM customer_messages WHERE email=%s "
                  "ORDER BY sent_at DESC", (email,))
    return {"summary": summary, "bookings": bookings, "journeys": journeys, "searches": searches,
            "messages": messages}


# ── Reminders (from real booking traveller data) ──────────────────────────────

def _all_travelers() -> list[dict]:
    """Every distinct traveller (by passport number, else name+DOB) with the
    latest details they gave, plus the booking contact."""
    seen: dict[str, dict] = {}
    for b in _bookings_with_travelers():
        for t in b["travelers"]:
            key = (t.get("passport_number") or "").upper() or \
                f"{t.get('first_name', '')}|{t.get('last_name', '')}|{t.get('date_of_birth', '')}".upper()
            if key in seen:
                continue
            seen[key] = {
                "name": f"{t.get('first_name', '')} {t.get('last_name', '')}".strip() or b.get("passenger_name"),
                "email": (t.get("email") or b.get("passenger_email") or "").strip().lower(),
                "phone": t.get("phone") or b.get("phone"),
                "date_of_birth": t.get("date_of_birth"), "passport_expiry": t.get("passport_expiry"),
                "passport_last4": (t.get("passport_number") or "")[-4:],
                "nationality": t.get("nationality"), "last_booking": b.get("locator_code"),
            }
    return list(seen.values())


def upcoming_birthdays(days: int = 30) -> list[dict]:
    today = datetime.now().date()
    out = []
    for t in _all_travelers():
        try:
            dob = datetime.strptime(t["date_of_birth"] or "", "%Y-%m-%d").date()
        except ValueError:
            continue
        try:
            nxt = dob.replace(year=today.year)
        except ValueError:          # 29 Feb
            nxt = dob.replace(year=today.year, day=28)
        if nxt < today:
            nxt = nxt.replace(year=today.year + 1)
        if (nxt - today).days <= days:
            out.append({**t, "birthday": nxt.isoformat(), "in_days": (nxt - today).days,
                        "turning": nxt.year - dob.year})
    _attach_sent(out, "birthday", lambda r: r["birthday"][:4])
    return sorted(out, key=lambda r: r["in_days"])


def expiring_passports(months: int = 6) -> list[dict]:
    today = datetime.now().date()
    limit = today + timedelta(days=int(months * 30.5))
    out = []
    for t in _all_travelers():
        try:
            exp = datetime.strptime(t["passport_expiry"] or "", "%Y-%m-%d").date()
        except ValueError:
            continue
        if exp <= limit:
            out.append({**t, "days_left": (exp - today).days, "expired": exp < today})
    _attach_sent(out, "passport", lambda r: r["passport_expiry"])
    return sorted(out, key=lambda r: r["days_left"])


def _attach_sent(rows: list[dict], kind: str, ref_fn) -> None:
    if not rows:
        return
    sent = {(r["email"], r["ref_key"]): r for r in _q(
        "SELECT email, ref_key, MAX(sent_at) sent_at FROM customer_messages WHERE kind=%s AND ok=1 "
        "GROUP BY email, ref_key", (kind,))}
    for r in rows:
        s = sent.get((r["email"], ref_fn(r)))
        r["sent_at"] = str(s["sent_at"])[:16] if s else None


def record_message(email: str, kind: str, ref_key: str, channel: str, ok: bool, error: str | None, by: str) -> None:
    _q("INSERT INTO customer_messages (email, kind, ref_key, channel, ok, error, sent_by) "
       "VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE ok=VALUES(ok), error=VALUES(error), "
       "sent_at=NOW(), sent_by=VALUES(sent_by)",
       (email, kind, ref_key, channel, 1 if ok else 0, (error or "")[:300] or None, by), write=True)


# ── Reports ───────────────────────────────────────────────────────────────────

def route_report(days: int = 30) -> list[dict]:
    """Searched vs booked per route — real searches and real tickets."""
    since = datetime.now() - timedelta(days=days)
    s = _q("""SELECT origin, destination, COUNT(*) searches, COUNT(DISTINCT ip) people,
                     MIN(cheapest_price) lowest, MAX(cheapest_currency) currency
              FROM search_log WHERE searched_at >= %s AND search_kind='search'
              GROUP BY origin, destination ORDER BY searches DESC LIMIT 50""", (since,))
    b = {(r["departure_airport"], r["arrival_airport"]): r["n"] for r in _q(
        "SELECT departure_airport, arrival_airport, COUNT(*) n FROM bookings WHERE created_at >= %s "
        "AND ticket_number IS NOT NULL AND ticket_number <> '' GROUP BY departure_airport, arrival_airport", (since,))}
    for r in s:
        r["ticketed"] = b.get((r["origin"], r["destination"]), 0)
        r["conversion"] = round(100 * r["ticketed"] / r["searches"], 1) if r["searches"] else 0
    return s


def alerts_since(since: datetime | None) -> dict:
    """For the admin pop-up: failed journeys not yet looked at, and IPs
    newly over a watch threshold."""
    failed = _q("SELECT journey_id, updated_at, route, airline, failed_step, customer_name, amount, currency "
                "FROM booking_journeys WHERE status='failed' AND failure_seen_at IS NULL "
                "ORDER BY updated_at DESC LIMIT 20")
    flagged = [r for r in ip_watch(days=1) if r["flags"] and not r["blocked"]]
    return {"failed_journeys": failed, "flagged_ips": [
        {"ip": r["ip"], "flags": r["flags"], "last_seen": r["last_seen"]} for r in flagged[:20]]}

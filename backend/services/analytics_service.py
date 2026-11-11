"""
Tick and Book business dashboard (/analytics): its own sign-in accounts and
the read queries behind each section. Everything here reads the analytics
tables that services/analytics_sync.py rebuilds — never the live booking
tables directly, and never writes to the main database.

Accounts: analytics_users (in the analytics database). A dashboard token
carries role "analytics", so it is refused by every /api/admin/* route
(auth.get_current_admin wants role "admin") and every customer route.
Only a super admin may create accounts, reset passwords or deactivate them
(Admin Console -> Customers & Insights -> Analytics Logins).

Personal data: names and emails are shown; phone numbers only as their last
3 digits; passport details are never copied into the analytics database.
"""
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import auth
import database
from services import analytics_sync as store
from services.analytics_sync import t

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# ── Sign-in throttle (per username, in memory) ────────────────────────────────
_FAIL_LIMIT = 8
_FAIL_WINDOW = 15 * 60
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _too_many(username: str) -> bool:
    now = time.time()
    with _fails_lock:
        recent = [x for x in _fails.get(username, []) if now - x < _FAIL_WINDOW]
        _fails[username] = recent
        return len(recent) >= _FAIL_LIMIT


def _note_fail(username: str) -> None:
    with _fails_lock:
        _fails.setdefault(username, []).append(time.time())


USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,50}$")
MIN_PASSWORD = 10


def _clean_username(u: str) -> str:
    return (u or "").strip().lower()


# ── Accounts ──────────────────────────────────────────────────────────────────

def login(username: str, password: str) -> dict:
    username = _clean_username(username)
    if _too_many(username):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Please wait 15 minutes and try again.")
    row = store.q(f"SELECT id, username, password_hash, full_name, active FROM {t('analytics_users')} "
                  "WHERE username=%s", (username,), one=True)
    if not row or not row["active"] or not auth.verify_password(password or "", row["password_hash"]):
        _note_fail(username)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    with _fails_lock:
        _fails.pop(username, None)
    store.write(f"UPDATE {t('analytics_users')} SET last_login_at=%s WHERE id=%s", (datetime.now(), row["id"]))
    token = auth.create_access_token({"sub": row["username"], "role": "analytics", "analytics_user_id": row["id"]})
    return {"access_token": token, "token_type": "bearer", "full_name": row.get("full_name"),
            "username": row["username"]}


def get_current_analytics_user(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    """FastAPI dependency: a valid token with role "analytics" whose account
    is still active (deactivating an account locks it out at once)."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Please sign in")
    payload = auth.decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session expired - please sign in again")
    if payload.get("role") != "analytics":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="An analytics dashboard account is required")
    row = store.q(f"SELECT active FROM {t('analytics_users')} WHERE id=%s", (payload.get("analytics_user_id"),), one=True)
    if not row or not row["active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This account is no longer active")
    return payload


def is_super_admin(admin_id) -> bool:
    """Super admins (services/go_live_switches.py, table super_admins). Falls
    back to reading super_admins directly where that module isn't present."""
    if not admin_id:
        return False
    try:
        from services import go_live_switches  # noqa: WPS433
    except ImportError:
        go_live_switches = None
    if go_live_switches is not None:
        try:
            return bool(go_live_switches.is_super_admin(admin_id))
        except Exception as e:
            logger.warning(f"Super admin check failed: {e}")
            return False
    try:
        conn = database.get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM super_admins WHERE admin_id=%s", (admin_id,))
            return cur.fetchone() is not None
        finally:
            cur.close()
            conn.close()
    except Exception:
        return False


def list_users() -> list[dict]:
    return store.q(f"SELECT id, username, full_name, active, created_at, created_by, updated_at, updated_by, "
                   f"last_login_at FROM {t('analytics_users')} ORDER BY active DESC, username")


def create_user(username: str, full_name: str, password: str, by: str) -> dict:
    username = _clean_username(username)
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=422, detail="Username: 3-50 characters, letters, numbers, dot, dash or underscore.")
    if len(password or "") < MIN_PASSWORD:
        raise HTTPException(status_code=422, detail=f"Password must be at least {MIN_PASSWORD} characters.")
    if store.q(f"SELECT id FROM {t('analytics_users')} WHERE username=%s", (username,), one=True):
        raise HTTPException(status_code=409, detail="That username is already taken.")
    new_id = store.write(f"INSERT INTO {t('analytics_users')} (username, password_hash, full_name, active, created_at, "
                         "created_by) VALUES (%s, %s, %s, 1, %s, %s)",
                         (username, auth.hash_password(password), (full_name or "").strip()[:150] or None,
                          datetime.now(), (by or "")[:100]))
    return {"id": new_id, "username": username}


def update_user(user_id: int, by: str, full_name: str | None = None, password: str | None = None,
                active: bool | None = None) -> dict:
    row = store.q(f"SELECT id FROM {t('analytics_users')} WHERE id=%s", (user_id,), one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    sets, params = [], []
    if full_name is not None:
        sets.append("full_name=%s")
        params.append(full_name.strip()[:150] or None)
    if password:
        if len(password) < MIN_PASSWORD:
            raise HTTPException(status_code=422, detail=f"Password must be at least {MIN_PASSWORD} characters.")
        sets.append("password_hash=%s")
        params.append(auth.hash_password(password))
    if active is not None:
        sets.append("active=%s")
        params.append(1 if active else 0)
    if not sets:
        raise HTTPException(status_code=422, detail="Nothing to change")
    sets += ["updated_at=%s", "updated_by=%s"]
    params += [datetime.now(), (by or "")[:100], user_id]
    store.write(f"UPDATE {t('analytics_users')} SET {', '.join(sets)} WHERE id=%s", tuple(params))
    return {"ok": True}


# ── Dashboard data ────────────────────────────────────────────────────────────

def _money_rows(rows: list[dict], key: str = "amount") -> list[dict]:
    return [{"currency": r["currency"] or "?", "amount": round(float(r[key] or 0), 2)} for r in rows if r[key]]


def _verticals_summary() -> dict:
    out = {}
    for v in ("hotel", "visa", "package"):
        rows = store.q(f"SELECT status, COUNT(*) n FROM {t('vertical_facts')} WHERE vertical=%s GROUP BY status", (v,))
        out[v] = {"total": sum(r["n"] for r in rows), "by_status": {r["status"] or "-": r["n"] for r in rows}}
    return out


def overview(days: int = 30) -> dict:
    bf, sf, ef = t("booking_facts"), t("search_facts"), t("extras_facts")
    since = datetime.now() - timedelta(days=days)
    totals = store.q(f"""SELECT COUNT(*) bookings, SUM(is_ticketed) ticketed, SUM(ever_ticketed) ever_ticketed,
                               SUM(status IN ('Cancelled','Voided')) cancelled, SUM(pax_count) passengers,
                               SUM(fare_source='NDC') ndc, SUM(fare_source<>'NDC' OR fare_source IS NULL) gds
                        FROM {bf}""", one=True) or {}
    revenue = _money_rows(store.q(f"SELECT currency, SUM(total_fare) amount FROM {bf} WHERE is_ticketed=1 "
                                  "GROUP BY currency ORDER BY amount DESC"))
    extras = _money_rows(store.q(f"SELECT currency, SUM(amount) amount FROM {ef} WHERE outcome='paid' "
                                 "GROUP BY currency ORDER BY amount DESC"))
    extras_items = store.q(f"SELECT COUNT(*) n FROM {ef} WHERE outcome='paid'", one=True)["n"]
    customers = store.q(f"SELECT COUNT(*) n, SUM(has_account) accounts, SUM(bookings>0) bookers, "
                        f"SUM(bookings>1) repeaters FROM {t('customers_360')}", one=True)
    searches = store.q(f"SELECT COUNT(*) n, SUM(result_count=0 OR result_count IS NULL) zero, SUM(has_error) errors "
                       f"FROM {sf}", one=True)
    period_s = store.q(f"SELECT COUNT(*) n FROM {sf} WHERE searched_at>=%s", (since,), one=True)["n"]
    period_b = store.q(f"SELECT COUNT(*) n, SUM(is_ticketed) tk FROM {bf} WHERE booking_date>=%s", (since,), one=True)
    daily = {}
    for r in store.q(f"SELECT DATE(searched_at) d, COUNT(*) n FROM {sf} WHERE searched_at>=%s GROUP BY d", (since,)):
        daily.setdefault(str(r["d"]), {})["searches"] = r["n"]
    for r in store.q(f"SELECT DATE(booking_date) d, COUNT(*) n, SUM(is_ticketed) tk, "
                     f"SUM(CASE WHEN is_ticketed=1 THEN total_fare ELSE 0 END) rev FROM {bf} "
                     f"WHERE booking_date>=%s GROUP BY d", (since,)):
        day = daily.setdefault(str(r["d"]), {})
        day.update(bookings=r["n"], ticketed=int(r["tk"] or 0), revenue=round(float(r["rev"] or 0), 2))
    series = []
    day, today = since.date() + timedelta(days=1), datetime.now().date()
    while day <= today:                     # every day in the window, quiet days as 0
        v = daily.get(day.isoformat(), {})
        series.append({"day": day.isoformat(), "searches": v.get("searches", 0), "bookings": v.get("bookings", 0),
                       "ticketed": v.get("ticketed", 0), "revenue": v.get("revenue", 0)})
        day += timedelta(days=1)
    by_status = store.q(f"SELECT status, COUNT(*) n FROM {bf} GROUP BY status ORDER BY n DESC")
    by_payment = store.q(f"SELECT payment_method, COUNT(*) n FROM {bf} GROUP BY payment_method ORDER BY n DESC")
    by_cabin = store.q(f"SELECT cabin_class, COUNT(*) n FROM {bf} GROUP BY cabin_class ORDER BY n DESC")
    by_trip = store.q(f"SELECT trip_type, COUNT(*) n FROM {bf} GROUP BY trip_type ORDER BY n DESC")
    n_s, n_b = int(searches["n"] or 0), int(totals.get("bookings") or 0)
    return {
        "bookings": n_b, "ticketed": int(totals.get("ticketed") or 0),
        "ever_ticketed": int(totals.get("ever_ticketed") or 0), "cancelled": int(totals.get("cancelled") or 0),
        "passengers": int(totals.get("passengers") or 0), "gds": int(totals.get("gds") or 0),
        "ndc": int(totals.get("ndc") or 0), "revenue": revenue, "extras_revenue": extras,
        "extras_items": int(extras_items or 0),
        "customers": int(customers["n"] or 0), "customer_accounts": int(customers["accounts"] or 0),
        "customers_booked": int(customers["bookers"] or 0), "repeat_customers": int(customers["repeaters"] or 0),
        "searches": n_s, "searches_no_results": int(searches["zero"] or 0),
        "searches_errors": int(searches["errors"] or 0),
        "conversion_pct": round(100 * n_b / n_s, 2) if n_s else None,
        "period_days": days, "period_searches": int(period_s or 0), "period_bookings": int(period_b["n"] or 0),
        "period_ticketed": int(period_b["tk"] or 0),
        "period_conversion_pct": round(100 * int(period_b["n"] or 0) / period_s, 2) if period_s else None,
        "daily": series,
        "by_status": [{"label": r["status"] or "-", "n": r["n"]} for r in by_status],
        "by_payment": [{"label": r["payment_method"] or "-", "n": r["n"]} for r in by_payment],
        "by_cabin": [{"label": r["cabin_class"] or "-", "n": r["n"]} for r in by_cabin],
        "by_trip": [{"label": r["trip_type"] or "-", "n": r["n"]} for r in by_trip],
        "verticals": _verticals_summary(),
        "last_sync": store.last_run(),
    }


def airlines() -> dict:
    bf, ef, sf = t("booking_facts"), t("extras_facts"), t("search_facts")
    rows = store.q(f"""SELECT airline_code, MAX(airline_name) airline_name, COUNT(*) bookings,
                              SUM(is_ticketed) ticketed, SUM(status IN ('Cancelled','Voided')) cancelled,
                              SUM(fare_source='NDC') ndc, SUM(fare_source<>'NDC' OR fare_source IS NULL) gds,
                              SUM(pax_count) passengers,
                              SUM(CASE WHEN is_ticketed=1 THEN total_fare ELSE 0 END) revenue,
                              AVG(CASE WHEN is_ticketed=1 THEN total_fare END) avg_fare,
                              MAX(currency) currency, COUNT(DISTINCT currency) currencies,
                              COUNT(DISTINCT destination) destinations, MAX(booking_date) last_booking
                       FROM {bf} GROUP BY airline_code ORDER BY ticketed DESC, bookings DESC""")
    extras = {r["airline_code"]: r for r in store.q(
        f"""SELECT airline_code, SUM(outcome='paid') paid, SUM(outcome='failed') failed,
                   SUM(CASE WHEN outcome='paid' THEN amount ELSE 0 END) paid_amount
            FROM {ef} GROUP BY airline_code""")}
    cheapest = {r["cheapest_airline"]: r["n"] for r in store.q(
        f"SELECT cheapest_airline, COUNT(*) n FROM {sf} WHERE cheapest_airline IS NOT NULL GROUP BY cheapest_airline")}
    out = []
    for r in rows:
        e = extras.get(r["airline_code"]) or {}
        out.append({
            "airline_code": r["airline_code"], "airline_name": r["airline_name"], "bookings": r["bookings"],
            "ticketed": int(r["ticketed"] or 0), "cancelled": int(r["cancelled"] or 0),
            "ndc": int(r["ndc"] or 0), "gds": int(r["gds"] or 0), "passengers": int(r["passengers"] or 0),
            "revenue": round(float(r["revenue"] or 0), 2), "avg_fare": round(float(r["avg_fare"] or 0), 2),
            "currency": r["currency"] if r["currencies"] == 1 else "mixed",
            "destinations": r["destinations"], "last_booking": r["last_booking"],
            "extras_paid": int(e.get("paid") or 0), "extras_failed": int(e.get("failed") or 0),
            "extras_amount": round(float(e.get("paid_amount") or 0), 2),
            "cheapest_in_searches": cheapest.get(r["airline_name"], 0),
        })
    return {"airlines": out}


def geography() -> dict:
    bf, sf = t("booking_facts"), t("search_facts")
    dest = store.q(f"""SELECT destination code, MAX(destination_city) city, MAX(destination_country) country,
                              COUNT(*) bookings, SUM(is_ticketed) ticketed,
                              SUM(CASE WHEN is_ticketed=1 THEN total_fare ELSE 0 END) revenue, MAX(currency) currency
                       FROM {bf} GROUP BY destination ORDER BY bookings DESC LIMIT 25""")
    orig = store.q(f"""SELECT origin code, MAX(origin_city) city, MAX(origin_country) country, COUNT(*) bookings,
                              SUM(is_ticketed) ticketed
                       FROM {bf} GROUP BY origin ORDER BY bookings DESC LIMIT 15""")
    countries = store.q(f"""SELECT destination_country country, COUNT(*) bookings, SUM(is_ticketed) ticketed,
                                   SUM(CASE WHEN is_ticketed=1 THEN total_fare ELSE 0 END) revenue,
                                   MAX(currency) currency, COUNT(DISTINCT destination) airports
                            FROM {bf} GROUP BY destination_country ORDER BY bookings DESC""")
    searched = store.q(f"""SELECT origin, destination, MAX(destination_city) city, COUNT(*) searches,
                                  SUM(result_count=0 OR result_count IS NULL) no_results,
                                  ROUND(AVG(result_count), 1) avg_results, MIN(cheapest_price) min_price,
                                  MAX(cheapest_currency) currency, MAX(searched_at) last_searched
                           FROM {sf} GROUP BY origin, destination ORDER BY searches DESC LIMIT 30""")
    booked_routes = {(r["origin"], r["destination"]): r["n"] for r in store.q(
        f"SELECT origin, destination, COUNT(*) n FROM {bf} GROUP BY origin, destination")}
    for r in searched:
        r["bookings"] = booked_routes.get((r["origin"], r["destination"]), 0)
        r["conversion_pct"] = round(100 * r["bookings"] / r["searches"], 1) if r["searches"] else None
    search_countries = store.q(f"""SELECT destination_country country, COUNT(*) searches
                                   FROM {sf} GROUP BY destination_country ORDER BY searches DESC LIMIT 20""")
    for rows in (dest, countries):
        for r in rows:
            r["ticketed"] = int(r["ticketed"] or 0)
            r["revenue"] = round(float(r["revenue"] or 0), 2)
    for r in orig:
        r["ticketed"] = int(r["ticketed"] or 0)
    for r in searched:
        r["no_results"] = int(r["no_results"] or 0)
        r["avg_results"] = float(r["avg_results"]) if r["avg_results"] is not None else None
    return {"destinations": dest, "origins": orig, "countries": countries, "searched_routes": searched,
            "search_countries": search_countries}


def extras() -> dict:
    ef = t("extras_facts")
    by_kind = store.q(f"""SELECT kind, outcome, COUNT(*) n, SUM(amount) amount, MAX(currency) currency
                          FROM {ef} GROUP BY kind, outcome ORDER BY kind, outcome""")
    by_airline = store.q(f"""SELECT airline_code, MAX(airline_name) airline_name,
                                    SUM(outcome='paid') paid, SUM(outcome='failed') failed,
                                    SUM(outcome='open') open_items, SUM(outcome='not_sold') not_sold,
                                    SUM(CASE WHEN outcome='paid' THEN amount ELSE 0 END) paid_amount,
                                    SUM(CASE WHEN outcome='paid' AND kind='seat' THEN 1 ELSE 0 END) seats,
                                    SUM(CASE WHEN outcome='paid' AND kind='extra' THEN 1 ELSE 0 END) extras,
                                    MAX(currency) currency
                             FROM {ef} GROUP BY airline_code ORDER BY paid DESC, failed DESC""")
    top_items = store.q(f"""SELECT kind, item_name, COUNT(*) n, SUM(amount) amount, MAX(currency) currency,
                                   ROUND(AVG(amount), 2) avg_price
                            FROM {ef} WHERE outcome='paid' AND kind='extra'
                            GROUP BY kind, item_name ORDER BY n DESC LIMIT 20""")
    by_source = store.q(f"""SELECT source, outcome, COUNT(*) n, SUM(amount) amount, MAX(currency) currency
                            FROM {ef} GROUP BY source, outcome""")
    for rows in (by_kind, by_airline, top_items, by_source):
        for r in rows:
            for k in ("amount", "paid_amount", "avg_price"):
                if k in r and r[k] is not None:
                    r[k] = round(float(r[k]), 2)
            for k in ("paid", "failed", "open_items", "not_sold", "seats", "extras"):
                if k in r:
                    r[k] = int(r[k] or 0)
    return {"by_kind": by_kind, "by_airline": by_airline, "top_items": top_items, "by_source": by_source}


def verticals() -> dict:
    out = {}
    for v in ("hotel", "visa", "package"):
        rows = store.q(f"""SELECT source_id, status, customer_name, customer_email, title, place, travellers, amount,
                                  currency, event_date, requested_at
                           FROM {t('vertical_facts')} WHERE vertical=%s ORDER BY requested_at DESC LIMIT 200""", (v,))
        by_status, places, amount = {}, {}, {}
        for r in rows:
            by_status[r["status"] or "-"] = by_status.get(r["status"] or "-", 0) + 1
            if r["place"]:
                places[r["place"]] = places.get(r["place"], 0) + 1
            if r["amount"] and r["currency"]:
                amount[r["currency"]] = amount.get(r["currency"], 0) + float(r["amount"])
        out[v] = {"total": len(rows), "by_status": by_status,
                  "places": sorted(({"label": k, "n": n} for k, n in places.items()), key=lambda x: -x["n"]),
                  "amount": [{"currency": k, "amount": round(a, 2)} for k, a in amount.items()],
                  "rows": rows}
    return out


def _cust_row(r: dict) -> dict:
    r = dict(r)
    r["spend"] = json.loads(r.pop("spend_json") or "{}")
    r["extras_spend"] = json.loads(r.pop("extras_spend_json") or "{}")
    r["airlines"] = json.loads(r.pop("airlines_json") or "{}")
    return r


def customers(search: str | None = None, sort: str = "spend", limit: int = 200) -> dict:
    order = {"spend": "spend_lkr DESC, bookings DESC", "bookings": "bookings DESC, spend_lkr DESC",
             "recent": "last_booking IS NULL, last_booking DESC", "name": "full_name"}.get(sort, "spend_lkr DESC")
    where, params = "", ()
    s = (search or "").strip().lower()
    if s:
        where = "WHERE LOWER(email) LIKE %s OR LOWER(full_name) LIKE %s"
        params = (f"%{s}%", f"%{s}%")
    rows = store.q(f"""SELECT email, customer_id, full_name, phone_masked, has_account, account_since, bookings,
                              ticketed, cancelled, spend_json, spend_lkr, first_booking, last_booking,
                              last_trip_route, last_trip_date, next_trip_date, airlines_json, extras_items,
                              extras_spend_json, searches, last_search, hotel_requests, visa_requests,
                              package_requests, loyalty_points
                       FROM {t('customers_360')} {where} ORDER BY {order} LIMIT %s""", params + (int(limit),))
    total = store.q(f"SELECT COUNT(*) n FROM {t('customers_360')}", one=True)["n"]
    return {"customers": [_cust_row(r) for r in rows], "total": total}


def customer_detail(email: str) -> dict:
    email = (email or "").strip().lower()
    row = store.q(f"SELECT * FROM {t('customers_360')} WHERE email=%s", (email,), one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Customer not found")
    bookings = store.q(f"""SELECT locator_code, passenger_name, pax_count, airline_code, airline_name, flight_number,
                                  origin, destination, destination_city, trip_type, cabin_class, fare_source,
                                  total_fare, currency, status, booking_date, departure_at, payment_method
                           FROM {t('booking_facts')} WHERE customer_email=%s ORDER BY booking_date DESC LIMIT 500""",
                       (email,))
    extras_rows = store.q(f"""SELECT source, locator_code, airline_code, kind, item_name, amount, currency, outcome,
                                     created_at
                              FROM {t('extras_facts')} WHERE customer_email=%s AND outcome IN ('paid','failed','open')
                              ORDER BY created_at DESC LIMIT 500""", (email,))
    other = store.q(f"""SELECT vertical, status, title, place, travellers, amount, currency, event_date, requested_at
                        FROM {t('vertical_facts')} WHERE customer_email=%s ORDER BY requested_at DESC""", (email,))
    routes, months = {}, {}
    for b in bookings:
        key = f"{b['origin']}-{b['destination']}"
        routes[key] = routes.get(key, 0) + 1
        if b["departure_at"]:
            m = b["departure_at"].strftime("%b")
            months[m] = months.get(m, 0) + 1
    cust = _cust_row(row)
    cust.pop("synced_at", None)
    return {"customer": cust, "bookings": bookings, "extras": extras_rows, "other": other,
            "top_routes": sorted(({"label": k, "n": n} for k, n in routes.items()), key=lambda x: -x["n"])[:8],
            "travel_months": months}

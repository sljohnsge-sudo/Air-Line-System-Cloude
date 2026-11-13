"""
services/eticket_email.py
=========================
The e-ticket email a customer gets automatically right after the ticket is
issued (approved 13/11/2026), for both systems:
  * Travelport - main._issue_and_save_ticket (card return, bank / cash staff
    confirm, late-ticket recovery) and the Emirates NDC instant pay;
  * Amadeus - main._amadeus_issue_after_payment.
Never on a failure: a paid-but-not-ticketed booking keeps its own refund
message.

Once per ticket number: each number is claimed in customer_notifications
(event "eticket", channel "email") before anything is sent, so a reloaded
return page, a retry or a staff confirm never emails twice. The outcome is
recorded on the same row: sent / not_configured (no email login yet) /
no_address / failed, with the reason. A ticket whose email couldn't go out is
not re-sent later on its own (staff can still share the ticket).

Recipient: the contact email given at booking, else the signed-in account's
email. Nobody else.

Content: Tick and Book only. Real data from the issued booking: PNR, airline
reference, each passenger's name and ticket number (no passport numbers),
the flights with dates, times and terminals, cabin, brand and baggage when
the booking has them (as the airline gives them), the amount paid and how.
Nothing of our own invented: a missing value shows as "-". Links to My Bookings and
Check My Ticket Status only when PUBLIC_SITE_URL is set - never a localhost
link.

Sent on a background thread and never raises: an email problem can't hold
up or undo a ticket.
"""
import html
import logging
import os
import re
import smtplib
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import database
from config.email_config import EmailConfig

logger = logging.getLogger(__name__)

BRAND = os.getenv("BRAND_NAME", "Tick and Book")
EVENT = "eticket"
AMADEUS_PREFIX = "AD-"  # notification key for Amadeus PNRs, never confused with a Travelport PNR

_PAX_TYPES = {"ADT": "Adult", "CHD": "Child", "CNN": "Child", "C07": "Child", "INF": "Infant", "INS": "Infant",
              "YTH": "Adult", "STU": "Adult", "SRC": "Adult"}
_PAYMENT = {"card": "Card", "credit card": "Card", "bank": "Bank transfer", "cash": "Cash"}


def site_url() -> str | None:
    """Our public site address, or None while PUBLIC_SITE_URL isn't set (a
    customer email must never link to localhost)."""
    url = (os.getenv("PUBLIC_SITE_URL") or "").strip().rstrip("/")
    return url if url.startswith("http") and "localhost" not in url and "127.0.0.1" not in url else None


def email_login_set() -> bool:
    return EmailConfig.smtp_configured()


# ── Formatting ───────────────────────────────────────────────────────────────

def _when(value) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s[:16]).strftime("%a %d %b %Y, %H:%M")
    except ValueError:
        return s


def _bag(text) -> str:
    """Baggage as the booking holds it ("30K", "2PC", "30 kg") -> readable."""
    t = str(text or "").strip()
    if not t:
        return ""
    m = re.fullmatch(r"(\d+)\s*(K|KG|kg)", t)
    if m:
        return f"{m.group(1)} kg"
    m = re.fullmatch(r"(\d+)\s*(P|PC|PCS|pieces?)", t)
    if m:
        return f"{m.group(1)} piece{'s' if m.group(1) != '1' else ''}"
    return t


def _money(currency, amount) -> str:
    try:
        return f"{currency or ''} {float(amount):,.2f}".strip()
    except (TypeError, ValueError):
        return ""


def _terminal(t) -> str:
    return f" (Terminal {t})" if t else ""


def compose(d: dict) -> tuple[str, str, str]:
    """(subject, plain text, html) from the booking facts in d:
    locator, airline_pnr, airline, passengers [{name, type, ticket}],
    flights [{flight, from, to, dep, arr, dep_terminal, arr_terminal, label?}],
    cabin, brand, baggage, amount, currency, payment."""
    loc = d["locator"]
    subject = f"Your e-ticket - booking {loc}"
    site = site_url()
    lines = [f"Thank you for booking with {BRAND}. Your ticket has been issued.", ""]
    lines.append(f"Booking reference (PNR): {loc}")
    if d.get("airline_pnr") and d["airline_pnr"] != loc:
        lines.append(f"Airline reference: {d['airline_pnr']}")
    lines += ["", "Passengers and ticket numbers:"]
    for p in d["passengers"]:
        lines.append(f"  - {p['name'] or '-'}{' (' + p['type'] + ')' if p.get('type') else ''}: {p.get('ticket') or '-'}")
    lines += ["", "Flights:"]
    for f in d["flights"]:
        if f.get("label"):
            lines.append(f"  {f['label']}")
        lines.append(f"  {f['flight']}  {f['from']}{_terminal(f.get('dep_terminal'))} -> {f['to']}{_terminal(f.get('arr_terminal'))}")
        lines.append(f"      Departs {_when(f.get('dep'))}" + (f" - arrives {_when(f.get('arr'))}" if f.get("arr") else ""))
    facts = [("Cabin", d.get("cabin")), ("Fare", d.get("brand")), ("Baggage", _bag(d.get("baggage")))]
    lines.append("")
    lines += [f"{k}: {v}" for k, v in facts if v]
    paid = _money(d.get("currency"), d.get("amount"))
    if paid:
        lines.append(f"Amount paid: {paid}" + (f" ({d['payment']})" if d.get("payment") else ""))
    lines.append("")
    if site:
        lines.append(f"My Bookings: {site}/account")
        lines.append(f"Check My Ticket Status: {site}/check-ticket")
    else:
        lines.append(f"You can see this booking under My Bookings or Check My Ticket Status on the {BRAND} website.")
    contacts = [c for c in (EmailConfig.SUPPORT_PHONE, EmailConfig.SUPPORT_EMAIL) if c]
    lines += ["", BRAND] + contacts
    text = "\n".join(lines) + "\n"

    e = html.escape
    pax_rows = "".join(
        f'<tr><td style="padding:6px 8px;border-bottom:1px solid #D6E3EF">{e(p["name"] or "-")}'
        f'<span style="color:#52657C;font-size:12px">{" " + e(p["type"]) if p.get("type") else ""}</span></td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #D6E3EF;font-weight:bold">{e(p.get("ticket") or "-")}</td></tr>'
        for p in d["passengers"])
    flight_rows = "".join(
        (f'<tr><td colspan="2" style="padding:8px 8px 2px;color:#52657C;font-size:12px;font-weight:bold">{e(f["label"])}</td></tr>' if f.get("label") else "")
        + f'<tr><td style="padding:6px 8px;border-bottom:1px solid #D6E3EF;font-weight:bold;white-space:nowrap">{e(f["flight"])}</td>'
          f'<td style="padding:6px 8px;border-bottom:1px solid #D6E3EF">{e(f["from"])}{e(_terminal(f.get("dep_terminal")))} &rarr; '
          f'{e(f["to"])}{e(_terminal(f.get("arr_terminal")))}<br><span style="color:#52657C;font-size:12px">'
          f'Departs {e(_when(f.get("dep")))}{(" &middot; arrives " + e(_when(f.get("arr")))) if f.get("arr") else ""}</span></td></tr>'
        for f in d["flights"])
    fact_rows = "".join(f'<tr><td style="padding:4px 8px;color:#52657C">{e(k)}</td><td style="padding:4px 8px">{e(v)}</td></tr>'
                        for k, v in facts if v)
    if paid:
        fact_rows += (f'<tr><td style="padding:4px 8px;color:#52657C">Amount paid</td><td style="padding:4px 8px;font-weight:bold">'
                      f'{e(paid)}{(" &middot; " + e(d["payment"])) if d.get("payment") else ""}</td></tr>')
    links = (f'<p style="margin:16px 0"><a href="{e(site)}/account" style="background:#123B70;color:#ffffff;text-decoration:none;'
             f'padding:10px 16px;border-radius:8px;font-weight:bold;display:inline-block;margin:0 8px 8px 0">My Bookings</a>'
             f'<a href="{e(site)}/check-ticket" style="background:#ffffff;color:#123B70;text-decoration:none;border:1px solid #123B70;'
             f'padding:9px 15px;border-radius:8px;font-weight:bold;display:inline-block">Check My Ticket Status</a></p>') if site else (
        f'<p style="margin:16px 0">You can see this booking under My Bookings or Check My Ticket Status on the {e(BRAND)} website.</p>')
    airline_ref = (f'<td style="padding:0 16px 0 0"><div style="font-size:11px;color:#52657C;text-transform:uppercase">Airline reference</div>'
                   f'<div style="font-size:18px;font-weight:bold">{e(d["airline_pnr"])}</div></td>') if d.get("airline_pnr") and d["airline_pnr"] != loc else ""
    body_html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#24364B;max-width:640px">'
        f'<div style="background:#123B70;color:#ffffff;padding:14px 18px;border-radius:12px 12px 0 0;font-size:20px;font-weight:bold">{e(BRAND)}</div>'
        '<div style="border:1px solid #D6E3EF;border-top:none;border-radius:0 0 12px 12px;padding:18px;background:#ffffff">'
        f'<p style="margin:0 0 14px;font-size:15px">Thank you for booking with {e(BRAND)}. Your ticket has been issued.</p>'
        '<table role="presentation" style="border-collapse:collapse;margin:0 0 14px"><tr>'
        f'<td style="padding:0 16px 0 0"><div style="font-size:11px;color:#52657C;text-transform:uppercase">Booking reference (PNR)</div>'
        f'<div style="font-size:18px;font-weight:bold">{e(loc)}</div></td>{airline_ref}</tr></table>'
        '<h3 style="font-size:14px;margin:14px 0 4px">Passengers and ticket numbers</h3>'
        f'<table role="presentation" style="border-collapse:collapse;width:100%;font-size:14px">{pax_rows}</table>'
        '<h3 style="font-size:14px;margin:16px 0 4px">Flights</h3>'
        f'<table role="presentation" style="border-collapse:collapse;width:100%;font-size:14px">{flight_rows}</table>'
        f'<table role="presentation" style="border-collapse:collapse;margin-top:12px;font-size:14px">{fact_rows}</table>'
        f'{links}'
        + (f'<p style="font-size:12px;color:#52657C;margin:8px 0 0">{"<br>".join(e(c) for c in contacts)}</p>' if contacts else "")
        + '</div></div>'
    )
    return subject, text, body_html


# ── Sending, once per ticket number ─────────────────────────────────────────

def _smtp_send(to_addr: str, subject: str, text: str, body_html: str) -> tuple[bool, str | None]:
    msg = MIMEMultipart("alternative")
    # Always Tick and Book as the sender name - SMTP_FROM_NAME in .env is the
    # visa desk's name ("Final Travels Visa Desk" on 13/11/2026).
    msg["From"] = formataddr((BRAND, EmailConfig.SMTP_USER))
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    try:
        with smtplib.SMTP(EmailConfig.SMTP_HOST, EmailConfig.SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(EmailConfig.SMTP_USER, EmailConfig.SMTP_PASSWORD)
            server.sendmail(EmailConfig.SMTP_USER, [to_addr], msg.as_string())
        return True, None
    except Exception as ex:
        return False, str(ex)


def deliver(key: str, ticket_numbers: list, to_addr: str | None, facts: dict) -> str:
    """Claims each ticket number, then sends one email for the ones not
    emailed before. Returns the outcome (sent / already_sent / disabled /
    not_configured / no_address / failed)."""
    numbers = [str(n) for n in ticket_numbers if n]
    if not numbers:
        return "no_ticket"
    key = key[:10]  # customer_notifications.locator_code is VARCHAR(10) (PNR 6, "AD-" + PNR 9)
    if not database.get_provider_settings().get("notify_email_enabled", True):
        logger.info(f"{key}: e-ticket email not sent - customer emails are switched off in Admin")
        return "disabled"
    fresh = [n for n in numbers if database.claim_notification(key, EVENT, n, "email")]
    if not fresh:
        return "already_sent"
    subject, text, body_html = compose(facts)
    if not to_addr:
        status, error = "no_address", "no email address on the booking"
    elif not EmailConfig.smtp_configured():
        status, error = "not_configured", "Email login not set (SMTP_USER / SMTP_PASSWORD in the backend .env)"
        logger.warning(f"{key}: e-ticket email NOT sent - email login not set (SMTP_USER / SMTP_PASSWORD in the backend .env)")
    else:
        sent, error = _smtp_send(to_addr, subject, text, body_html)
        status = "sent" if sent else "failed"
    database.finish_notifications(key, EVENT, fresh, "email", status, to_addr, subject, text, error)
    logger.info(f"{key}: e-ticket email for ticket(s) {', '.join(fresh)}: {status}" + (f" ({error})" if error and status == "failed" else ""))
    return status


def _in_background(name: str, fn, *args) -> None:
    def run():
        try:
            fn(*args)
        except Exception as ex:
            logger.error(f"{name}: e-ticket email failed (the ticket is not affected): {ex}")
    threading.Thread(target=run, name=f"eticket-{name}", daemon=True).start()


# ── Travelport ──────────────────────────────────────────────────────────────

def travelport_facts(b: dict) -> tuple[list, str | None, dict]:
    """(ticket numbers, recipient, facts) from a saved Travelport booking."""
    travelers = [t for t in (b.get("travelers") or []) if isinstance(t, dict)]
    entries = [t if isinstance(t, dict) else {"number": t} for t in (b.get("ticket_numbers") or [])]
    # Each ticket goes to its own passenger when the booking read says which
    # (traveler_index, from the ticket-number fix f6bbfe0a); else in order.
    if entries and all(isinstance(e.get("traveler_index"), int) for e in entries):
        by_index = {e["traveler_index"]: e for e in entries}
        entries = [by_index.get(i, {}) for i in range(max(len(travelers), max(by_index) + 1))]
    tickets = [e.get("number") for e in entries] or ([b["ticket_number"]] if b.get("ticket_number") else [])
    ptypes = [e.get("passenger_type") for e in entries]
    passengers = []
    for i in range(max(len(travelers), len(tickets))):
        t = travelers[i] if i < len(travelers) else {}
        name = (t.get("confirmed_name") or f"{t.get('first_name') or ''} {t.get('last_name') or ''}".strip()
                or (b.get("passenger_name") if i == 0 else "") or "")
        code = (ptypes[i] if i < len(ptypes) else None) or t.get("passenger_type")
        passengers.append({"name": name.upper(), "type": _PAX_TYPES.get(str(code or "").upper(), ""),
                           "ticket": tickets[i] if i < len(tickets) else None})
    legs = b.get("legs") if isinstance(b.get("legs"), list) and b.get("legs") else [b]
    flights = []
    for li, leg in enumerate(legs):
        segs = leg.get("segments") if isinstance(leg.get("segments"), list) and leg.get("segments") else [leg]
        label = ("Departure" if li == 0 else "Return" if li == len(legs) - 1 and leg.get("arrival_airport") == legs[0].get("departure_airport")
                 else f"Flight {li + 1}") if len(legs) > 1 else None
        for si, s in enumerate(segs):
            flights.append({"label": label if si == 0 else None,
                            "flight": f"{s.get('flight_number') or ''}".strip(),
                            "from": s.get("departure_airport") or "", "to": s.get("arrival_airport") or "",
                            "dep": s.get("departure_time"), "arr": s.get("arrival_time"),
                            "dep_terminal": s.get("departure_terminal"), "arr_terminal": s.get("arrival_terminal")})
    to_addr = b.get("email") or b.get("passenger_email")
    if not to_addr and b.get("customer_id"):
        to_addr = (database.get_customer_by_id(b["customer_id"]) or {}).get("email")
    facts = {"locator": b.get("locator_code"), "airline_pnr": re.sub(r"^[A-Z0-9]{2}_", "", str(b.get("airline_pnr") or "")) or None,
             "airline": b.get("airline"), "passengers": passengers, "flights": flights,
             "cabin": b.get("cabin_class"), "brand": b.get("fare_family"), "baggage": b.get("baggage_allowance"),
             "amount": b.get("total_fare"), "currency": b.get("currency"),
             "payment": _PAYMENT.get(str(b.get("payment_method") or "").lower(), b.get("payment_method"))}
    return [t for t in tickets if t], to_addr, facts


def _send_travelport(locator: str) -> None:
    b = database.get_booking_by_locator(locator)
    if not b or not (b.get("ticket_number") or b.get("ticket_numbers")):
        logger.warning(f"{locator}: e-ticket email skipped - no saved ticket")
        return
    tickets, to_addr, facts = travelport_facts(b)
    deliver(locator, tickets, to_addr, facts)


def send_travelport_eticket(locator: str) -> None:
    """Right after a Travelport ticket is saved. Background, never raises."""
    if locator:
        _in_background(locator, _send_travelport, locator)


# ── Amadeus ─────────────────────────────────────────────────────────────────

def amadeus_facts(row: dict, view: dict | None) -> tuple[list, str | None, dict]:
    """(ticket numbers, recipient, facts) from our Amadeus booking record and
    the live Amadeus PNR (its ticket lines give each passenger's number)."""
    it = row.get("itinerary") or {}
    docs = [d for d in ((view or {}).get("documents") or []) if d.get("kind") == "ticket" and d.get("status") == "issued"]
    travelers = row.get("travelers") or []
    if docs:
        # A lap infant's ticket line is filed under its adult's name; the
        # infant's own name is its INF passenger on the booking (in order).
        infants = [f"{t.get('firstName') or ''} {t.get('lastName') or ''}".strip().upper()
                   for t in ((view or {}).get("travelers") or []) if (t.get("type") or "").upper() == "INF"]
        passengers = []
        for d in docs:
            name = (d.get("passenger") or "").upper()
            if d.get("infant"):
                name = infants.pop(0) if infants else ""
            passengers.append({"name": name, "type": "Infant" if d.get("infant") else "", "ticket": d.get("number")})
        tickets = [d.get("number") for d in docs]
    else:
        tickets = row.get("ticket_list") or []
        passengers = [{"name": f"{t.get('first_name') or ''} {t.get('last_name') or ''}".strip().upper(),
                       "type": _PAX_TYPES.get(str(t.get("passenger_type") or "").upper(), ""),
                       "ticket": tickets[i] if i < len(tickets) else None} for i, t in enumerate(travelers)] \
            or [{"name": (row.get("lead_name") or "").upper(), "type": "", "ticket": n} for n in tickets]
    legs = it.get("legs") if isinstance(it.get("legs"), list) and it.get("legs") else [it]
    flights = []
    for li, leg in enumerate(legs):
        segs = leg.get("segments") if isinstance(leg.get("segments"), list) and leg.get("segments") else [leg]
        label = ("Departure" if li == 0 else "Return") if len(legs) > 1 else None
        for si, s in enumerate(segs):
            if not s.get("flight_number"):
                continue
            flights.append({"label": label if si == 0 else None, "flight": s.get("flight_number"),
                            "from": s.get("departure_airport") or "", "to": s.get("arrival_airport") or "",
                            "dep": s.get("departure_time"), "arr": s.get("arrival_time"),
                            "dep_terminal": s.get("departure_terminal"), "arr_terminal": s.get("arrival_terminal")})
    if not flights:
        route = (row.get("route") or "").split("-")
        flights = [{"flight": row.get("flights") or "", "from": route[0] if route else "", "to": route[-1] if route else "",
                    "dep": row.get("travel_date")}]
    to_addr = row.get("email")
    if not to_addr and row.get("customer_id"):
        to_addr = (database.get_customer_by_id(row["customer_id"]) or {}).get("email")
    facts = {"locator": row["locator"], "airline_pnr": None, "airline": it.get("airline"),
             "passengers": passengers, "flights": flights, "cabin": it.get("cabin_class"),
             "brand": it.get("fare_family"), "baggage": it.get("baggage_allowance"),
             "amount": row.get("amount"), "currency": row.get("currency"),
             "payment": _PAYMENT.get(str(row.get("payment_method") or "").lower(), row.get("payment_method"))}
    return [t for t in tickets if t], to_addr, facts


def _send_amadeus(locator: str) -> None:
    from services import amadeus_booking_proxy, amadeus_customer_bookings
    row = amadeus_customer_bookings.get(locator)
    if not row or row.get("status") != "ticketed":
        logger.warning(f"Amadeus {locator}: e-ticket email skipped - not ticketed on our record")
        return
    try:
        view = amadeus_booking_proxy.servicing_view_sync(locator)
    except Exception as ex:
        logger.warning(f"Amadeus {locator}: live PNR not read for the e-ticket email ({ex}) - using our record")
        view = None
    tickets, to_addr, facts = amadeus_facts(row, view)
    deliver(AMADEUS_PREFIX + locator, tickets, to_addr, facts)


def send_amadeus_eticket(locator: str) -> None:
    """Right after an Amadeus ticket is issued. Background, never raises."""
    if locator:
        _in_background(AMADEUS_PREFIX + locator, _send_amadeus, locator)

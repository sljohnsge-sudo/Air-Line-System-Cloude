"""
services/share_fare_service.py
================================
"Share this fare" by email: a signed-in customer sends the fares they ticked
on the search results to a friend's email address, from Tick and Book's own
sender (the SMTP account in config/email_config.py — no new mail settings).

The email carries:
  * a picture of each fare card (PNG drawn in the browser, see
    frontend/src/ShareFares.jsx drawFareCard) shown inline,
  * the same text summary the WhatsApp message has,
  * the link that re-runs the search live. The link, never the picture or
    the text, is what gets booked — the price in them is only "at the time
    of sharing".

The link is rebuilt here from the /results/<token> path alone, on our own
site address (PUBLIC_SITE_URL), and any other web address in the text is
replaced by it, so this can't be used to mail other links from our sender.
One recipient per email, at most 5 pictures of at most 1.5 MB each, and at
most 10 emails per customer per hour (kept in memory — resets on restart).
"""

import base64
import html
import logging
import os
import re
import smtplib
import threading
import time
from collections import defaultdict, deque
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from config.email_config import EmailConfig

logger = logging.getLogger(__name__)

BRAND = os.getenv("BRAND_NAME", "Tick and Book")
SITE_URL = os.getenv("PUBLIC_SITE_URL", "http://localhost:5176").rstrip("/")

MAX_FARES = 5
MAX_PNG_BYTES = 1_500_000
MAX_TEXT_CHARS = 8000
RATE_LIMIT_COUNT = 10
RATE_LIMIT_WINDOW_SECONDS = 3600

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_RESULTS_PATH_RE = re.compile(r"^/results/[A-Za-z0-9_-]{16,4000}$")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_DATA_URL_PREFIX = "data:image/png;base64,"


class ShareFareError(Exception):
    """A request we refuse; status_code is the HTTP answer for the route."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ── Checks ───────────────────────────────────────────────────────────────────

def validate_email(address: str) -> str:
    """One plain address (no lists, no display names). Returns it trimmed."""
    addr = (address or "").strip()
    if not addr or len(addr) > 254 or "," in addr or ";" in addr or not _EMAIL_RE.match(addr):
        raise ShareFareError(422, "Please enter one valid email address.")
    local = addr.split("@", 1)[0]
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
        raise ShareFareError(422, "Please enter one valid email address.")
    return addr


def build_link(results_path: str) -> str:
    """The live-search link on our own site, from the /results/<token> path."""
    path = (results_path or "").strip()
    if not _RESULTS_PATH_RE.match(path):
        raise ShareFareError(422, "The link to these fares is missing. Please search again and share from the results.")
    return f"{SITE_URL}{path}"


def decode_images(images: list) -> list[bytes]:
    """PNG pictures sent as data URLs (or bare base64). 1..MAX_FARES of them,
    each a real PNG of at most MAX_PNG_BYTES."""
    images = images or []
    if not images:
        raise ShareFareError(422, "The fare picture is missing. Please try again.")
    if len(images) > MAX_FARES:
        raise ShareFareError(422, f"You can share up to {MAX_FARES} fares at a time.")
    out = []
    for raw in images:
        if not isinstance(raw, str):
            raise ShareFareError(422, "The fare picture could not be read.")
        b64 = raw[len(_DATA_URL_PREFIX):] if raw.startswith(_DATA_URL_PREFIX) else raw
        # base64 is 4/3 the size: refuse oversize pictures before decoding them.
        if len(b64) > (MAX_PNG_BYTES * 4) // 3 + 8:
            raise ShareFareError(413, "A fare picture is too large (1.5 MB at most).")
        try:
            data = base64.b64decode(b64, validate=True)
        except Exception:
            raise ShareFareError(422, "The fare picture could not be read.")
        if len(data) > MAX_PNG_BYTES:
            raise ShareFareError(413, "A fare picture is too large (1.5 MB at most).")
        if not data.startswith(PNG_SIGNATURE):
            raise ShareFareError(422, "The fare picture must be a PNG image.")
        out.append(data)
    return out


def clean_text(text: str, link: str) -> str:
    """The summary text, trimmed, with every web address replaced by our link."""
    body = (text or "").replace("\r\n", "\n").strip()
    if not body:
        raise ShareFareError(422, "The fare details are missing. Please try again.")
    if len(body) > MAX_TEXT_CHARS:
        raise ShareFareError(422, "The fare details are too long to send.")
    body = _URL_RE.sub(link, body)
    if link not in body:
        body += f"\n\nSee the live price & book: {link}"
    return body


# ── Rate limit (per customer, in memory) ─────────────────────────────────────

_sent_times: dict = defaultdict(deque)
_lock = threading.Lock()


def check_rate_limit(customer_id, now: float | None = None) -> None:
    """Raises 429 when this customer already sent RATE_LIMIT_COUNT emails in
    the last hour. Does not count this one — see record_send."""
    now = time.time() if now is None else now
    with _lock:
        q = _sent_times[customer_id]
        while q and now - q[0] >= RATE_LIMIT_WINDOW_SECONDS:
            q.popleft()
        if len(q) >= RATE_LIMIT_COUNT:
            wait_min = max(1, int((RATE_LIMIT_WINDOW_SECONDS - (now - q[0])) // 60) + 1)
            raise ShareFareError(429, f"You've sent {RATE_LIMIT_COUNT} share emails in the last hour. Please try again in about {wait_min} minutes.")


def record_send(customer_id, now: float | None = None) -> None:
    with _lock:
        _sent_times[customer_id].append(time.time() if now is None else now)


# ── The email ────────────────────────────────────────────────────────────────

def _sender_name(customer: dict) -> str:
    """The customer's name from their account, else their email."""
    name = ""
    try:
        import database
        row = database.get_customer_by_id(customer.get("customer_id"))
        name = ((row or {}).get("full_name") or "").strip()
    except Exception as e:  # the email still goes without the name
        logger.warning(f"Share fare: customer name lookup failed: {e}")
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", name or (customer.get("sub") or "")).strip()[:80]
    return name or "A Tick and Book customer"


def compose(sender_name: str, reply_to: str | None, to_addr: str, text: str, link: str, pngs: list[bytes]) -> MIMEMultipart:
    n = len(pngs)
    subject = f"{sender_name} shared {'a flight option' if n == 1 else f'{n} flight options'} with you - {BRAND}"
    intro = f"{sender_name} shared {'this flight option' if n == 1 else 'these flight options'} with you on {BRAND}."
    note = ("Prices shown are from when this was shared and change with seat availability. "
            "The live price on the link is the price you pay.")
    plain = f"{intro}\n\n{text}\n\n{note}\n\n{BRAND}\n"

    cids = [make_msgid(domain="tickandbook.share")[1:-1] for _ in pngs]
    pictures = "".join(
        f'<p style="margin:0 0 16px"><img src="cid:{cid}" alt="Flight option {i + 1}" '
        f'style="max-width:100%;width:600px;border:1px solid #D6E3EF;border-radius:12px;display:block"></p>'
        for i, cid in enumerate(cids)
    )
    safe_link = html.escape(link, quote=True)
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#24364B;max-width:640px">'
        f'<div style="background:#123B70;color:#ffffff;padding:14px 18px;border-radius:12px 12px 0 0;font-size:20px;font-weight:bold">{html.escape(BRAND)}</div>'
        '<div style="border:1px solid #D6E3EF;border-top:none;border-radius:0 0 12px 12px;padding:18px">'
        f'<p style="margin:0 0 16px;font-size:15px">{html.escape(intro)}</p>'
        f'{pictures}'
        f'<p style="margin:0 0 20px"><a href="{safe_link}" style="background:#123B70;color:#ffffff;text-decoration:none;'
        'padding:12px 20px;border-radius:8px;font-weight:bold;display:inline-block">See the live price &amp; book</a></p>'
        f'<pre style="white-space:pre-wrap;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.5;margin:0 0 16px;color:#24364B">{html.escape(text)}</pre>'
        f'<p style="font-size:12px;color:#52657C;margin:0">{html.escape(note)}</p>'
        '</div></div>'
    )

    msg = MIMEMultipart("related")
    msg["From"] = formataddr((EmailConfig.SMTP_FROM_NAME or BRAND, EmailConfig.SMTP_USER))
    msg["To"] = to_addr
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)
    for i, (cid, png) in enumerate(zip(cids, pngs)):
        img = MIMEImage(png, "png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"tick-and-book-flight-{i + 1}.png")
        msg.attach(img)
    return msg


def _smtp_send(msg: MIMEMultipart, to_addr: str) -> tuple[bool, str | None]:
    """Same connection as email_service._send, for a message with pictures."""
    try:
        with smtplib.SMTP(EmailConfig.SMTP_HOST, EmailConfig.SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(EmailConfig.SMTP_USER, EmailConfig.SMTP_PASSWORD)
            server.sendmail(EmailConfig.SMTP_USER, [to_addr], msg.as_string())
        return True, None
    except Exception as e:
        return False, str(e)


def send_share_email(customer: dict, to: str, results_path: str, text: str, images: list) -> dict:
    """customer: the decoded customer token (customer_id, sub = email).
    Raises ShareFareError for anything refused or failed."""
    customer_id = customer.get("customer_id")
    to_addr = validate_email(str(to))
    link = build_link(results_path)
    pngs = decode_images(images)
    body = clean_text(text, link)
    check_rate_limit(customer_id)
    if not EmailConfig.smtp_configured():
        raise ShareFareError(503, "Sending by email isn't available just now. Please use WhatsApp or your own email app.")

    msg = compose(_sender_name(customer), customer.get("sub"), to_addr, body, link, pngs)
    sent, error = _smtp_send(msg, to_addr)
    if not sent:
        logger.error(f"Share fare email by customer {customer_id} failed: {error}")
        raise ShareFareError(502, "We couldn't send the email just now. Please try again in a minute.")
    record_send(customer_id)
    logger.info(f"Share fare email sent by customer {customer_id}: {len(pngs)} fare(s)")
    return {"sent": True, "to": to_addr, "fares": len(pngs)}

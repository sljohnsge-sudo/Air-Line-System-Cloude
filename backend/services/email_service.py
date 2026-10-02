"""
services/email_service.py
===========================
Sends the visa consultation booking notification to the consultant mailbox
(and a confirmation copy to the customer) over SMTP (Microsoft 365 / Outlook).

Best-effort: a booking is always saved to the database first (see
database.create_visa_consultation) regardless of whether the email goes out,
so a slot is never lost to an SMTP hiccup. Callers check the returned
(sent, error) tuple to tell the customer whether the notification actually
went out.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config.email_config import EmailConfig


def _send(to_addr: str, subject: str, body: str) -> tuple[bool, str | None]:
    """to_addr may be a single address or a comma-separated list — every
    recipient gets the same message, addressed to all of them in the To: header."""
    if not EmailConfig.smtp_configured():
        return False, "SMTP is not configured yet (missing SMTP_USER / SMTP_PASSWORD in .env)."

    to_list = [addr.strip() for addr in to_addr.split(",") if addr.strip()]
    if not to_list:
        return False, "No recipient email address."

    msg = MIMEMultipart()
    msg["From"] = f"{EmailConfig.SMTP_FROM_NAME} <{EmailConfig.SMTP_USER}>"
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(EmailConfig.SMTP_HOST, EmailConfig.SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(EmailConfig.SMTP_USER, EmailConfig.SMTP_PASSWORD)
            server.sendmail(EmailConfig.SMTP_USER, to_list, msg.as_string())
        return True, None
    except Exception as e:
        return False, str(e)


def send_visa_consultation_email(booking: dict) -> tuple[bool, str | None]:
    """Notifies the consultant mailbox(es) of a new booking. Returns (sent, error).

    Routes to every officer assigned to the destination country
    (booking['consultant_emails'], comma-joined and snapshotted at booking time
    by database.get_visa_consultants_by_country) when any are set, otherwise
    falls back to the general VISA_CONSULTANT_EMAIL in .env.
    """
    to_addr = booking.get("consultant_emails") or EmailConfig.VISA_CONSULTANT_EMAIL
    if not to_addr:
        return False, "No consultant email on file for this country and no general VISA_CONSULTANT_EMAIL configured."

    consultant_line = f" (assigned to {booking['consultant_names']})" if booking.get("consultant_names") else ""
    subject = f"New Visa Consultation Booking — {booking['slot_date']} {booking['slot_time']}"
    body = (
        f"A customer has booked a visa consultation slot{consultant_line}.\n\n"
        f"Date:        {booking['slot_date']}\n"
        f"Time:        {booking['slot_time']}\n\n"
        f"Nationality: {booking['nationality']}\n"
        f"Destination: {booking['destination']}\n\n"
        f"Name:        {booking['full_name']}\n"
        f"Email:       {booking['email']}\n"
        f"Phone:       {booking['phone']}\n"
        f"Notes:       {booking.get('notes') or '—'}\n\n"
        f"Booking reference: #{booking['id']}\n"
    )
    return _send(to_addr, subject, body)


def send_provider_failure_email(to_addr: str, provider: str, error_message: str, route_info: str | None) -> tuple[bool, str | None]:
    """Alerts staff that the unified flight search (Travelport "TP" + Amadeus
    "AD") couldn't get fares back from one of the two providers -- so results
    shown to customers are silently one-sided until someone notices. Called
    from main.py's /api/flights/search aggregator; to_addr is the
    comma-joined list of active notification_recipients."""
    provider_name = {"TP": "Travelport", "AD": "Amadeus"}.get(provider, provider)
    subject = f"Fare search failed — {provider_name} did not return results"
    body = (
        f"The unified flight search could not get fares from {provider_name} ({provider}).\n\n"
        f"Route:   {route_info or 'n/a'}\n"
        f"Error:   {error_message}\n\n"
        f"Customers searching this route are currently only seeing "
        f"{'Amadeus' if provider == 'TP' else 'Travelport'} fares, if any. "
        f"Check the provider's status and the Admin Portal's Notifications tab for the full log.\n"
    )
    return _send(to_addr, subject, body)

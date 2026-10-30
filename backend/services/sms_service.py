"""
services/sms_service.py
=========================
Small provider-agnostic SMS sender. The gateway is picked in .env
(config/sms_config.py); until one is configured every send is skipped with
"SMS not configured" and nothing fails.

Sri Lankan gateways all take a simple HTTP call. Shapes below follow each
provider's public API docs — check them against the account's own docs when
the provider is chosen:
- notifylk: GET https://app.notify.lk/api/v1/send
            ?user_id&api_key&sender_id&to=94XXXXXXXXX&message
- textlk:   POST https://app.text.lk/api/v3/sms/send, Bearer token,
            JSON {recipient: "94XXXXXXXXX", sender_id, type: "plain", message}
- generic:  POST SMS_API_URL, Bearer SMS_API_KEY,
            JSON {to: "+94XXXXXXXXX", sender_id, message} — for any other
            gateway (e.g. Dialog), or adapt here.
"""
import logging
import re

import httpx

from config.sms_config import SmsConfig

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "SMS not configured"


def normalise_phone(raw: str | None, default_country: str = "94") -> str | None:
    """'0771234567', '771234567', '94771234567', '+94 77 123 4567',
    '0094771234567' -> '+94771234567'. Other international numbers given
    with + or 00 are kept. None if it doesn't look like a phone number."""
    if not raw:
        return None
    text = str(raw).strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith(default_country) and len(digits) == 11:
        pass
    elif digits.startswith("0") and len(digits) == 10:
        digits = default_country + digits[1:]
    elif len(digits) == 9:
        digits = default_country + digits
    else:
        return None
    return f"+{digits}" if 8 <= len(digits) <= 15 else None


def send_sms(to: str, message: str) -> tuple[bool, str | None]:
    """to: international format (+94…). Returns (sent, error)."""
    if not SmsConfig.configured():
        logger.info(f"{NOT_CONFIGURED} — skipped SMS to {to[:5]}…")
        return False, NOT_CONFIGURED
    number = to.lstrip("+")
    try:
        if SmsConfig.PROVIDER == "notifylk":
            r = httpx.get(SmsConfig.API_URL or "https://app.notify.lk/api/v1/send", timeout=15, params={
                "user_id": SmsConfig.USER_ID, "api_key": SmsConfig.API_KEY, "sender_id": SmsConfig.SENDER_ID,
                "to": number, "message": message})
        elif SmsConfig.PROVIDER == "textlk":
            r = httpx.post(SmsConfig.API_URL or "https://app.text.lk/api/v3/sms/send", timeout=15,
                           headers={"Authorization": f"Bearer {SmsConfig.API_KEY}", "Accept": "application/json"},
                           json={"recipient": number, "sender_id": SmsConfig.SENDER_ID, "type": "plain", "message": message})
        else:
            r = httpx.post(SmsConfig.API_URL, timeout=15, headers={"Authorization": f"Bearer {SmsConfig.API_KEY}"},
                           json={"to": to, "sender_id": SmsConfig.SENDER_ID, "message": message})
    except httpx.HTTPError as e:
        return False, f"SMS gateway unreachable: {e}"
    if r.status_code >= 400:
        return False, f"SMS gateway answered HTTP {r.status_code}: {r.text[:200]}"
    return True, None

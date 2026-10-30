"""
config/sms_config.py
======================
Outbound SMS gateway settings, read from .env. Nothing is filled in by
default: until SMS_PROVIDER and its credentials are set, SMS is skipped
(logged "SMS not configured") and never fails the caller.

  SMS_PROVIDER    notifylk | textlk | generic
  SMS_API_URL     gateway endpoint (optional for notifylk / textlk, which have defaults)
  SMS_API_KEY     API key / token
  SMS_USER_ID     account / user id (Notify.lk needs it)
  SMS_SENDER_ID   approved sender name (mask)

To update credentials: edit the .env file. Never hardcode values here.
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


class SmsConfig:
    PROVIDER: str = os.getenv("SMS_PROVIDER", "").strip().lower()
    API_URL: str = os.getenv("SMS_API_URL", "").strip()
    API_KEY: str = os.getenv("SMS_API_KEY", "").strip()
    USER_ID: str = os.getenv("SMS_USER_ID", "").strip()
    SENDER_ID: str = os.getenv("SMS_SENDER_ID", "").strip()

    @classmethod
    def configured(cls) -> bool:
        if cls.PROVIDER == "notifylk":
            return bool(cls.API_KEY and cls.USER_ID and cls.SENDER_ID)
        if cls.PROVIDER == "textlk":
            return bool(cls.API_KEY and cls.SENDER_ID)
        if cls.PROVIDER == "generic":
            return bool(cls.API_URL and cls.API_KEY)
        return False

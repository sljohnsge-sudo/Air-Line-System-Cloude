"""
config/cybersource_config.py
============================
NDB Bank payment gateway — CyberSource Secure Acceptance Hosted Checkout.
All credentials come from .env; nothing is hard-coded here.

PAYMENT_GATEWAY picks the gateway the site uses: "cybersource" (NDB) or
"paycorp" (Sampath, the previous gateway). CYBERSOURCE_ENV "test" uses the
sandbox (testsecureacceptance.cybersource.com); "live" the production host —
only switch to live with production keys from the bank.
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


class CyberSourceConfig:
    GATEWAY: str = os.getenv("PAYMENT_GATEWAY", "paycorp").strip().lower()
    ENV: str = os.getenv("CYBERSOURCE_ENV", "test").strip().lower()
    PROFILE_ID: str = os.getenv("CYBERSOURCE_PROFILE_ID", "")
    ACCESS_KEY: str = os.getenv("CYBERSOURCE_ACCESS_KEY", "")
    SECRET_KEY: str = os.getenv("CYBERSOURCE_SECRET_KEY", "")
    LOCALE: str = os.getenv("CYBERSOURCE_LOCALE", "en-us")
    # Public address of this backend's /api, used for the bank's receipt
    # page. Empty = the same site the customer pays from (its /api).
    PUBLIC_API_BASE: str = os.getenv("CYBERSOURCE_PUBLIC_API_BASE", "").rstrip("/")

    @classmethod
    def pay_url(cls) -> str:
        host = "secureacceptance.cybersource.com" if cls.ENV == "live" else "testsecureacceptance.cybersource.com"
        return f"https://{host}/pay"

    @classmethod
    def configured(cls) -> bool:
        return bool(cls.PROFILE_ID and cls.ACCESS_KEY and cls.SECRET_KEY)

    @classmethod
    def active(cls) -> bool:
        return cls.GATEWAY == "cybersource"

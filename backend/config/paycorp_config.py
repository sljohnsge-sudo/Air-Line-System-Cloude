"""
config/paycorp_config.py
=========================
PayCorp (Sampath Bank) Payment Gateway Configuration
All credential and endpoint settings for the PayCorp REST proxy API.

To update credentials: edit the .env file. Never hardcode values here.
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


class PayCorpConfig:
    """Central configuration class for PayCorp payment gateway access."""

    ENDPOINT: str = os.getenv("PAYCORP_ENDPOINT", "https://sampath.paycorp.lk/rest/service/proxy")
    AUTH_TOKEN: str = os.getenv("PAYCORP_AUTH_TOKEN", "")
    HMAC_SECRET: str = os.getenv("PAYCORP_HMAC_SECRET", "")

    # Two merchant Client IDs — one per settlement currency.
    CLIENT_ID_LKR: str = os.getenv("PAYCORP_CLIENT_ID_LKR", "")
    CLIENT_ID_USD: str = os.getenv("PAYCORP_CLIENT_ID_USD", "")

    @classmethod
    def client_id_for_currency(cls, currency: str) -> str:
        """Return the Client ID matching the booking's currency (LKR or USD)."""
        return cls.CLIENT_ID_USD if (currency or "").upper() == "USD" else cls.CLIENT_ID_LKR

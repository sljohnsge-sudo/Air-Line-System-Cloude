"""
config/email_config.py
========================
Outbound SMTP configuration for the Visa Consultation email notification.

To update credentials: edit the .env file. Never hardcode values here.
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


class EmailConfig:
    """Central configuration class for outbound SMTP email."""

    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.office365.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_NAME: str = os.getenv("SMTP_FROM_NAME", "Final Travels Visa Desk")

    # Fallback recipient when a destination country has no assigned consultant
    # in the visa_consultants table (see database.get_visa_consultant_by_country).
    VISA_CONSULTANT_EMAIL: str = os.getenv("VISA_CONSULTANT_EMAIL", "")

    @classmethod
    def smtp_configured(cls) -> bool:
        """Whether outbound SMTP itself is usable — independent of which recipient a given email targets."""
        return bool(cls.SMTP_USER and cls.SMTP_PASSWORD)

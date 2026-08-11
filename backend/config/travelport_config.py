"""
travelport_config.py
====================
STEP 1 — Travelport API Configuration
All credential and authentication settings for the Travelport TripServices API.

To update credentials: edit the .env file. Never hardcode values here.
"""

import os
import uuid
from dotenv import load_dotenv

# Load credentials from .env file located in the backend directory
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


class TravelportConfig:
    """Central configuration class for Travelport API access."""

    # ── OAuth 2.0 ─────────────────────────────────────────────────────────────
    AUTH_URL: str = os.getenv("TP_AUTH_URL", "https://auth.pp.travelport.net/oauth/token")
    CLIENT_ID: str = os.getenv("TP_CLIENT_ID", "")
    CLIENT_SECRET: str = os.getenv("TP_CLIENT_SECRET", "")
    USERNAME: str = os.getenv("TP_USERNAME", "")
    PASSWORD: str = os.getenv("TP_PASSWORD", "")

    # ── API Base ───────────────────────────────────────────────────────────────
    API_BASE: str = os.getenv("TP_API_BASE", "https://api.pp.travelport.net")
    API_VERSION: str = os.getenv("TP_API_VERSION", "11")

    # ── Agency / PCC ──────────────────────────────────────────────────────────
    PCC: str = os.getenv("TP_PCC", "7F3C")
    ACCESS_GROUP: str = os.getenv("TP_ACCESS_GROUP", "")

    # ── PCC Core header (TVP-PCC-Core) — same as PCC by default ───────────────
    TVP_PCC_CORE: str = os.getenv("TVP_PCC_CORE", os.getenv("TP_PCC", "7F3C"))

    # ── Content Source ─────────────────────────────────────────────────────────
    # "GDS" for Travelport GDS content, "NDC" for NDC carriers
    CONTENT_SOURCE: str = os.getenv("TP_CONTENT_SOURCE", "GDS")

    # ── Search Defaults ────────────────────────────────────────────────────────
    OFFERS_PER_PAGE: int = int(os.getenv("TP_OFFERS_PER_PAGE", "45"))
    MAX_UPSELLS: int = int(os.getenv("TP_MAX_UPSELLS", "1"))

    # ── Request Defaults ──────────────────────────────────────────────────────
    DEFAULT_CURRENCY: str = "USD"
    DEFAULT_LANGUAGE: str = "en"
    REQUEST_TIMEOUT: int = 120   # seconds per HTTP request

    @classmethod
    def base_path(cls) -> str:
        """Returns the versioned base path e.g. https://api.pp.travelport.net/11"""
        return f"{cls.API_BASE}/{cls.API_VERSION}"

    @classmethod
    def generate_trace_id(cls) -> str:
        """Generate a unique TraceId for each request."""
        return f"TraceID_{uuid.uuid4().hex[:12].upper()}"

    @classmethod
    def generate_session_id(cls) -> str:
        """Generate a unique travelportPlusSessionIdentifier for each request."""
        return str(uuid.uuid4())

    @classmethod
    def get_agency_context(cls) -> dict:
        """Returns the agency context block required by Travelport booking requests."""
        return {
            "AgencyContext": {
                "PseudoCityCode": cls.PCC,
                "IATA_Number": None,
                "CountryCode": "LK"
            }
        }

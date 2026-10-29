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

    # ── Agency Identity (Add Travel Agency step, GDS bookings) ────────────────
    # George Steuart Travel's own registered address/contact/corporate code,
    # sent on the Add Travel Agency call (Travelport GDS certification step 7).
    # Optional per Travelport docs (mandatory only for AF/KL NDC bookings), but
    # included on GDS bookings to match Travelport's certification reference.
    AGENCY_NAME: str = os.getenv("TP_AGENCY_NAME", "George Steuart Travel")
    AGENCY_ADDRESS_LINE: str = os.getenv("TP_AGENCY_ADDRESS_LINE", "Ground Floor, 439 Galle Rd")
    AGENCY_CITY: str = os.getenv("TP_AGENCY_CITY", "Colombo")
    AGENCY_POSTAL_CODE: str = os.getenv("TP_AGENCY_POSTAL_CODE", "00300")
    AGENCY_COUNTRY: str = os.getenv("TP_AGENCY_COUNTRY", "LK")
    AGENCY_CORPORATE_CODE: str = os.getenv("TP_AGENCY_CORPORATE_CODE", "7F3C")
    AGENCY_PHONE_COUNTRY_CODE: str = os.getenv("TP_AGENCY_PHONE_COUNTRY_CODE", "94")
    AGENCY_PHONE_AREA_CODE: str = os.getenv("TP_AGENCY_PHONE_AREA_CODE", "11")
    AGENCY_PHONE_NUMBER: str = os.getenv("TP_AGENCY_PHONE_NUMBER", "7792400")
    AGENCY_EMAIL: str = os.getenv("TP_AGENCY_EMAIL", "ticketing@gstravels.lk")

    # ── Content Sources ────────────────────────────────────────────────────────
    # Searched together by default (contentSourceList). /api/ndc/search still
    # narrows a single request to NDC.
    CONTENT_SOURCES: list = [s.strip().upper() for s in os.getenv("TP_CONTENT_SOURCES", "GDS,NDC").split(",") if s.strip()]

    # ── Search Defaults ────────────────────────────────────────────────────────
    # offersPerPage must be sent so journey-based results are cached for the
    # reference calls that follow (AirPrice/Add Offer); 999 keeps all offers.
    # 4 is the most GDS upsells Travelport returns in a Search response.
    OFFERS_PER_PAGE: int = int(os.getenv("TP_OFFERS_PER_PAGE", "999"))
    MAX_UPSELLS: int = int(os.getenv("TP_MAX_UPSELLS", "4"))

    # ── Request Defaults ──────────────────────────────────────────────────────
    # Sent as PricingModifiersAir.currencyCode on every Search request so GDS
    # and NDC offers alike come back priced in the agency's own currency
    # instead of each airline's filed currency — confirmed live that without
    # this, NDC content (e.g. Singapore Airlines SQ on CMB-SIN) came back
    # priced in SGD while every GDS offer on the same search correctly used
    # the PCC's default (LKR), a currency mismatch within one results page.
    DEFAULT_CURRENCY: str = os.getenv("TP_CURRENCY", "LKR")
    DEFAULT_LANGUAGE: str = "en"
    REQUEST_TIMEOUT: int = 120   # seconds per HTTP request

    @classmethod
    def is_production(cls) -> bool:
        """True when pointed at Travelport production rather than pre-production (.pp.)."""
        return ".pp." not in cls.API_BASE and ".pp." not in cls.AUTH_URL

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

"""
config/hotel_config.py
=======================
Travelport TripServices Stays (Hotel) v11/v12 API configuration.

Kept entirely separate from travelport_config.py (Air) — same Travelport
account/OAuth credentials are reused (OAuth is account-wide, not
product-scoped), but Hotel has its own base paths, since SearchComplete is
on ODM v12 while every other Hotel endpoint is v11.

To update credentials: edit the .env file. Never hardcode values here.

NOTE: As of this integration, the sandbox account (TP_USERNAME in .env) is
NOT yet provisioned for the Hotel/Stays product — every Hotel endpoint
returns 403 at the Akamai edge, confirmed via live testing. This code is
written strictly to the documented API contract
(https://developer.travelport.com/apis/stays) and cannot be verified
end-to-end until Travelport enables Hotel/Stays access on the account.
"""

import os
import uuid
from dotenv import load_dotenv
from config.travelport_config import TravelportConfig

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


class HotelConfig:
    """Central configuration class for Travelport Hotel (Stays) API access."""

    # ── API Base (Hotel has its own version split: v12 for SearchComplete, v11 for everything else) ──
    API_BASE: str = TravelportConfig.API_BASE  # e.g. https://api.pp.travelport.net
    V11_BASE: str = f"{API_BASE}/11/hotel"
    V12_BASE: str = f"{API_BASE}/12/hotel"

    # ── Agency / PCC (reused from the Air account — same Travelport agency) ──
    PCC: str = TravelportConfig.PCC
    ACCESS_GROUP: str = TravelportConfig.ACCESS_GROUP

    DEFAULT_CURRENCY: str = os.getenv("HOTEL_DEFAULT_CURRENCY", "USD")
    REQUEST_TIMEOUT: int = 120

    # ── Guarantee / deposit card ──────────────────────────────────────────────
    # Travelport's Create Reservation API requires a FormOfPayment.PaymentCard
    # block to guarantee or prepay the room with the hotel supplier — this is
    # DIFFERENT from how flight tickets are settled (no card is ever sent to
    # Travelport for flights; PayCorp handles the customer charge entirely
    # out-of-band). For hotels, Travelport itself needs a real card on file.
    #
    # The customer should still be charged via the existing PayCorp gateway
    # for the total amount (same pattern as flights, card never touches this
    # backend). The card configured here is the AGENCY's own guarantee/virtual
    # card used solely to satisfy Travelport's booking requirement — it must
    # be entered directly into .env by the agency, never typed or generated
    # by this codebase.
    GUARANTEE_CARD_HOLDER_NAME: str = os.getenv("HOTEL_GUARANTEE_CARD_HOLDER_NAME", "")
    GUARANTEE_CARD_NUMBER: str = os.getenv("HOTEL_GUARANTEE_CARD_NUMBER", "")
    GUARANTEE_CARD_TYPE: str = os.getenv("HOTEL_GUARANTEE_CARD_TYPE", "Credit")
    GUARANTEE_CARD_CODE: str = os.getenv("HOTEL_GUARANTEE_CARD_CODE", "")  # e.g. VI, MC, AX
    GUARANTEE_CARD_EXPIRE: str = os.getenv("HOTEL_GUARANTEE_CARD_EXPIRE", "")  # MMYY
    GUARANTEE_CARD_CVV: str = os.getenv("HOTEL_GUARANTEE_CARD_CVV", "")
    GUARANTEE_CARD_BILLING_COUNTRY: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_COUNTRY", "LK")
    GUARANTEE_CARD_BILLING_POSTAL: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_POSTAL", "")
    GUARANTEE_CARD_BILLING_CITY: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_CITY", "")
    GUARANTEE_CARD_BILLING_ADDRESS: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_ADDRESS", "")

    @classmethod
    def guarantee_card_configured(cls) -> bool:
        return bool(cls.GUARANTEE_CARD_NUMBER and cls.GUARANTEE_CARD_EXPIRE and cls.GUARANTEE_CARD_CODE)

    @classmethod
    def generate_trace_id(cls) -> str:
        return f"TraceID_{uuid.uuid4().hex[:12].upper()}"

    @classmethod
    def generate_correlation_id(cls) -> str:
        return str(uuid.uuid4())


class HotelEndpoints:
    """All Travelport Hotel (Stays) REST endpoint URLs.
    https://developer.travelport.com/apis/stays
    """

    # ── Search (v12 — combines search + details + availability in one call) ──
    SEARCH_COMPLETE = f"{HotelConfig.V12_BASE}/search/searchcomplete"

    # ── Property Details (v11, optional enrichment) ────────────────────────
    PROPERTY_DETAILS = f"{HotelConfig.V11_BASE}/search/propertiesdetail"

    # ── Create Reservation — Full Payload (v11) ─────────────────────────────
    # Sends complete PropertyKey/DateRange/bookingCode/Price rather than a
    # cached rateKey reference — mirrors this codebase's existing GDS
    # certification practice for Air (see api_endpoints.py
    # add_offer_to_workbench_full_payload): full payload has no dependency
    # on a cached session that can expire before the user finishes checkout.
    CREATE_RESERVATION = f"{HotelConfig.V11_BASE}/book/reservations"

    # ── Retrieve / Cancel ────────────────────────────────────────────────────
    @staticmethod
    def retrieve_reservation(locator_code: str) -> str:
        """GET → retrieve full hotel reservation details by locator code."""
        return f"{HotelConfig.V11_BASE}/book/reservations/{locator_code}"

    @staticmethod
    def cancel_reservation(locator_code: str, supplier_locator: str) -> str:
        """PUT → cancel a hotel reservation (or a single room/offer on it)."""
        return f"{HotelConfig.V11_BASE}/book/reservations/{locator_code}/canceloffer?supplierLocator={supplier_locator}"

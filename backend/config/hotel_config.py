"""
config/hotel_config.py
=======================
Travelport Stays (Hotel) API configuration — being rebuilt step by step
against the official docs at https://developer.travelport.com/apis/stays.

Deliberately independent of travelport_config.py (Air): its own OAuth
credentials (HOTEL_TP_*), its own base paths, its own PCC/access group.
Nothing here is read by, or reads from, the Air/flight side — per instruction,
the Air booking system is not to be touched while Hotel is reconfigured.

To update credentials: edit the .env file. Never hardcode values here.
"""

import os
import uuid
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


class HotelConfig:
    """Central configuration class for Travelport Hotel (Stays) API access."""

    # ── OAuth 2.0 (separate from the Air TP_* credentials) ──────────────────
    AUTH_URL: str = os.getenv("HOTEL_TP_AUTH_URL", "https://auth.pp.travelport.net/oauth/token")
    CLIENT_ID: str = os.getenv("HOTEL_TP_CLIENT_ID", "")
    CLIENT_SECRET: str = os.getenv("HOTEL_TP_CLIENT_SECRET", "")
    USERNAME: str = os.getenv("HOTEL_TP_USERNAME", "")
    PASSWORD: str = os.getenv("HOTEL_TP_PASSWORD", "")

    # ── API Base — every Hotel/Stays endpoint used here is v11 ──────────────
    API_BASE: str = os.getenv("HOTEL_TP_API_BASE", "https://api.pp.travelport.net")
    V11_BASE: str = f"{API_BASE}/11/hotel"

    # ── Agency / PCC (own Hotel/Stays provisioning — not the Air agency's) ──
    PCC: str = os.getenv("HOTEL_TP_PCC", "")
    ACCESS_GROUP: str = os.getenv("HOTEL_TP_ACCESS_GROUP", "")

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
    # Confirmed present in Travelport's own Create Reservation examples
    # (https://developer.travelport.com/apis/stays/unified-check-out/createhotelreservation)
    # but optional — only sent if configured, same as the fields above.
    GUARANTEE_CARD_BILLING_STREET_NUMBER: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_STREET_NUMBER", "")
    GUARANTEE_CARD_BILLING_STATE_PROV: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_STATE_PROV", "")
    GUARANTEE_CARD_BILLING_COUNTY: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_COUNTY", "")
    GUARANTEE_CARD_BILLING_PHONE_COUNTRY_CODE: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_PHONE_COUNTRY_CODE", "")
    GUARANTEE_CARD_BILLING_PHONE_AREA_CODE: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_PHONE_AREA_CODE", "")
    GUARANTEE_CARD_BILLING_PHONE_NUMBER: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_PHONE_NUMBER", "")
    GUARANTEE_CARD_BILLING_EMAIL: str = os.getenv("HOTEL_GUARANTEE_CARD_BILLING_EMAIL", "")

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

    # ── Search and Details — https://developer.travelport.com/apis/stays/search-and-details ──
    # Search by Location: geo coordinates, address, IATA airport code, or IATA city code.
    SEARCH_BY_LOCATION = f"{HotelConfig.V11_BASE}/search/properties/search"

    # Search by Property ID: up to 250 known chainCode/propertyCode pairs.
    SEARCH_BY_PROPERTY_ID = f"{HotelConfig.V11_BASE}/search/properties"

    # Property Details — optional enrichment (description/images), no rates.
    PROPERTY_DETAILS = f"{HotelConfig.V11_BASE}/search/propertiesdetail"

    @staticmethod
    def search_properties_page(identifier: str) -> str:
        """GET → next page (2-5) of a Search by Location/ID result set over 100 properties."""
        return f"{HotelConfig.V11_BASE}/search/properties/{identifier}"

    # ── Availability — https://developer.travelport.com/apis/stays/availability ──
    # Returns the actual bookable room types/rates for one or more properties
    # (Search by Location only returns property-level summaries). Offers
    # returned here are cached 30 minutes.
    AVAILABILITY = f"{HotelConfig.V11_BASE}/availability/catalogofferingshospitality"

    @staticmethod
    def availability_page(identifier: str) -> str:
        """GET → next page (2-5) of an Availability result set over 100 rates."""
        return f"{HotelConfig.V11_BASE}/availability/catalogofferingshospitality/{identifier}"

    # ── Rules — https://developer.travelport.com/apis/stays/rules ──────────
    # Reference payload: send the CatalogOffering.id from an Availability
    # response. (buildfromcatalogofferings, the plural/older variant, is
    # marked Deprecated in Travelport's own docs — use this one instead.)
    RULES_BUILD_FROM_OFFERING = f"{HotelConfig.V11_BASE}/rules/offershospitality/buildfromcatalogoffering"

    # ── Create Reservation — Full Payload (v11) ─────────────────────────────
    # Sends complete PropertyKey/DateRange/bookingCode/Price rather than a
    # cached rateKey reference — mirrors this codebase's existing GDS
    # certification practice for Air (see api_endpoints.py
    # add_offer_to_workbench_full_payload): full payload has no dependency
    # on a cached session that can expire before the user finishes checkout.
    CREATE_RESERVATION = f"{HotelConfig.V11_BASE}/book/reservations"

    # ── Create Reservation — Reference Payload (v11) ────────────────────────
    # Books against a cached CatalogOffering.id from an Availability response
    # instead of resending full rate details — used by the Search ->
    # Availability -> book flow. See
    # https://developer.travelport.com/apis/stays/unified-check-out/buildhotelreservation.
    CREATE_RESERVATION_REFERENCE = f"{HotelConfig.V11_BASE}/book/reservations/build"

    # ── Retrieve / Cancel ────────────────────────────────────────────────────
    @staticmethod
    def retrieve_reservation(locator_code: str) -> str:
        """GET → retrieve full hotel reservation details by locator code."""
        return f"{HotelConfig.V11_BASE}/book/reservations/{locator_code}"

    @staticmethod
    def cancel_reservation(locator_code: str) -> str:
        """PUT → cancel a hotel reservation. supplierLocator is passed as a
        query param by the caller (via httpx params=), not baked in here, so
        it's properly URL-encoded — see
        https://developer.travelport.com/apis/stays/unified-check-out/cancelhoteloffer.
        """
        return f"{HotelConfig.V11_BASE}/book/reservations/{locator_code}/canceloffer"

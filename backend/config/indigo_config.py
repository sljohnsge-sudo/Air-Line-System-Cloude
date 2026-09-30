"""
config/indigo_config.py
========================
Travelport legacy Universal API (uAPI) configuration — IndiGo (6E) ancillary
content (meals, baggage, seat selection).

Kept entirely separate from travelport_config.py (Air, JSON TripServices
v11) and hotel_config.py (Hotel/Stays) — this is a DIFFERENT Travelport
product with its own credential set and its own SOAP/XML wire format
(AirService + UniversalRecordService, schemas air_v43_0 / universal_v43_0),
not the modern JSON REST API the rest of this codebase uses. Provider code
ACH is IndiGo's Travelport uAPI identifier.

Written strictly to the request/response shapes in the reference logs under
"Indigo all ancillary service" (LFS_Req/Res, APR_WOOS/WTOS_Req/Res,
SeatMapReq/Res, ACR_Req/Res) — also available converted to this project's
own JSON convention at backend/reference/indigo_uapi_json/, for readability
alongside the rest of the JSON-based codebase.

NO ENDPOINT IS HARDCODED HERE. The reference logs under "Indigo all
ancillary service" contain only request/response bodies — every file was
checked and none of them mention an AirService/UniversalRecordService host.
An earlier version of this file defaulted TP_UAPI_BASE_URL to a guessed
regional host (apac.universal-api.pp.travelport.com) when live-tested that
guess did reach a real Travelport Axis SOAP server, but per explicit
instruction that guess has been removed rather than left in as a silent
fallback: TP_UAPI_BASE_URL must now be supplied directly in .env, from your
Travelport account documentation or account rep, before anything in this
module will run.

To update credentials/endpoint: edit the .env file (TP_UAPI_* keys). Never
hardcode values here.

NOTE: As of this integration, TP_UAPI_USERNAME/PASSWORD/TARGET_BRANCH/
BASE_URL in .env are all empty — this account has not yet been provisioned
with legacy Universal API credentials or a confirmed endpoint. Nothing in
this module can be exercised end-to-end until they are filled in.
"""

import os
import uuid
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


class IndigoConfig:
    """Central configuration for Travelport legacy Universal API (uAPI) access, IndiGo/ACH content."""

    USERNAME: str = os.getenv("TP_UAPI_USERNAME", "")
    PASSWORD: str = os.getenv("TP_UAPI_PASSWORD", "")
    TARGET_BRANCH: str = os.getenv("TP_UAPI_TARGET_BRANCH", "")
    # No default — see module docstring. Must come from your Travelport
    # account documentation, not a guess baked into this codebase.
    BASE_URL: str = (os.getenv("TP_UAPI_BASE_URL") or "").rstrip("/")

    PROVIDER_CODE = "ACH"   # IndiGo's Travelport uAPI provider code
    CARRIER_CODE = "6E"

    REQUEST_TIMEOUT: int = 120  # seconds

    # ── Agency guarantee card ────────────────────────────────────────────────
    # AirCreateReservationReq requires a real FormOfPayment/CreditCard to
    # ticket (see ACR_Req.xml / reference/indigo_uapi_json/ACR_Req.json) —
    # same PayCorp-charges-the-customer-separately pattern as
    # config/hotel_config.py's guarantee card. Never accepts a
    # customer-entered card; this is the agency's own card, entered directly
    # into .env.
    GUARANTEE_CARD_HOLDER_NAME: str = os.getenv("TP_UAPI_GUARANTEE_CARD_HOLDER_NAME", "")
    GUARANTEE_CARD_NUMBER: str = os.getenv("TP_UAPI_GUARANTEE_CARD_NUMBER", "")
    GUARANTEE_CARD_TYPE: str = os.getenv("TP_UAPI_GUARANTEE_CARD_TYPE", "")  # e.g. VI, MC, AX
    GUARANTEE_CARD_EXPIRY: str = os.getenv("TP_UAPI_GUARANTEE_CARD_EXPIRY", "")  # YYYY-MM
    GUARANTEE_CARD_CVV: str = os.getenv("TP_UAPI_GUARANTEE_CARD_CVV", "")
    GUARANTEE_CARD_BILLING_COUNTRY: str = os.getenv("TP_UAPI_GUARANTEE_CARD_BILLING_COUNTRY", "")
    GUARANTEE_CARD_BILLING_STATE: str = os.getenv("TP_UAPI_GUARANTEE_CARD_BILLING_STATE", "")
    GUARANTEE_CARD_BILLING_CITY: str = os.getenv("TP_UAPI_GUARANTEE_CARD_BILLING_CITY", "")
    GUARANTEE_CARD_BILLING_POSTAL: str = os.getenv("TP_UAPI_GUARANTEE_CARD_BILLING_POSTAL", "")
    GUARANTEE_CARD_BILLING_ADDRESS: str = os.getenv("TP_UAPI_GUARANTEE_CARD_BILLING_ADDRESS", "")

    @classmethod
    def is_configured(cls) -> bool:
        """True once all four uAPI credential fields have been filled into .env."""
        return bool(cls.USERNAME and cls.PASSWORD and cls.TARGET_BRANCH and cls.BASE_URL)

    @classmethod
    def guarantee_card_configured(cls) -> bool:
        return bool(cls.GUARANTEE_CARD_NUMBER and cls.GUARANTEE_CARD_EXPIRY and cls.GUARANTEE_CARD_TYPE)

    @classmethod
    def generate_trace_id(cls) -> str:
        return f"UAPI_{uuid.uuid4().hex[:12].upper()}"


class IndigoEndpoints:
    """
    Travelport legacy Universal API SOAP endpoints.

    NOT VERIFIED — no endpoint host is hardcoded anywhere in this codebase
    (see IndigoConfig.BASE_URL / module docstring); it comes entirely from
    TP_UAPI_BASE_URL in .env. The "/AirService" and "/UniversalRecordService"
    path suffixes below follow Travelport's documented uAPI convention (one
    path per WSDL service, chosen by the request's root XML element) but
    have not been confirmed against this account's own WSDL either — confirm
    both the host and these paths against your Travelport uAPI account
    documentation once available.
    """

    @staticmethod
    def air_service() -> str:
        """LowFareSearchReq, AirPriceReq, SeatMapReq all post here."""
        return f"{IndigoConfig.BASE_URL}/AirService"

    @staticmethod
    def universal_record_service() -> str:
        """AirCreateReservationReq (and other Universal Record operations) post here."""
        return f"{IndigoConfig.BASE_URL}/UniversalRecordService"

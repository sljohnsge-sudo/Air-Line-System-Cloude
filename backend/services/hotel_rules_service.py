"""
services/hotel_rules_service.py
=================================
Hotel Rules (Reference Payload) —
https://developer.travelport.com/apis/stays/rules/buildhotelrulesfromcatalogoffering

Returns the cancellation policy / terms for a specific Availability offer.
Informational only — Create Reservation does not require a prior Rules call
to succeed, but this lets the customer see the cancellation policy before
booking.

(The plural "buildfromcatalogofferings" variant is marked Deprecated in
Travelport's own docs — "use BuildFromCatalogOffering end point" — so this
singular, reference-payload-by-one-offer endpoint is the one wired up here.)

Kept fully independent of services/search_service.py (Air) — no shared code,
no shared endpoints, per instruction not to touch the flight side.
"""

import httpx
import logging
from config.hotel_config import HotelConfig, HotelEndpoints
from services.hotel_common import get_hotel_headers, HotelApiError
from utils import tp_logger

logger = logging.getLogger(__name__)


def get_rules_for_offer(offer_id: str) -> dict:
    """
    Fetch the rules/cancellation policy for a cached Availability offer.

    Args:
        offer_id: CatalogOffering.id from a hotel_availability_service
            get_availability() response (see parse_availability_offers).

    Returns:
        dict: raw OfferHospitalityResponse.

    Raises:
        HotelApiError: on any non-2xx response from Travelport.
    """
    payload = {
        "OfferQueryBuildFromCatalogOffering": {
            "@type": "OfferQueryBuildFromCatalogOffering",
            "BuildFromCatalogOfferingHospitality": {
                "@type": "BuildFromCatalogOfferingHospitality",
                "CatalogOfferingIdentifier": {"value": offer_id},
            },
        }
    }

    headers = get_hotel_headers()
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.post(HotelEndpoints.RULES_BUILD_FROM_OFFERING, json=payload, headers=headers)

    if response.status_code >= 400:
        logger.error(f"Hotel Rules failed: {response.status_code} — {response.text[:500]}")
        raise HotelApiError(
            f"Hotel rules failed (HTTP {response.status_code})",
            status_code=response.status_code,
            body=response.text,
        )

    return response.json()

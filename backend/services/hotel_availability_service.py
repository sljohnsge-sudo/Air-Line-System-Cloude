"""
services/hotel_availability_service.py
=========================================
Hotel Availability — https://developer.travelport.com/apis/stays/availability/createhotelavailability

Returns the actual bookable room types/rates for a chosen property — what
Search by Location cannot provide (see hotel_search_service module
docstring). Offers returned here are cached by Travelport for 30 minutes;
CatalogOffering.id is what create_hotel_reservation_from_offer() in
hotel_booking_service.py books against.

Kept fully independent of services/search_service.py (Air) — no shared code,
no shared endpoints, per instruction not to touch the flight side.
"""

import httpx
import logging
from typing import Optional
from config.hotel_config import HotelConfig, HotelEndpoints
from services.hotel_common import get_hotel_headers, build_room_stay_candidates, HotelApiError
from utils import tp_logger

logger = logging.getLogger(__name__)


def get_availability(
    chain_code: str,
    property_code: str,
    check_in_date: str,
    check_out_date: str,
    adults: int = 1,
    children_ages: Optional[list[int]] = None,
    rooms: int = 1,
    currency: Optional[str] = None,
) -> dict:
    """
    Fetch bookable room types/rates for one property on given stay dates.

    Args:
        chain_code / property_code: from a Search by Location result
            (property["chain_code"] / property["property_code"]).
        check_in_date / check_out_date: "YYYY-MM-DD"
        adults / children_ages / rooms: same semantics as hotel_search_service.search_hotels.
        currency: 3-letter ISO code; defaults to HotelConfig.DEFAULT_CURRENCY

    Returns:
        dict: raw CatalogOfferingsHospitalityResponse (see parse_availability_offers to simplify).

    Raises:
        HotelApiError: on any non-2xx response from Travelport.
    """
    payload = {
        "CatalogOfferingsQueryRequest": {
            "@type": "CatalogOfferingsRequestHospitality",
            "CatalogOfferingsRequest": [
                {
                    "@type": "CatalogOfferingsRequestHospitality",
                    "verboseResponseInd": True,
                    "requestedCurrency": currency or HotelConfig.DEFAULT_CURRENCY,
                    "StayDates": {"start": check_in_date, "end": check_out_date},
                    "HotelSearchCriterion": {
                        "@type": "HotelSearchCriterion",
                        "numberOfRooms": rooms,
                        "PropertyRequest": [
                            {
                                "@type": "PropertyRequest",
                                "PropertyKey": {
                                    "@type": "PropertyKey",
                                    "chainCode": chain_code,
                                    "propertyCode": property_code,
                                },
                            }
                        ],
                        "RoomStayCandidates": {
                            "@type": "RoomStayCandidates",
                            "RoomStayCandidate": build_room_stay_candidates(adults, children_ages, rooms),
                        },
                    },
                }
            ]
        }
    }

    headers = get_hotel_headers()
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.post(HotelEndpoints.AVAILABILITY, json=payload, headers=headers)

    if response.status_code >= 400:
        logger.error(f"Hotel Availability failed: {response.status_code} — {response.text[:500]}")
        raise HotelApiError(
            f"Hotel availability failed (HTTP {response.status_code})",
            status_code=response.status_code,
            body=response.text,
        )

    return response.json()


def parse_availability_offers(raw_response: dict) -> list[dict]:
    """
    Simplify a CatalogOfferingsHospitalityResponse into a flat list of
    bookable offers. Field paths per the fully-expanded response schema on
    https://developer.travelport.com/apis/stays/availability/createhotelavailability
    — CatalogOfferings.CatalogOffering[], each with a confirmed `id`
    (the offer key, cached 30 min — what booking uses) and a fully-confirmed
    Price breakdown (CurrencyCode/Base/TotalTaxes/TotalFees/TotalPrice).

    NOTE: ProductOptions[].Product[] (type "ProductID") is where Travelport's
    own docs say the room name/description lives, but the docs site's nested
    schema explorer could not be fully expanded for that one sub-object at
    verification time. room_description extraction below is best-effort,
    for display only — booking does NOT depend on it, it books by offer_id.
    """
    offers = []
    response = raw_response.get("CatalogOfferingsHospitalityResponse", raw_response)
    catalog_offerings = response.get("CatalogOfferings", {}) or {}

    for offering in catalog_offerings.get("CatalogOffering", []) or []:
        price = offering.get("Price", {}) or {}
        currency_code = (price.get("CurrencyCode", {}) or {}).get("value", HotelConfig.DEFAULT_CURRENCY)

        room_description = ""
        for option in offering.get("ProductOptions", []) or []:
            for product in option.get("Product", []) or []:
                if not isinstance(product, dict):
                    continue
                room_description = (
                    product.get("roomDescription")
                    or ((product.get("RoomType", {}) or {}).get("Description", {}) or {}).get("value", "")
                    or product.get("description", "")
                )
                if room_description:
                    break
            if room_description:
                break

        terms = offering.get("TermsAndConditions", {}) or {}

        offers.append({
            "offer_id": offering.get("id", ""),
            "room_description": room_description or "Room",
            "currency": currency_code,
            "base_price": price.get("Base", 0),
            "total_taxes": price.get("TotalTaxes", 0),
            "total_fees": price.get("TotalFees", 0),
            "total_price": price.get("TotalPrice", 0),
            "raw_terms": terms,
        })

    return offers

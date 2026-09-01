"""
services/hotel_search_service.py
==================================
STEP 1 — Hotel Search via Travelport SearchComplete (ODM v12).

SearchComplete combines what the v11 workflow needs three separate calls for
(Search, Details, Availability) into one request/response — see
https://developer.travelport.com/docs/stays/guides/stays-general-guide.
No separate Availability call is required before booking when using
SearchComplete.

Kept fully independent of services/search_service.py (Air) — no shared code,
no shared endpoints, per instruction not to touch the flight side.
"""

import httpx
import logging
from typing import Optional
from config.hotel_config import HotelConfig, HotelEndpoints
from services.hotel_common import get_hotel_headers, HotelApiError
from utils import tp_logger

logger = logging.getLogger(__name__)


def search_hotels(
    location_type: str,
    location_value: str,
    check_in_date: str,
    check_out_date: str,
    adults: int = 1,
    children_ages: Optional[list[int]] = None,
    rooms: int = 1,
    radius_km: int = 30,
    currency: Optional[str] = None,
) -> dict:
    """
    STEP 1 — Search hotels + room availability + rates in one call.

    Args:
        location_type: "cityIATACode" | "airportIATACode" | "coordinates" | "address"
        location_value: the IATA code (e.g. "DXB") for the code-based types
        check_in_date / check_out_date: "YYYY-MM-DD"
        adults: total adult guests across all rooms
        children_ages: list of child ages, one per child guest
        rooms: number of rooms requested
        radius_km: search radius around the location
        currency: 3-letter ISO code; defaults to HotelConfig.DEFAULT_CURRENCY

    Returns:
        dict: raw SearchComplete response (see parse_hotel_offers to simplify).

    Raises:
        HotelApiError: on any non-2xx response from Travelport.
    """
    guests: dict = {"adults": adults}
    if children_ages:
        guests["children"] = [{"age": age} for age in children_ages]

    payload = {
        "requestedCurrency": currency or HotelConfig.DEFAULT_CURRENCY,
        "stayDetails": {
            "checkInDateLocal": check_in_date,
            "checkOutDateLocal": check_out_date,
            "rooms": rooms,
            "guests": guests,
        },
        "propertyFilter": {
            "location": {
                "type": location_type,
                "details": {"iataCode": location_value} if location_type in ("cityIATACode", "airportIATACode") else {},
                "radius": {"value": radius_km, "unit": "km"},
            },
            "returnOnlyAvailableProperties": True,
            "imageSize": "Medium",
        },
    }

    headers = get_hotel_headers()
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.post(HotelEndpoints.SEARCH_COMPLETE, json=payload, headers=headers)

    if response.status_code >= 400:
        logger.error(f"Hotel SearchComplete failed: {response.status_code} — {response.text[:500]}")
        raise HotelApiError(
            f"Hotel search failed (HTTP {response.status_code})",
            status_code=response.status_code,
            body=response.text,
        )

    return response.json()


def get_property_details(chain_code: str, property_code: str, image_size: Optional[str] = None) -> dict:
    """
    Optional enrichment — additional property-level info (description,
    images, amenities, check-in/out policy) not returned by SearchComplete.
    Does not need to be preceded by a Search request.
    https://developer.travelport.com/apis/stays/search-and-details/getpropertiesdetail

    Args:
        chain_code: 2-5 char hotel chain code (e.g. "HL").
        property_code: property code within that chain (<=32 chars, typically 5).
        image_size: "Large" | "Medium" | "Small" | "Thumbnail" | "ExtraLarge"

    Returns:
        dict: raw PropertiesResponse from Travelport.

    Raises:
        HotelApiError: on any non-2xx response from Travelport.
    """
    params = {"chainCode": chain_code, "propertyCode": property_code}
    if image_size:
        params["ImageSize"] = image_size

    headers = get_hotel_headers()
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.get(HotelEndpoints.PROPERTY_DETAILS, params=params, headers=headers)

    if response.status_code >= 400:
        logger.error(f"Hotel Property Details failed: {response.status_code} — {response.text[:500]}")
        raise HotelApiError(
            f"Hotel property details failed (HTTP {response.status_code})",
            status_code=response.status_code,
            body=response.text,
        )

    return response.json()


def parse_property_details(raw_response: dict) -> dict:
    """
    Simplify a PropertiesResponse (from get_property_details) into a flat
    dict for the frontend. Field paths per
    https://support.travelport.com/webhelp/JSONAPIs/Hotelv11/.../APIRef_Details.htm
    """
    properties_response = raw_response.get("PropertiesResponse", raw_response)
    property_infos = (properties_response.get("Properties", {}) or {}).get("PropertyInfo", []) or []
    info = property_infos[0] if property_infos else {}
    prop = info.get("Property", {}) or {}
    address = prop.get("Address", {}) or {}
    geoloc = prop.get("GeoLocation", {}) or {}
    checkin_policy = prop.get("CheckInOutPolicy", {}) or {}

    return {
        "property_name": prop.get("name", ""),
        "chain_code": (prop.get("PropertyKey", {}) or {}).get("chainCode", ""),
        "property_code": (prop.get("PropertyKey", {}) or {}).get("propertyCode", ""),
        "ratings": prop.get("Rating", []) or [],
        "latitude": geoloc.get("latitude"),
        "longitude": geoloc.get("longitude"),
        "images": [
            {"url": img.get("value", ""), "caption": img.get("caption", ""), "category": img.get("pictureCategory")}
            for img in (prop.get("Image", []) or [])
        ],
        "descriptions": [d.get("value", "") for d in (prop.get("Description", []) or [])],
        "business_services": [s.get("description", s) if isinstance(s, dict) else s for s in (prop.get("BusinessService", []) or [])],
        "accessibility_features": prop.get("AccessibilityFeature", []) or [],
        "address": {
            "street": address.get("street", address.get("Street", "")),
            "city": address.get("city", address.get("City", "")),
            "state_province": address.get("stateProvince", address.get("StateProv", "")),
            "country_code": address.get("countryCode", address.get("Country", "")),
            "postal_code": address.get("postalCode", address.get("PostalCode", "")),
        },
        "telephones": prop.get("Telephone", []) or [],
        "email": prop.get("Email", {}) or {},
        "amenities": [
            {"description": a.get("description", ""), "code": a.get("code"), "category": a.get("category", "")}
            for a in (prop.get("PropertyAmenity", []) or [])
        ],
        "check_in_time": checkin_policy.get("checkInTime", ""),
        "check_out_time": checkin_policy.get("checkOutTime", ""),
        "minimum_age": checkin_policy.get("minimumAge"),
        "raw": raw_response,
    }


def parse_hotel_offers(raw_response: dict) -> list[dict]:
    """
    Parse a raw SearchComplete response into simplified property objects,
    each carrying its available room/rate offers, for the frontend.

    NOTE: field paths follow the documented SearchComplete response schema
    (https://developer.travelport.com/apis/stays — SearchComplete). This has
    not been exercised against a live response yet (Hotel/Stays product not
    yet provisioned on the account) — parsing is defensive (.get() chains)
    so a shape mismatch degrades gracefully instead of raising.
    """
    properties = []
    hotels_response = raw_response.get("hotelsResponse", {})
    check_in = hotels_response.get("checkInDateLocal", "")
    check_out = hotels_response.get("checkOutDateLocal", "")

    for item in hotels_response.get("propertyItems", []):
        property_info = item.get("propertyInfo", {}) or {}
        address = property_info.get("address", {}) or {}
        geoloc = (property_info.get("geolocation", {}) or {}).get("center", {}) or {}

        rooms = []
        for room in item.get("roomTypes", []) or []:
            for rate in room.get("rates", []) or []:
                price = rate.get("price", {}) or {}
                rate_code_info = rate.get("rateCodeInfo", {}) or {}
                terms = rate.get("terms", {}) or {}
                rooms.append({
                    "booking_code": rate.get("bookingCode", ""),
                    "room_description": rate.get("roomDescription") or room.get("shortRoomDescription", ""),
                    "rate_description": rate.get("rateDescription", ""),
                    "max_occupancy": room.get("maxOccupancy"),
                    "currency": price.get("currencyCode", "USD"),
                    "base_price": (price.get("base", {}) or {}).get("amount", 0),
                    "total_taxes": (price.get("totalTaxes", {}) or {}).get("amount", 0),
                    "total_price": (price.get("totalPrice", {}) or {}).get("amount", 0),
                    "refundable": terms.get("refundable", False),
                    "breakfast_included": rate.get("breakfastIncluded", False),
                    "wifi_included": rate.get("wifiIncluded", False),
                    "guarantee_type": terms.get("guaranteeType", ""),
                    "rate_code": rate_code_info.get("rateCode", ""),
                    "rate_type": rate_code_info.get("rateType", ""),
                    "cancel_penalties": terms.get("cancelPenalties", []),
                })

        lowest = item.get("lowestPublicAvailableRate") or item.get("lowestUnfilteredPublicAvailableRate") or {}
        properties.append({
            "property_name": item.get("name", ""),
            "chain_code": item.get("chainCode", ""),
            "property_code": item.get("propertyCode", ""),
            "property_type": item.get("estimatedPropertyType", "Hotel"),
            "check_in_date": check_in,
            "check_out_date": check_out,
            "address": {
                "street": address.get("street", ""),
                "city": address.get("city", ""),
                "state_province": address.get("stateProvince", ""),
                "country_code": address.get("countryCode", ""),
                "postal_code": address.get("postalCode", ""),
            },
            "latitude": geoloc.get("latitude"),
            "longitude": geoloc.get("longitude"),
            "distance": (property_info.get("distanceFromSearchPoint", {}) or {}).get("value"),
            "ratings": item.get("ratings") if isinstance(item.get("ratings"), list) else [],
            "amenities": [a.get("description", "") for a in (property_info.get("amenities", []) or [])],
            "image_urls": [img.get("url", "") for img in (property_info.get("imageURLs", []) or []) if img.get("url")],
            "lowest_price": (lowest.get("totalPrice", {}) or {}).get("amount"),
            "lowest_price_currency": lowest.get("currencyCode", "USD"),
            "rooms": rooms,
        })

    return properties

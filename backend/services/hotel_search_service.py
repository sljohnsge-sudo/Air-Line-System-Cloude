"""
services/hotel_search_service.py
==================================
Hotel Search via Travelport Stays Search and Details —
https://developer.travelport.com/apis/stays/search-and-details/searchbylocation

Search by Location returns property-level results (name, rating, geo,
images, lowest/maximum rate) only — it does NOT return selectable
room/rate offers. Pick a property, then call hotel_availability_service
(https://developer.travelport.com/apis/stays/availability) to get its
actual bookable rooms/rates.

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

# SearchBy discriminators this codebase wires up — the API also supports
# SearchByAddress/SearchByGeoLocation, but nothing upstream (the frontend's
# country→city picker) sends those yet.
_SEARCH_BY_BUILDERS = {
    "cityIATACode": lambda value: {"@type": "SearchByCity", "SearchCity": value},
    "airportIATACode": lambda value: {"@type": "SearchByAirport", "SearchAirport": value},
}


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
    Search by Location — property-level results only (see module docstring).

    Args:
        location_type: "cityIATACode" | "airportIATACode"
        location_value: the 3-letter IATA code (e.g. "DXB")
        check_in_date / check_out_date: "YYYY-MM-DD"
        adults: total adult guests across all rooms
        children_ages: list of child ages, one per child guest
        rooms: number of rooms requested
        radius_km: search radius around the resolved location
        currency: 3-letter ISO code; defaults to HotelConfig.DEFAULT_CURRENCY

    Returns:
        dict: raw PropertiesResponse (see parse_hotel_offers to simplify).

    Raises:
        HotelApiError: on any non-2xx response from Travelport, or an
            unsupported location_type.
    """
    build_search_by = _SEARCH_BY_BUILDERS.get(location_type)
    if build_search_by is None:
        raise HotelApiError(f"Unsupported location_type '{location_type}' — only cityIATACode/airportIATACode are wired up")

    search_by = build_search_by(location_value)
    search_by["SearchRadius"] = {"value": radius_km, "unitOfDistance": "Kilometers"}

    payload = {
        "PropertiesQuerySearch": {
            "@type": "PropertiesQuerySearch",
            "CheckInDate": check_in_date,
            "CheckOutDate": check_out_date,
            "RequestedCurrency": currency or HotelConfig.DEFAULT_CURRENCY,
            "RoomStayCandidate": build_room_stay_candidates(adults, children_ages, rooms),
            "SearchBy": search_by,
            "returnOnlyAvailablePropertiesInd": True,
        }
    }

    headers = get_hotel_headers()
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.post(HotelEndpoints.SEARCH_BY_LOCATION, json=payload, headers=headers)

    if response.status_code >= 400:
        logger.error(f"Hotel Search by Location failed: {response.status_code} — {response.text[:500]}")
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


def parse_hotel_offers(raw_response: dict, check_in_date: str = "", check_out_date: str = "") -> list[dict]:
    """
    Parse a raw Search by Location PropertiesResponse into simplified
    property objects for the frontend. Field paths per
    https://developer.travelport.com/apis/stays/search-and-details/searchbylocation
    response schema (PropertiesResponse.Properties.PropertyInfo[]).

    check_in_date/check_out_date are echoed from the original search request
    onto each property — the response itself carries no stay dates (not part
    of the documented schema, unlike the old SearchComplete response).

    NOTE: this response has no per-room rate data and no Address field —
    both confirmed absent from the documented schema. "rooms" is always []
    here; wiring up the Availability endpoint
    (https://developer.travelport.com/apis/stays/availability) for a chosen
    property is what fills it in. Parsing is defensive (.get() chains) so a
    shape mismatch degrades gracefully instead of raising.
    """
    properties = []
    properties_response = raw_response.get("PropertiesResponse", raw_response)
    property_infos = (properties_response.get("Properties", {}) or {}).get("PropertyInfo", []) or []

    for info in property_infos:
        prop = info.get("Property", {}) or {}
        property_key = prop.get("PropertyKey", {}) or {}
        geoloc = prop.get("GeoLocation", {}) or {}
        lowest = info.get("LowestAvailableRate", {}) or {}

        properties.append({
            "property_name": prop.get("name", ""),
            "chain_code": property_key.get("chainCode", ""),
            "property_code": property_key.get("propertyCode", ""),
            "availability": info.get("availability", ""),
            "featured": info.get("featuredPropertyInd", False),
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
            "address": {},  # not part of this endpoint's response — see Property Details
            "latitude": geoloc.get("latitude"),
            "longitude": geoloc.get("longitude"),
            "distance": (info.get("Distance", {}) or {}).get("value"),
            "ratings": prop.get("Rating", []) or [],
            "image_urls": [img.get("value", "") for img in (prop.get("Image", []) or []) if img.get("value")],
            "lowest_price": lowest.get("value"),
            "lowest_price_currency": lowest.get("code", "USD"),
            "rooms": [],
        })

    return properties

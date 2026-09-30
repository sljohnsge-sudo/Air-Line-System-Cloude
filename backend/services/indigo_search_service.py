"""
services/indigo_search_service.py
===================================
IndiGo (6E) flight search + fare/ancillary pricing + seat map, via
Travelport's legacy Universal API (uAPI), provider code ACH.

Built strictly to the request/response shapes in the reference logs under
"Indigo all ancillary service" (LFS_Req/Res.xml, APR_WOOS/WTOS_Req/Res.xml,
SeatMapReq/Res.xml) — also available converted to this project's own JSON
convention at backend/reference/indigo_uapi_json/. UNTESTED end-to-end —
TP_UAPI_* credentials are not yet configured (see config/indigo_config.py),
though the transport layer itself (SOAP envelope, endpoint, auth mechanism)
was confirmed reachable live — see indigo_uapi_client.py's docstring.

This module is entirely separate from search_service.py (GDS/NDC, JSON
TripServices v11) — no GDS/NDC code path calls into this file, and nothing
here is imported by it.

State between calls (Search -> Price -> SeatMap -> Book) is uAPI-native: the
caller must keep passing back the exact AirSegment + HostToken elements (and,
for ancillaries, the exact OptionalService elements) it was given, rather
than a server-side session id — there is no "workbench" concept in this
legacy API. This module returns those as opaque base64 XML blobs the
frontend/caller round-trips unmodified; see `segments_token` and
`ancillary_token` below. Everything else returned to the caller is plain
JSON-shaped dicts (FastAPI serializes these as this system's normal JSON
responses) — the XML is fully contained to the Travelport-facing boundary.
"""

import base64
import logging
import xml.etree.ElementTree as ET
from config.indigo_config import IndigoConfig, IndigoEndpoints
from services.indigo_uapi_client import (
    NS_AIR, NS_COMMON, air_tag, common_tag, post_xml, find_all, find_one, IndigoApiError,
)

logger = logging.getLogger(__name__)


# ── Opaque element round-tripping ───────────────────────────────────────────
# The legacy uAPI has no server-side session for search results (only the
# HostToken, which just anchors this particular fare cache and expires like
# any other GDS fare cache) — every subsequent call must resend the exact
# AirSegment/HostToken/OptionalService XML Travelport handed back. Rather
# than hand-reconstruct those elements from a simplified dict (error-prone —
# see e.g. the ~15 attributes on a single OptionalService), we base64-encode
# the raw element(s) and hand that blob back to the caller to round-trip.
# Everything ELSE in this module's return values is a plain JSON dict.

def _encode_elements(elements: list[ET.Element]) -> str:
    wrapper = ET.Element("Bundle")
    for el in elements:
        wrapper.append(el)
    return base64.urlsafe_b64encode(ET.tostring(wrapper, encoding="utf-8")).decode("ascii")


def _decode_elements(token: str) -> list[ET.Element]:
    xml_bytes = base64.urlsafe_b64decode(token.encode("ascii"))
    wrapper = ET.fromstring(xml_bytes)
    return list(wrapper)


def _sub(parent: ET.Element, tag: str, attrib: dict | None = None, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: v for k, v in (attrib or {}).items() if v is not None})
    if text is not None:
        el.text = text
    return el


# ── STEP 1: Search (LowFareSearchReq) ───────────────────────────────────────

def search_flights(origin: str, destination: str, departure_date: str, passenger_type: str = "ADT") -> dict:
    """
    Search IndiGo flights for one leg (LFS_Req.xml shape — one SearchAirLeg,
    one SearchPassenger). Round-trip/multi-city would repeat SearchAirLeg per
    reference docs, but only a single-leg example was provided, so this is
    scoped to one-way for now.

    Returns:
        dict: {"flights": [ { flight fields..., "segments_token": "<opaque>" } ]}
    """
    root = ET.Element(air_tag("LowFareSearchReq"), {
        "AuthorizedBy": "user",
        "SolutionResult": "true",
        "TargetBranch": IndigoConfig.TARGET_BRANCH,
        "TraceId": IndigoConfig.generate_trace_id(),
        "xmlns:air": NS_AIR,
        "xmlns:common": NS_COMMON,
    })
    _sub(root, common_tag("BillingPointOfSaleInfo"), {"OriginApplication": "UAPI"})
    leg = _sub(root, air_tag("SearchAirLeg"))
    _sub(_sub(leg, air_tag("SearchOrigin")), common_tag("CityOrAirport"), {"Code": origin.upper()})
    _sub(_sub(leg, air_tag("SearchDestination")), common_tag("CityOrAirport"), {"Code": destination.upper()})
    _sub(leg, air_tag("SearchDepTime"), {"PreferredTime": departure_date})
    modifiers = _sub(root, air_tag("AirSearchModifiers"))
    _sub(_sub(modifiers, air_tag("PreferredProviders")), common_tag("Provider"), {"Code": IndigoConfig.PROVIDER_CODE})
    _sub(root, common_tag("SearchPassenger"), {"Code": passenger_type})

    response = post_xml(IndigoEndpoints.air_service(), root)
    return _parse_search_response(response)


def _parse_search_response(response: ET.Element) -> dict:
    flight_details = {el.get("id"): el for el in find_all(response, "air:FlightDetailsList/air:FlightDetails")}
    air_segments = {el.get("Key"): el for el in find_all(response, "air:AirSegmentList/air:AirSegment")}
    fare_infos = {el.get("Key"): el for el in find_all(response, "air:FareInfoList/air:FareInfo")}
    host_tokens = {el.get("Key"): el for el in find_all(response, "air:HostTokenList/common:HostToken")}

    flights = []
    for solution in find_all(response, "air:AirPricingSolution"):
        seg_refs = [ref.get("Key") for ref in find_all(solution, "air:Journey/air:AirSegmentRef")]
        segs = [air_segments[k] for k in seg_refs if k in air_segments]
        if not segs:
            continue

        pricing_info = find_one(solution, "air:AirPricingInfo")
        booking_info = find_one(pricing_info, "air:BookingInfo") if pricing_info is not None else None
        fare_ref = booking_info.get("FareInfoRef") if booking_info is not None else None
        fare_info = fare_infos.get(fare_ref)
        host_token_key = booking_info.get("HostTokenRef") if booking_info is not None else None
        host_token_el = host_tokens.get(host_token_key)

        segments_out = []
        for seg in segs:
            fd_ref = find_one(seg, "air:FlightDetailsRef")
            fd = flight_details.get(fd_ref.get("Key")) if fd_ref is not None else None
            dep = find_one(fd, "air:Departure") if fd is not None else None
            arr = find_one(fd, "air:Arrival") if fd is not None else None
            segments_out.append({
                "carrier": seg.get("Carrier"),
                "flight_number": seg.get("FlightNumber"),
                "equipment": seg.get("Equipment"),
                "departure_airport": dep.get("location") if dep is not None else seg.get("Origin"),
                "arrival_airport": arr.get("location") if arr is not None else seg.get("Destination"),
                "departure_time": f"{dep.get('date')}T{dep.get('time')}" if dep is not None else seg.get("DepartureTime"),
                "arrival_time": f"{arr.get('date')}T{arr.get('time')}" if arr is not None else seg.get("ArrivalTime"),
                "arrival_terminal": arr.get("terminal") if arr is not None else None,
                "raw_segment": seg,
            })

        round_trip_elements = [s["raw_segment"] for s in segments_out]
        if host_token_el is not None:
            round_trip_elements.append(host_token_el)

        currency = None
        base_price = None
        if fare_info is not None:
            amount = fare_info.get("Amount") or ""
            currency = amount[:3] if len(amount) > 3 else None
            base_price = amount[3:] if len(amount) > 3 else None

        total_price_attr = solution.get("TotalPrice") or ""
        flights.append({
            "cabin_class": booking_info.get("CabinClass") if booking_info is not None else None,
            "booking_code": booking_info.get("BookingCode") if booking_info is not None else None,
            "fare_basis": fare_info.get("FareBasis") if fare_info is not None else None,
            "currency": currency or (total_price_attr[:3] if len(total_price_attr) > 3 else "INR"),
            "base_price": float(base_price) if base_price else None,
            "total_price": float(total_price_attr[3:]) if len(total_price_attr) > 3 else None,
            "taxes": solution.get("Taxes"),
            "segments": [
                {k: v for k, v in s.items() if k != "raw_segment"} for s in segments_out
            ],
            # Opaque blob the caller must send back unmodified to Price/SeatMap —
            # contains the AirSegment(s) + HostToken exactly as Travelport sent them.
            "segments_token": _encode_elements(round_trip_elements),
        })

    return {"flights": flights}


# ── Shared: re-key AirSegment/HostToken elements for a follow-up request ────
# Reference logs show the price/seatmap/booking requests re-key search's
# random Keys down to simple local ones (Seg1, h0R1, ...) — the segment's
# real identity travels in its carrier/flightNumber/origin/destination/time
# attributes, not the Key string, so this is safe as long as AirSegment.Key
# and HostToken.Key/AirSegment.HostTokenRef stay internally consistent
# within THIS request.
def _rekey_for_request(elements: list[ET.Element]) -> tuple[list[ET.Element], dict[str, str]]:
    """Returns (re-keyed element copies, {old_host_token_key: new_local_key})."""
    host_key_map: dict[str, str] = {}
    out = []
    host_i = 0
    for el in elements:
        if el.tag == common_tag("HostToken"):
            old_key = el.get("Key")
            new_key = f"h{host_i}R1"
            host_i += 1
            if old_key:
                host_key_map[old_key] = new_key

    seg_i = 0
    for el in elements:
        copy = ET.fromstring(ET.tostring(el))
        if copy.tag == air_tag("AirSegment"):
            seg_i += 1
            copy.set("Key", f"Seg{seg_i}")
            old_href = copy.get("HostTokenRef")
            if old_href and old_href in host_key_map:
                copy.set("HostTokenRef", host_key_map[old_href])
            out.append(copy)
        elif copy.tag == common_tag("HostToken"):
            old_key = el.get("Key")
            if old_key in host_key_map:
                copy.set("Key", host_key_map[old_key])
            out.append(copy)
        else:
            out.append(copy)
    return out, host_key_map


def _segment_keys(elements: list[ET.Element]) -> list[str]:
    return [el.get("Key") for el in elements if el.tag == air_tag("AirSegment")]


# ── STEP 2: Price (AirPriceReq) — with or without ancillary discovery ──────

def _build_price_request(
    segments_token: str,
    fare_basis: str,
    passenger_type: str,
    ancillary_elements: list[ET.Element] | None = None,
) -> ET.Element:
    elements = _decode_elements(segments_token)
    reqd_elements, _ = _rekey_for_request(elements)
    seg_keys = _segment_keys(reqd_elements)

    root = ET.Element(air_tag("AirPriceReq"), {
        "AuthorizedBy": "user",
        "CheckOBFees": "All",
        "TargetBranch": IndigoConfig.TARGET_BRANCH,
        "TraceId": IndigoConfig.generate_trace_id(),
        "xmlns:air": NS_AIR,
        "xmlns:common": NS_COMMON,
    })
    _sub(root, common_tag("BillingPointOfSaleInfo"), {"OriginApplication": ""})
    itinerary = _sub(root, air_tag("AirItinerary"))
    for el in reqd_elements:
        itinerary.append(el)
    _sub(root, common_tag("SearchPassenger"), {"Code": passenger_type, "BookingTravelerRef": "PAX1"})
    pricing_cmd = _sub(root, air_tag("AirPricingCommand"))
    for seg_key in seg_keys:
        _sub(pricing_cmd, air_tag("AirSegmentPricingModifiers"), {"AirSegmentRef": seg_key, "FareBasisCode": fare_basis})

    if ancillary_elements:
        optional_services = _sub(root, air_tag("OptionalServices"))
        for el in ancillary_elements:
            optional_services.append(ET.fromstring(ET.tostring(el)))

    # Reference logs send a placeholder Visa card here purely so Travelport's
    # CheckOBFees="All" can compute card-network booking fees at quote time —
    # this is NOT the customer's real card (per this app's PayCorp + agency
    # card payment model, decided for the actual booking/payment step).
    fop = _sub(root, common_tag("FormOfPayment"), {"Type": "Credit"})
    _sub(fop, common_tag("CreditCard"), {"Type": "VI"})

    return root


def price_offer(segments_token: str, fare_basis: str, passenger_type: str = "ADT") -> dict:
    """
    STEP 2a — Price without pre-selected ancillaries (APR_WOOS reference).
    The response's <air:OptionalServices> block is the full catalog of
    available ancillaries (meals, baggage, seats) for this itinerary.

    Returns:
        dict: {total_price, currency, base_price, taxes, ancillaries: [...],
               segments_token (unchanged, pass through to next call)}
    """
    root = _build_price_request(segments_token, fare_basis, passenger_type)
    response = post_xml(IndigoEndpoints.air_service(), root)
    return _parse_price_response(response, segments_token)


def price_offer_with_ancillaries(
    segments_token: str, fare_basis: str, passenger_type: str, ancillary_tokens: list[str],
) -> dict:
    """
    STEP 2b — Re-price with specific ancillaries selected (APR_WTOS
    reference). `ancillary_tokens` are the opaque `token` values from the
    `ancillaries` list returned by price_offer() for the ones the traveler
    picked — echoed back verbatim, as Travelport's reference does.
    """
    ancillary_elements = [el for token in ancillary_tokens for el in _decode_elements(token)]
    root = _build_price_request(segments_token, fare_basis, passenger_type, ancillary_elements)
    response = post_xml(IndigoEndpoints.air_service(), root)
    return _parse_price_response(response, segments_token)


def _parse_price_response(response: ET.Element, segments_token: str) -> dict:
    solution = find_one(response, "air:AirPriceResult/air:AirPricingSolution")
    if solution is None:
        raise IndigoApiError("AirPrice response had no AirPricingSolution", body=ET.tostring(response, encoding="unicode")[:2000])

    ancillaries = []
    for service in find_all(solution, "air:OptionalServices/air:OptionalService"):
        display = service.get("DisplayText") or service.get("Type")
        total_price = service.get("TotalPrice") or ""
        ancillaries.append({
            "key": service.get("Key"),
            "type": service.get("Type"),
            "display_text": display,
            "provider_defined_type": service.get("ProviderDefinedType"),
            "status": service.get("ServiceStatus"),
            "currency": total_price[:3] if len(total_price) > 3 else None,
            "price": float(total_price[3:]) if len(total_price) > 3 else 0.0,
            "quantity": service.get("Quantity"),
            "description": next(
                (d.text for d in find_all(service, "common:ServiceInfo/common:Description")), None
            ),
            # Opaque blob to echo back verbatim in price_offer_with_ancillaries()
            # if the traveler selects this ancillary.
            "token": _encode_elements([service]),
        })

    return {
        "total_price": float(solution.get("TotalPrice", "")[3:]) if len(solution.get("TotalPrice", "")) > 3 else None,
        "base_price": float(solution.get("BasePrice", "")[3:]) if len(solution.get("BasePrice", "")) > 3 else None,
        "currency": (solution.get("TotalPrice") or "")[:3] or None,
        "taxes": solution.get("Taxes"),
        "fees": solution.get("Fees"),
        "ancillaries": ancillaries,
        "segments_token": segments_token,
        # Opaque blob of the ENTIRE raw <air:AirPricingSolution> (segments,
        # AirPricingInfo/FareInfo/FareRuleKey, selected OptionalServices) —
        # ACR_Req.xml reproduces this whole structure near-verbatim inside
        # the reservation request, so create_reservation() consumes this
        # directly rather than us hand-rebuilding FareRuleKey etc.
        "pricing_token": _encode_elements([solution]),
    }


# ── Seat map (SeatMapReq) ───────────────────────────────────────────────────

def get_seat_map(segments_token: str, first_name: str, last_name: str) -> dict:
    """
    STEP — SeatMapReq reference. Needs a traveler name even before travelers
    are formally added (matches the reference's placeholder "ALPHA TEST").
    """
    elements = _decode_elements(segments_token)
    reqd_elements, _ = _rekey_for_request(elements)

    root = ET.Element(air_tag("SeatMapReq"), {
        "AuthorizedBy": "user",
        "ReturnBrandingInfo": "true",
        "RetrieveProviderReservationDetails": "true",
        "ReturnSeatPricing": "true",
        "TargetBranch": IndigoConfig.TARGET_BRANCH,
        "TraceId": IndigoConfig.generate_trace_id(),
        "xmlns:air": NS_AIR,
        "xmlns:common": NS_COMMON,
    })
    _sub(root, common_tag("BillingPointOfSaleInfo"), {"OriginApplication": "uAPI"})
    for el in reqd_elements:
        root.append(el)
    traveler = _sub(root, air_tag("SearchTraveler"), {"Code": "ADT", "Key": "0"})
    _sub(traveler, common_tag("Name"), {"First": first_name.upper(), "Last": last_name.upper()})

    response = post_xml(IndigoEndpoints.air_service(), root)
    return _parse_seatmap_response(response)


def _parse_seatmap_response(response: ET.Element) -> dict:
    seat_services = {el.get("Key"): el for el in find_all(response, "air:OptionalServices/air:OptionalService")}

    rows = []
    for row in find_all(response, "air:Rows/air:Row"):
        facilities = []
        for facility in find_all(row, "air:Facility"):
            service_ref = facility.get("OptionalServiceRef")
            service = seat_services.get(service_ref)
            price_attr = (service.get("TotalPrice") if service is not None else None) or ""
            facilities.append({
                "type": facility.get("Type"),
                "seat_code": facility.get("SeatCode"),
                "availability": facility.get("Availability"),
                "paid": facility.get("Paid") == "true",
                "characteristics": [c.get("Value") for c in find_all(facility, "air:Characteristic")],
                "price": float(price_attr[3:]) if len(price_attr) > 3 else 0.0,
                "currency": price_attr[:3] if len(price_attr) > 3 else None,
                # Echo back verbatim as one of the ancillary_tokens to
                # price_offer_with_ancillaries() if this seat is selected.
                "token": _encode_elements([service]) if service is not None else None,
            })
        rows.append({"number": row.get("Number"), "seats": facilities})

    return {"rows": rows}

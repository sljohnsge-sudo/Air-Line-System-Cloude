"""
services/indigo_booking_service.py
====================================
STEP 3 — Create an IndiGo (6E) reservation via Travelport's legacy Universal
API (AirCreateReservationReq, UniversalRecordService), provider code ACH.

Built strictly to ACR_Req.xml / ACR_Res.xml under "Indigo all ancillary
service" (also available as JSON at backend/reference/indigo_uapi_json/).
UNTESTED end-to-end — see indigo_search_service.py's module docstring for
why, and config/indigo_config.py for the credentials this is blocked on.

Payment: per ACR_Req.xml, Travelport's Create Reservation itself requires a
real FormOfPayment/CreditCard to ticket — same pattern as this app's hotel
integration (services/hotel_booking_service.py): the customer is charged the
total via PayCorp separately, and the card sent here is the AGENCY's own
guarantee card (config/indigo_config.py's GUARANTEE_CARD_* fields), never a
customer-entered card.

Scope note: search/price currently support only ONE SearchPassenger type at
a time (matches the single `<common:SearchPassenger Code="ADT"/>` example in
LFS_Req.xml) — this function accepts multiple travelers of that SAME
passenger type. Mixed types in one booking (e.g. ADT+CHD+INF together, as
ACR_Req.xml itself shows) would need indigo_search_service.search_flights()
extended to send multiple PassengerCriteria first; flagged as a follow-up,
not implemented here to avoid guessing at an untested multi-type flow.
"""

import logging
import xml.etree.ElementTree as ET
from config.indigo_config import IndigoConfig, IndigoEndpoints
from config.travelport_config import TravelportConfig  # read-only: agency identity only, shared across products (see hotel_config.py's same pattern)
from services.indigo_uapi_client import NS_AIR, NS_COMMON, NS_UNIV, air_tag, common_tag, univ_tag, post_xml, find_all, find_one, IndigoApiError
from services.indigo_search_service import _decode_elements, _sub  # noqa: F401 (intentional reuse of the same echo-verbatim + element-builder helpers)

logger = logging.getLogger(__name__)


def _docs_ssr_text(traveler: dict) -> str:
    """P/{issueCountry}/{docNumber}/{nationality}/{DOB}/{gender}/{expiry}/{surname}/{given} — ACR_Req.xml's DOCS SSR format."""
    def ddmmmyy(iso_date: str) -> str:
        from datetime import date
        y, m, d = (int(p) for p in iso_date.split("-"))
        return date(y, m, d).strftime("%d%b%y").upper()

    gender_code = "M" if traveler["gender"].lower().startswith("m") else "F"
    return (
        f"P/{traveler['passport_issue_country']}/{traveler['passport_number']}/"
        f"{traveler['nationality']}/{ddmmmyy(traveler['date_of_birth'])}/{gender_code}/"
        f"{ddmmmyy(traveler['passport_expiry'])}/{traveler['last_name'].upper()}/{traveler['first_name'].upper()}"
    )


def _build_booking_traveler(traveler: dict, key: str) -> ET.Element:
    el = ET.Element(common_tag("BookingTraveler"), {
        "DOB": traveler["date_of_birth"],
        "Key": key,
        "Nationality": traveler["nationality"],
        "TravelerType": traveler["passenger_type_code"],
        "Gender": "Male" if traveler["gender"].lower().startswith("m") else "Female",
    })
    _sub(el, common_tag("BookingTravelerName"), {
        "First": traveler["first_name"].upper(),
        "Last": traveler["last_name"].upper(),
        "Prefix": traveler.get("prefix", "MR" if traveler["gender"].lower().startswith("m") else "MS"),
    })
    if traveler.get("phone_number"):
        _sub(el, common_tag("PhoneNumber"), {
            "Number": traveler["phone_number"],
            "Type": "Mobile",
            "Text": "Traveler contact",
        })
    if traveler.get("email"):
        _sub(el, common_tag("Email"), {"EmailID": traveler["email"], "Type": "P"})
    _sub(el, common_tag("SSR"), {
        "Type": "DOCS", "Carrier": IndigoConfig.CARRIER_CODE, "Status": "HK",
        "FreeText": _docs_ssr_text(traveler),
    })
    return el


def _remap_traveler_refs(solution: ET.Element, traveler_keys: list[str]) -> None:
    """
    The priced solution's PassengerType/ServiceData carry Travelport's own
    internal placeholder BookingTravelerRef (assigned before we'd named any
    travelers) — ACR_Req.xml replaces these with OUR chosen PAX1/PAX2/...
    keys. All travelers here share one passenger type (see module docstring
    scope note), so every distinct placeholder ref found is mapped to our
    traveler keys in encounter order.
    """
    placeholder_refs: list[str] = []
    for el in solution.iter():
        ref = el.get("BookingTravelerRef")
        if ref and ref not in placeholder_refs:
            placeholder_refs.append(ref)

    ref_map = dict(zip(placeholder_refs, traveler_keys))
    for el in solution.iter():
        ref = el.get("BookingTravelerRef")
        if ref in ref_map:
            el.set("BookingTravelerRef", ref_map[ref])


def create_reservation(pricing_token: str, travelers: list[dict], selected_ancillary_tokens: list[str] | None = None) -> dict:
    """
    Args:
        pricing_token: from indigo_search_service.price_offer[_with_ancillaries]().
        travelers: list of dicts — first_name, last_name, prefix, gender,
            date_of_birth (YYYY-MM-DD), passenger_type_code, nationality,
            passport_number, passport_issue_country, passport_expiry,
            phone_number, email. All must share one passenger_type_code —
            see module docstring.
        selected_ancillary_tokens: the same tokens already priced in via
            price_offer_with_ancillaries() — NOT re-selected here; passed
            only so this function can sanity-check they're already present
            in the priced solution embedded in pricing_token.

    Returns:
        dict: {locator_code, status} on success.
    """
    if not IndigoConfig.guarantee_card_configured():
        raise IndigoApiError(
            "Agency guarantee card is not configured — "
            "TP_UAPI_GUARANTEE_CARD_* is empty in .env. IndiGo's Create Reservation "
            "requires a real FormOfPayment/CreditCard to ticket (see ACR_Req.xml)."
        )

    solutions = _decode_elements(pricing_token)
    if not solutions or solutions[0].tag != air_tag("AirPricingSolution"):
        raise IndigoApiError("pricing_token did not decode to an AirPricingSolution")
    solution = solutions[0]
    solution.set("Key", "Sol1")

    traveler_keys = [f"PAX{i}" for i in range(1, len(travelers) + 1)]
    _remap_traveler_refs(solution, traveler_keys)

    root = ET.Element(univ_tag("AirCreateReservationReq"), {
        "AuthorizedBy": "user",
        "RetainReservation": "None",
        "TargetBranch": IndigoConfig.TARGET_BRANCH,
        "xmlns:univ": NS_UNIV,
        "xmlns:air": NS_AIR,
        "xmlns:common": NS_COMMON,
    })
    _sub(root, common_tag("BillingPointOfSaleInfo"), {"OriginApplication": "UAPI"})

    for traveler, key in zip(travelers, traveler_keys):
        root.append(_build_booking_traveler(traveler, key))

    agency = _sub(root, common_tag("AgencyContactInfo"))
    _sub(agency, common_tag("PhoneNumber"), {
        "Type": "Agency",
        "CountryCode": TravelportConfig.AGENCY_PHONE_COUNTRY_CODE,
        "AreaCode": TravelportConfig.AGENCY_PHONE_AREA_CODE,
        "Number": TravelportConfig.AGENCY_PHONE_NUMBER,
        "Text": f"{TravelportConfig.AGENCY_NAME} contact",
    })
    _sub(root, common_tag("EmailNotification"), {"Recipients": "All"})

    fop = _sub(root, common_tag("FormOfPayment"), {"Type": "Credit"})
    card = _sub(fop, common_tag("CreditCard"), {
        "BankCountryCode": IndigoConfig.GUARANTEE_CARD_BILLING_COUNTRY,
        "CVV": IndigoConfig.GUARANTEE_CARD_CVV,
        "ExpDate": IndigoConfig.GUARANTEE_CARD_EXPIRY,
        "Name": IndigoConfig.GUARANTEE_CARD_HOLDER_NAME,
        "Number": IndigoConfig.GUARANTEE_CARD_NUMBER,
        "Type": IndigoConfig.GUARANTEE_CARD_TYPE,
    })
    billing = _sub(card, common_tag("BillingAddress"))
    _sub(billing, common_tag("AddressName"), text=TravelportConfig.AGENCY_NAME)
    _sub(billing, common_tag("Street"), text=IndigoConfig.GUARANTEE_CARD_BILLING_ADDRESS)
    _sub(billing, common_tag("City"), text=IndigoConfig.GUARANTEE_CARD_BILLING_CITY)
    if IndigoConfig.GUARANTEE_CARD_BILLING_STATE:
        _sub(billing, common_tag("State"), text=IndigoConfig.GUARANTEE_CARD_BILLING_STATE)
    _sub(billing, common_tag("PostalCode"), text=IndigoConfig.GUARANTEE_CARD_BILLING_POSTAL)
    _sub(billing, common_tag("Country"), text=IndigoConfig.GUARANTEE_CARD_BILLING_COUNTRY)

    root.append(solution)
    _sub(root, common_tag("ActionStatus"), {"Type": "ACTIVE", "ProviderCode": IndigoConfig.PROVIDER_CODE, "TicketDate": "T*"})

    response = post_xml(IndigoEndpoints.universal_record_service(), root)
    return _parse_create_reservation_response(response)


def _parse_create_reservation_response(response: ET.Element) -> dict:
    """
    Per ACR_Res.xml (reference/indigo_uapi_json/ACR_Res.json): response-level
    messages are plain <common:ResponseMessage Type="Warning|Error">, and
    there are THREE distinct locator codes — UniversalRecord's own
    (Travelport-internal), ProviderReservationInfo's (the actual 6E/ACH host
    locator == the airline PNR), and AirReservation's (a third, air-specific
    one). ProviderReservationInfo's is what's useful to show the traveler as
    their booking reference.
    """
    messages = [
        {"code": m.get("Code"), "type": m.get("Type"), "text": m.text}
        for m in find_all(response, "common:ResponseMessage")
    ]
    for m in messages:
        if (m["type"] or "").lower() == "error":
            raise IndigoApiError(f"IndiGo reservation failed ({m['code']}): {m['text']}")

    universal_record = find_one(response, "univ:UniversalRecord")
    provider_reservation = find_one(response, "univ:UniversalRecord/univ:ProviderReservationInfo")
    air_reservation = find_one(response, "univ:UniversalRecord/air:AirReservation")

    return {
        "universal_record_locator": universal_record.get("LocatorCode") if universal_record is not None else None,
        "status": universal_record.get("Status") if universal_record is not None else None,
        # This is the one to show the traveler — the actual airline/host locator.
        "locator_code": provider_reservation.get("LocatorCode") if provider_reservation is not None else None,
        "air_reservation_locator": air_reservation.get("LocatorCode") if air_reservation is not None else None,
        "messages": messages,
    }

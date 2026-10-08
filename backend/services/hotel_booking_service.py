"""
services/hotel_booking_service.py
===================================
Create, Retrieve, and Cancel a hotel reservation via Travelport TripServices
Stays v11 — https://developer.travelport.com/apis/stays/unified-check-out.

Two ways to create a reservation, both confirmed against Travelport's own
documented example payloads:
  - create_hotel_reservation() — Full Payload (POST book/reservations):
    sends complete stay/rate details directly. Same reasoning as the
    existing Air integration's full-payload choice for GDS bookings (see
    api_endpoints.py add_offer_to_workbench_full_payload): usable without
    first calling Availability, if a bookingCode is already known.
  - create_hotel_reservation_from_offer() — Reference Payload (POST
    book/reservations/build): books against a CatalogOffering.id cached
    from a hotel_availability_service.get_availability() call. This is the
    path the Search -> Availability -> book flow uses, and avoids ever
    having to parse Availability's not-fully-documented Product[] shape for
    booking purposes (display-only there — see hotel_availability_service).

Payment model — DIFFERENT from the Air flow:
  Flights: PayCorp charges the customer; Travelport never sees a card number.
  Hotels: Travelport's Create Reservation itself requires a
    FormOfPayment.PaymentCard block to guarantee/prepay the room with the
    supplier. The customer should still be charged via PayCorp for the
    total (same as flights, card never touches this backend) — the card
    sent to Travelport here is the AGENCY's own guarantee/virtual card,
    configured in .env by the agency (see config/hotel_config.py). This
    module never accepts a customer-entered card number as input.

NOTE: Not yet verified against a live response — the sandbox account is not
provisioned for Hotel/Stays (confirmed via live 403s at the Akamai edge).
Written strictly to the documented contract at
https://developer.travelport.com/apis/stays. Response parsing is defensive
so a schema mismatch degrades gracefully once access is granted.
"""

import httpx
import logging
from typing import Optional
from config.hotel_config import HotelConfig, HotelEndpoints
from services.hotel_common import get_hotel_headers, HotelApiError
from utils import tp_logger

logger = logging.getLogger(__name__)


def _build_payment_card() -> dict:
    """The agency's guarantee/virtual card as a PaymentCardDetail object,
    matching Travelport's documented shape field-for-field — confirmed from
    TWO separate example payloads (createhotelreservation and
    buildhotelreservation), both showing the same richer shape than this
    module previously sent: PersonName/Telephone/Email on the card itself,
    and an Address with Number/Street/County/StateProv alongside
    AddressLine/City/Country/PostalCode. The extra fields are only included
    when configured — guarantee_card_configured() already gates booking on
    the fields that are actually required (NUMBER/EXPIRE/CODE).
    """
    holder_given, _, holder_surname = HotelConfig.GUARANTEE_CARD_HOLDER_NAME.partition(" ")

    card: dict = {
        "@type": "PaymentCardDetail",
        "expireDate": HotelConfig.GUARANTEE_CARD_EXPIRE,
        "CardType": HotelConfig.GUARANTEE_CARD_TYPE,
        "CardCode": HotelConfig.GUARANTEE_CARD_CODE,
        "CardHolderName": HotelConfig.GUARANTEE_CARD_HOLDER_NAME,
        "CardNumber": {"@type": "CardNumber", "PlainText": HotelConfig.GUARANTEE_CARD_NUMBER},
        "SeriesCode": {"@type": "SeriesCode", "PlainText": HotelConfig.GUARANTEE_CARD_CVV},
        "PersonName": {
            "@type": "PersonNameDetail",
            "Given": holder_given,
            "Surname": holder_surname or holder_given,
        },
        "Address": {
            "@type": "AddressDetail",
            "AddressLine": [HotelConfig.GUARANTEE_CARD_BILLING_ADDRESS],
            "City": HotelConfig.GUARANTEE_CARD_BILLING_CITY,
            "Country": {"value": HotelConfig.GUARANTEE_CARD_BILLING_COUNTRY},
            "PostalCode": HotelConfig.GUARANTEE_CARD_BILLING_POSTAL,
        },
    }
    if HotelConfig.GUARANTEE_CARD_BILLING_STREET_NUMBER:
        card["Address"]["Number"] = {"value": HotelConfig.GUARANTEE_CARD_BILLING_STREET_NUMBER}
    if HotelConfig.GUARANTEE_CARD_BILLING_ADDRESS:
        card["Address"]["Street"] = HotelConfig.GUARANTEE_CARD_BILLING_ADDRESS
    if HotelConfig.GUARANTEE_CARD_BILLING_STATE_PROV:
        card["Address"]["StateProv"] = {"value": HotelConfig.GUARANTEE_CARD_BILLING_STATE_PROV}
    if HotelConfig.GUARANTEE_CARD_BILLING_COUNTY:
        card["Address"]["County"] = HotelConfig.GUARANTEE_CARD_BILLING_COUNTY
    if HotelConfig.GUARANTEE_CARD_BILLING_PHONE_NUMBER:
        card["Telephone"] = [{
            "@type": "TelephoneDetail",
            "countryAccessCode": HotelConfig.GUARANTEE_CARD_BILLING_PHONE_COUNTRY_CODE,
            "areaCityCode": HotelConfig.GUARANTEE_CARD_BILLING_PHONE_AREA_CODE,
            "phoneNumber": HotelConfig.GUARANTEE_CARD_BILLING_PHONE_NUMBER,
        }]
    if HotelConfig.GUARANTEE_CARD_BILLING_EMAIL:
        card["Email"] = [{"value": HotelConfig.GUARANTEE_CARD_BILLING_EMAIL}]
    return card


def _build_travelers(travelers: list[dict]) -> list[dict]:
    return [
        {
            "@type": "Traveler",
            "PersonName": {
                "@type": "PersonNameDetail",
                "Given": t.get("first_name", ""),
                "Surname": t.get("last_name", ""),
            },
            "Telephone": [
                {
                    "@type": "TelephoneDetail",
                    "countryAccessCode": t.get("country_access_code", ""),
                    "areaCityCode": t.get("area_city_code", ""),
                    "phoneNumber": t.get("phone", ""),
                }
            ],
            "Email": [{"value": t.get("email", "")}],
        }
        for t in travelers
    ]


def create_hotel_reservation(
    chain_code: str,
    property_code: str,
    booking_code: str,
    check_in_date: str,
    check_out_date: str,
    rooms: int,
    guests: int,
    price: dict,
    travelers: list[dict],
    offer_id: Optional[str] = None,
) -> dict:
    """
    Book a hotel room — Full Payload
    (https://developer.travelport.com/apis/stays/unified-check-out/createhotelreservation).

    Args:
        chain_code / property_code: from a Search by Location property.
        booking_code: from the selected room rate.
        check_in_date / check_out_date: "YYYY-MM-DD".
        rooms: number of rooms requested.
        guests: total guest count for this booking.
        price: {"currency": str, "base": float, "total_taxes": float, "total_price": float}
        travelers: list of {"first_name", "last_name", "email", "phone",
                             "country_access_code"?, "area_city_code"?}
        offer_id: optional CatalogOffering.id from a prior Availability call
            — Travelport's own examples include this on the Offer even in
            Full Payload requests (used for its price/guarantee-change
            validation). Omit if booking without a prior Availability call.

    Returns:
        dict: raw ReservationResponse from Travelport.

    Raises:
        HotelApiError: on any non-2xx response, or if the guarantee card
            isn't configured in .env.
    """
    if not HotelConfig.guarantee_card_configured():
        raise HotelApiError(
            "Hotel guarantee card is not configured. Set HOTEL_GUARANTEE_CARD_* "
            "in backend/.env before booking hotels — see config/hotel_config.py."
        )

    offer: dict = {
        "@type": "Offer",
        "Product": [
            {
                "@type": "ProductHospitality",
                "bookingCode": booking_code,
                "Quantity": rooms,
                "guests": guests,
                "PropertyKey": {
                    "@type": "PropertyKey",
                    "chainCode": chain_code,
                    "propertyCode": property_code,
                },
                "DateRange": {
                    "start": check_in_date,
                    "end": check_out_date,
                },
            }
        ],
        "Price": {
            "@type": "PriceDetail",
            "CurrencyCode": {"value": price.get("currency", HotelConfig.DEFAULT_CURRENCY)},
            "Base": price.get("base", 0),
            "TotalTaxes": price.get("total_taxes", 0),
            "TotalPrice": price.get("total_price", 0),
        },
    }
    if offer_id:
        offer["id"] = offer_id

    reservation_detail = {
        "ReservationDetail": {
            "@type": "Reservation",
            "Offer": [offer],
            "Traveler": _build_travelers(travelers),
            "Payment": [
                {
                    "@type": "Payment",
                    "Amount": {
                        "code": price.get("currency", HotelConfig.DEFAULT_CURRENCY),
                        "value": price.get("total_price", 0),
                    },
                    "guaranteeInd": True,
                    "depositInd": False,
                }
            ],
            "FormOfPayment": [
                {"@type": "FormOfPaymentPaymentCard", "PaymentCard": _build_payment_card()}
            ],
            "TravelAgency": {
                "AgencyPCC": {"agencyCode": HotelConfig.PCC},
            },
        }
    }

    headers = get_hotel_headers()
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.post(HotelEndpoints.CREATE_RESERVATION, json=reservation_detail, headers=headers)

    if response.status_code >= 400:
        logger.error(f"Hotel Create Reservation failed: {response.status_code} — {response.text[:800]}")
        raise HotelApiError(
            f"Hotel booking failed (HTTP {response.status_code})",
            status_code=response.status_code,
            body=response.text,
        )

    return response.json()


def create_hotel_reservation_from_offer(
    offer_id: str,
    total_price: float,
    currency: str,
    travelers: list[dict],
) -> dict:
    """
    Book a hotel room — Reference Payload
    (https://developer.travelport.com/apis/stays/unified-check-out/buildhotelreservation),
    against a cached CatalogOffering.id from hotel_availability_service.get_availability().
    This is the path used by the Search -> Availability -> book flow.

    Args:
        offer_id: CatalogOffering.id (offer["offer_id"] from
            hotel_availability_service.parse_availability_offers()). Cached
            by Travelport for 30 minutes from when Availability was called.
        total_price / currency: the offer's total price (offer["total_price"]
            / offer["currency"]) — sent as the Payment amount.
        travelers: same shape as create_hotel_reservation().

    Returns:
        dict: raw ReservationResponse from Travelport.

    Raises:
        HotelApiError: on any non-2xx response, or if the guarantee card
            isn't configured in .env.
    """
    if not HotelConfig.guarantee_card_configured():
        raise HotelApiError(
            "Hotel guarantee card is not configured. Set HOTEL_GUARANTEE_CARD_* "
            "in backend/.env before booking hotels — see config/hotel_config.py."
        )

    reservation_build = {
        "ReservationQueryBuild": {
            "@type": "ReservationQueryBuild",
            "ReservationBuild": {
                "@type": "ReservationBuildFromCatalogOffering",
                "BuildFromCatalogOfferingHospitality": {
                    "@type": "BuildFromCatalogOfferingHospitality",
                    "CatalogOfferingIdentifier": {"value": offer_id},
                },
                "Traveler": _build_travelers(travelers),
                "Payment": [
                    {
                        "@type": "Payment",
                        "Amount": {"code": currency, "value": total_price},
                        "guaranteeInd": True,
                        "depositInd": False,
                    }
                ],
                "FormOfPayment": [
                    {"@type": "FormOfPaymentPaymentCard", "PaymentCard": _build_payment_card()}
                ],
            },
        }
    }

    headers = get_hotel_headers()
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.post(HotelEndpoints.CREATE_RESERVATION_REFERENCE, json=reservation_build, headers=headers)

    if response.status_code >= 400:
        logger.error(f"Hotel Create Reservation (reference) failed: {response.status_code} — {response.text[:800]}")
        raise HotelApiError(
            f"Hotel booking failed (HTTP {response.status_code})",
            status_code=response.status_code,
            body=response.text,
        )

    return response.json()


def retrieve_hotel_reservation(locator_code: str) -> dict:
    """STEP 3 — Retrieve a hotel reservation by its Travelport locator code.

    identifierType=Locator tells Travelport the path value is a locator code,
    not its own internal Reservation ID (the endpoint also accepts
    SupplierLocator/DocumentNumber) — see
    https://developer.travelport.com/apis/stays/unified-check-out/retrievehotelreservation.
    detailViewInd=true is required to get the full ReservationDetail back.
    """
    headers = get_hotel_headers()
    params = {"identifierType": "Locator", "detailViewInd": "true"}
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.get(HotelEndpoints.retrieve_reservation(locator_code), params=params, headers=headers)

    if response.status_code >= 400:
        logger.error(f"Hotel Retrieve failed for {locator_code}: {response.status_code} — {response.text[:500]}")
        raise HotelApiError(
            f"Hotel reservation retrieval failed (HTTP {response.status_code})",
            status_code=response.status_code,
            body=response.text,
        )

    return response.json()


def cancel_hotel_reservation(locator_code: str, supplier_locator: str) -> bool:
    """STEP 4 — Cancel a hotel reservation."""
    headers = get_hotel_headers()
    params = {"supplierLocator": supplier_locator}
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.put(HotelEndpoints.cancel_reservation(locator_code), params=params, headers=headers)

    if response.status_code >= 400:
        logger.error(f"Hotel Cancel failed for {locator_code}: {response.status_code} — {response.text[:500]}")
        raise HotelApiError(
            f"Hotel reservation cancellation failed (HTTP {response.status_code})",
            status_code=response.status_code,
            body=response.text,
        )

    return True


def parse_hotel_reservation(raw_response: dict) -> dict:
    """
    Simplify a Create/Retrieve ReservationResponse into a flat dict for
    storage and display. Field paths per
    https://support.travelport.com/webhelp/JSONAPIs/Hotelv11/.../APIRef_Retrieve.htm
    (Create Reservation response uses the same structure).
    """
    reservation = raw_response.get("Reservation", raw_response.get("ReservationDetail", {})) or {}
    offers = reservation.get("Offer", []) or []
    offer = offers[0] if offers else {}
    products = offer.get("Product", []) or []
    product = products[0] if products else {}
    property_key = product.get("PropertyKey", {}) or {}
    date_range = product.get("DateRange", {}) or {}
    price = offer.get("Price", {}) or {}
    room_type = product.get("RoomType", {}) or {}

    receipts = reservation.get("Receipt", []) or []
    receipt = receipts[0] if receipts else {}
    confirmation = receipt.get("Confirmation", {}) or {}
    locator = confirmation.get("Locator", {}) or {}
    offer_status = confirmation.get("OfferStatus", {}) or {}

    return {
        "locator_code": locator.get("value", ""),
        "confirmation_number": confirmation.get("value", ""),
        "status": offer_status.get("Status", "Confirmed"),
        "property_name": product.get("propertyName", ""),
        "chain_code": property_key.get("chainCode", ""),
        "property_code": property_key.get("propertyCode", ""),
        "check_in_date": date_range.get("start", ""),
        "check_out_date": date_range.get("end", ""),
        "room_description": (room_type.get("Description", {}) or {}).get("value", ""),
        "currency": (price.get("CurrencyCode", {}) or {}).get("value", HotelConfig.DEFAULT_CURRENCY),
        "base_price": price.get("Base", 0),
        "total_taxes": price.get("TotalTaxes", 0),
        "total_price": price.get("TotalPrice", 0),
        "raw": raw_response,
    }

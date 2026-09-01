"""
services/hotel_booking_service.py
===================================
STEPS 2-4 — Create, Retrieve, and Cancel a hotel reservation via Travelport
TripServices Stays v11.

Full-payload Create Reservation is used (POST book/reservations), not the
reference-payload/build endpoint — same reasoning as the existing Air
integration's full-payload choice for GDS bookings (see api_endpoints.py
add_offer_to_workbench_full_payload): no dependency on a cached
Availability/SearchComplete session that expires after 30 minutes.

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
https://developer.travelport.com/apis/stays and
https://support.travelport.com/webhelp/JSONAPIs/Hotelv11/. Response parsing
is defensive so a schema mismatch degrades gracefully once access is granted.
"""

import httpx
import logging
from typing import Optional
from config.hotel_config import HotelConfig, HotelEndpoints
from services.hotel_common import get_hotel_headers, HotelApiError
from utils import tp_logger

logger = logging.getLogger(__name__)


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
) -> dict:
    """
    STEP 2 — Book a hotel room (full payload).

    Args:
        chain_code / property_code: from a SearchComplete property item.
        booking_code: from the selected room rate (rate["booking_code"]).
        check_in_date / check_out_date: "YYYY-MM-DD".
        rooms: number of rooms requested.
        guests: total guest count for this booking.
        price: {"currency": str, "base": float, "total_taxes": float, "total_price": float}
        travelers: list of {"first_name", "last_name", "email", "phone",
                             "country_access_code"?, "area_city_code"?}

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

    lead = travelers[0]

    reservation_detail = {
        "ReservationDetail": {
            "Offer": [
                {
                    "@type": "Offer",
                    "Identifier": {"authority": "TVPT"},
                    "Product": [
                        {
                            "@type": "ProductHospitality",
                            "bookingCode": booking_code,
                            "Quantity": str(rooms),
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
            ],
            "Traveler": [
                {
                    "@type": "Traveler",
                    "PersonName": {
                        "@type": "PersonName",
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
            ],
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
                {
                    "@type": "FormOfPaymentPaymentCard",
                    "PaymentCard": {
                        "@type": "PaymentCardDetail",
                        "expireDate": HotelConfig.GUARANTEE_CARD_EXPIRE,
                        "CardType": HotelConfig.GUARANTEE_CARD_TYPE,
                        "CardCode": HotelConfig.GUARANTEE_CARD_CODE,
                        "CardHolderName": HotelConfig.GUARANTEE_CARD_HOLDER_NAME,
                        "CardNumber": {"PlainText": HotelConfig.GUARANTEE_CARD_NUMBER},
                        "SeriesCode": {"PlainText": HotelConfig.GUARANTEE_CARD_CVV},
                        "Address": {
                            "@type": "AddressDetail",
                            "AddressLine": [HotelConfig.GUARANTEE_CARD_BILLING_ADDRESS],
                            "City": HotelConfig.GUARANTEE_CARD_BILLING_CITY,
                            "Country": {"value": HotelConfig.GUARANTEE_CARD_BILLING_COUNTRY},
                            "PostalCode": HotelConfig.GUARANTEE_CARD_BILLING_POSTAL,
                        },
                    },
                }
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


def retrieve_hotel_reservation(locator_code: str) -> dict:
    """STEP 3 — Retrieve a hotel reservation by its Travelport locator code."""
    headers = get_hotel_headers()
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.get(HotelEndpoints.retrieve_reservation(locator_code), headers=headers)

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
    with httpx.Client(timeout=HotelConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.put(HotelEndpoints.cancel_reservation(locator_code, supplier_locator), headers=headers)

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

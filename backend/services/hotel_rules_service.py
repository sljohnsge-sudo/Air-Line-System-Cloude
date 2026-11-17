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


def parse_rules(raw_response: dict) -> dict:
    """Customer-facing summary of a Rules (OfferHospitalityResponse) answer —
    field paths as in the live pre-prod response for FP-22792 (13 Oct 2026):
    Offer.TermsAndConditionsFull[0].{CancelPenalty[], CheckInOutPolicy,
    RatePaymentInfo, Guarantee[], TextBlock[], AcceptedCreditCard[]}, and
    Offer.Price for the confirmed price. Missing parts come back empty."""
    resp = raw_response.get("OfferHospitalityResponse", raw_response) or {}
    offer = resp.get("Offer", {}) or {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    terms_list = offer.get("TermsAndConditionsFull", []) or []
    terms = terms_list[0] if terms_list else {}
    price = offer.get("Price", {}) or {}

    penalties = []
    for p in terms.get("CancelPenalty", []) or []:
        deadline = ((p.get("Deadline") or {}).get("SpecificDate")) or {}
        hp = p.get("HotelPenalty", {}) or {}
        amount = None
        for a in hp.get("Amount", []) or []:
            amount = {"value": a.get("value"), "currency": a.get("code")}
            break
        penalties.append({
            "refundable": (p.get("Refundable") or "").lower() == "yes",
            "from": deadline.get("start"),
            "until": deadline.get("end"),
            "time": (p.get("Deadline") or {}).get("Time"),
            "percent": hp.get("Percent"),
            "amount": amount,
            "description": p.get("Description", ""),
        })

    policy = terms.get("CheckInOutPolicy", {}) or {}
    blocks = []
    for b in terms.get("TextBlock", []) or []:
        text = " ".join((t.get("value") or "").strip() for t in (b.get("TextFormatted") or []) if t.get("value"))
        if text:
            blocks.append({"title": b.get("title", ""), "text": text})

    free_until = next((p for p in penalties if p["refundable"] and p.get("until")), None)
    return {
        "currency": (price.get("CurrencyCode") or {}).get("value"),
        "base_price": price.get("Base"),
        "total_taxes": price.get("TotalTaxes"),
        "total_price": price.get("TotalPrice"),
        "refundable": any(p["refundable"] for p in penalties),
        "free_cancellation_until": free_until["until"] if free_until else None,
        "free_cancellation_time": free_until["time"] if free_until else None,
        "cancel_penalties": penalties,
        "check_in_time": (policy.get("checkInTime") or "")[:5],
        "check_out_time": (policy.get("checkOutTime") or "")[:5],
        "payment": terms.get("RatePaymentInfo", ""),  # e.g. "PostPay" = paid at the hotel
        "guarantee": [g.get("guaranteeType") for g in (terms.get("Guarantee") or []) if g.get("guaranteeType")],
        "accepted_cards": sorted({c.get("value") for c in (terms.get("AcceptedCreditCard") or []) if c.get("value")}),
        "description": [d for d in (terms.get("Description") or []) if d],
        "text_blocks": blocks,
    }

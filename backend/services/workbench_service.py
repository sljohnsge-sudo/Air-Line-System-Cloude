"""
services/workbench_service.py
==============================
STEPS 4, 5, 6, 7 — Reservation Workbench Flow
Handles creating a workbench, adding the selected flight offer,
adding traveler details, and committing to generate the PNR.

All Travelport workbench interactions happen here.
To update the booking payload structure: modify only this file.
"""

import httpx
import logging
import re
import time
from datetime import datetime
from config.travelport_config import TravelportConfig
from config.api_endpoints import TravelportEndpoints
from services.auth_service import get_auth_headers, invalidate_token, flow_trace_id
from services.pricing_service import get_settings as get_pricing_settings, apply_markup
from utils import tp_logger

logger = logging.getLogger(__name__)


def _api_post(url: str, payload: dict | str, session_id: str | None = None) -> dict:
    """Internal helper for POST requests with automatic token retry."""
    headers = get_auth_headers(session_id)
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        if isinstance(payload, str):
            response = client.post(url, content=payload, headers=headers)
        else:
            response = client.post(url, json=payload, headers=headers)
            
        if response.status_code == 401:
            invalidate_token()
            headers = get_auth_headers(session_id)
            if isinstance(payload, str):
                response = client.post(url, content=payload, headers=headers)
            else:
                response = client.post(url, json=payload, headers=headers)
                
        if response.status_code not in (200, 201):
            logger.error(f"API POST Error {response.status_code} for URL {url}: {response.text}")
        response.raise_for_status()
        return response.json()


def _raise_if_error(result: dict, step_name: str) -> None:
    """
    Per Travelport support feedback on submitted logs: a GDS Add Offer call
    returned HTTP 200 with an embedded failure ("0 Avail Closed" — the booked
    class had closed for sale between search and book) that the caller never
    checked for, so the flow continued as if it had succeeded. This checks
    every response for an embedded Result.Error the same way commit_workbench()
    and confirm_price() already did, and raises immediately with Travelport's
    own error text instead of silently proceeding.
    """
    errors = (
        result.get("Result", {}).get("Error", []) or
        result.get("OfferListResponse", {}).get("Result", {}).get("Error", []) or
        result.get("ReservationResponse", {}).get("Result", {}).get("Error", [])
    )
    if errors:
        raise ValueError(f"Travelport {step_name} failed: {errors[0].get('Message')}")


def _api_get(url: str, session_id: str | None = None) -> dict:
    """Internal helper for GET requests with automatic token retry."""
    headers = get_auth_headers(session_id)
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.get(url, headers=headers)
        if response.status_code == 401:
            invalidate_token()
            response = client.get(url, headers=get_auth_headers(session_id))
        response.raise_for_status()
        return response.json()


def _api_delete(url: str, session_id: str | None = None) -> None:
    """Internal helper for DELETE requests (used to ignore/cancel a stale workbench)."""
    headers = get_auth_headers(session_id)
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.delete(url, headers=headers)
        if response.status_code == 401:
            invalidate_token()
            response = client.delete(url, headers=get_auth_headers(session_id))
        # 200, 204, 404 are all acceptable — 404 means it was already gone
        if response.status_code not in (200, 204, 404):
            logger.warning(f"DELETE workbench returned unexpected status {response.status_code}")


def _api_patch(url: str, payload: dict, session_id: str | None = None) -> dict:
    """Internal helper for PATCH requests with automatic token retry."""
    headers = get_auth_headers(session_id)
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.patch(url, json=payload, headers=headers)
        if response.status_code == 401:
            invalidate_token()
            response = client.patch(url, json=payload, headers=get_auth_headers(session_id))
        if response.status_code not in (200, 201):
            logger.error(f"API PATCH Error {response.status_code} for URL {url}: {response.text}")
        response.raise_for_status()
        return response.json()


def _api_post_with_retry(url: str, payload: dict | str, session_id: str | None = None,
                         max_retries: int = 3, retry_on: tuple = (502, 503, 504)) -> dict:
    """
    POST with automatic retry on gateway errors (502/503/504).
    Uses exponential backoff: waits 3s, then 6s between retries.
    This handles transient Travelport sandbox timeouts gracefully.
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            return _api_post(url, payload, session_id=session_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in retry_on:
                wait = 3 * (2 ** attempt)  # 3s, 6s, 12s
                logger.warning(
                    f"Attempt {attempt + 1}/{max_retries} — got {e.response.status_code} from "
                    f"{url}. Retrying in {wait}s..."
                )
                last_exc = e
                if attempt < max_retries - 1:
                    time.sleep(wait)
            else:
                raise  # Non-retryable HTTP error — raise immediately
        except httpx.TimeoutException as e:
            wait = 3 * (2 ** attempt)
            logger.warning(
                f"Attempt {attempt + 1}/{max_retries} — request timed out for {url}. "
                f"Retrying in {wait}s..."
            )
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(wait)
    raise last_exc


# ── STEP 4: Create Workbench ───────────────────────────────────────────────────

_STALE_WORKBENCH_ERROR_CODE = "4350"  # Galileo 1G: "COMMIT OR IGNORE RESERVATION WORKBENCH"


class StaleWorkbenchError(Exception):
    """
    Raised when Travelport returns Galileo error 4350 at the commit step.
    The caller should clean up the stale workbench and retry the whole flow.
    """
    pass


def _ignore_stale_workbench(stale_id: str) -> None:
    """
    Ignore (DELETE) a stale open Galileo workbench that is blocking new workbench creation.
    Travelport Galileo 1G error 4350 occurs when a previous workbench session was never
    committed or explicitly ignored — e.g. a booking flow that was interrupted mid-way.
    Deleting the stale workbench via the reservationworkbench endpoint ignores it,
    clearing the PCC so a fresh workbench can be created.
    """
    ignore_url = f"{TravelportEndpoints.CREATE_WORKBENCH}/{stale_id}"
    logger.info(f"Ignoring stale open workbench {stale_id} to clear Galileo PCC lock...")
    try:
        _api_delete(ignore_url, session_id=None)
        logger.info(f"Stale workbench {stale_id} ignored successfully.")
    except Exception as ex:
        logger.warning(f"Could not ignore stale workbench {stale_id}: {ex} — continuing anyway.")


def discard_workbench(workbench_id: str) -> None:
    """
    Discard (DELETE) an open workbench session to clear the Galileo PCC lock.
    """
    ignore_url = f"{TravelportEndpoints.CREATE_WORKBENCH}/{workbench_id}"
    logger.info(f"Discarding workbench session {workbench_id}...")
    try:
        _api_delete(ignore_url, session_id=None)
        logger.info(f"Workbench session {workbench_id} discarded successfully.")
    except Exception as ex:
        logger.warning(f"Could not discard workbench {workbench_id}: {ex}")



def _extract_stale_workbench_id(result: dict) -> str | None:
    """
    Try to extract the stale/blocking workbench ID from a Travelport error 4350 response.
    Travelport sometimes embeds the blocking workbench ID in the error SourceID or Message.
    If we cannot find it we return None and the caller will skip the ignore step.
    """
    try:
        errors = (
            result.get("ReservationResponse", {}).get("Result", {}).get("Error", []) or
            result.get("Result", {}).get("Error", [])
        )
        for err in errors:
            # SourceID sometimes contains the stale workbench UUID
            source_id = err.get("SourceID", "")
            if source_id and "-" in source_id and len(source_id) > 10:
                return source_id
            # Sometimes the UUID appears in the Message field
            msg = err.get("Message", "")
            uuid_match = re.search(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                msg, re.IGNORECASE
            )
            if uuid_match:
                return uuid_match.group(0)
    except Exception:
        pass
    return None


def _is_stale_workbench_error(result: dict) -> bool:
    """Return True if the response contains Galileo error 4350 (stale open workbench)."""
    try:
        errors = (
            result.get("ReservationResponse", {}).get("Result", {}).get("Error", []) or
            result.get("Result", {}).get("Error", [])
        )
        for err in errors:
            if str(err.get("SourceCode", "")) == _STALE_WORKBENCH_ERROR_CODE:
                return True
            if "COMMIT OR IGNORE RESERVATION WORKBENCH" in str(err.get("Message", "")):
                return True
    except Exception:
        pass
    return False


def create_workbench() -> str:
    """
    STEP 4: Create a new Reservation Workbench session.

    Automatically handles Galileo 1G error 4350 ("COMMIT OR IGNORE RESERVATION WORKBENCH"):
    if a stale open workbench is blocking creation, it is ignored (DELETEd) and the
    create call is retried once. This covers interrupted/failed booking flows that left
    an orphan workbench session open on the PCC.

    Returns:
        str: Workbench ID to use in subsequent steps.
    """
    logger.info("Creating reservation workbench...")

    payload = {"ReservationID": {}}
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        # Creating a workbench MUST be stateless (no travelportPlusSessionIdentifier header)
        result = _api_post(TravelportEndpoints.CREATE_WORKBENCH, payload, session_id=None)

        # ── Happy path: extract workbench ID ──────────────────────────────────
        workbench_id = (
            result.get("ReservationResponse", {}).get("Reservation", {}).get("Identifier", {}).get("value") or
            result.get("Reservation", {}).get("Identifier", {}).get("value") or
            result.get("id") or
            result.get("ReservationWorkbench", {}).get("identifier", {}).get("value")
        )

        if workbench_id:
            logger.info(f"Workbench created: {workbench_id}")
            return workbench_id

        # ── Error 4350: stale open workbench blocking creation ─────────────────
        if _is_stale_workbench_error(result):
            if attempt < max_attempts:
                logger.warning(
                    f"Attempt {attempt}/{max_attempts}: Galileo error 4350 — stale open workbench "
                    f"detected. Attempting to auto-ignore it..."
                )
                stale_id = _extract_stale_workbench_id(result)
                if stale_id:
                    _ignore_stale_workbench(stale_id)
                else:
                    # No ID in the error — wait briefly; Galileo may auto-expire old sessions
                    logger.warning(
                        "Could not extract stale workbench ID from error. "
                        f"Waiting 3s before retry {attempt + 1}/{max_attempts}..."
                    )
                    time.sleep(3)
                continue  # retry the create call
            else:
                errors = (
                    result.get("ReservationResponse", {}).get("Result", {}).get("Error", []) or
                    result.get("Result", {}).get("Error", [])
                )
                error_msg = errors[0].get("Message") if errors else "COMMIT OR IGNORE RESERVATION WORKBENCH"
                raise ValueError(
                    f"Travelport booking failed after {max_attempts} attempts: {error_msg}. "
                    "A previous booking session could not be cleared. Please try again in a moment."
                )

        # Unknown failure — no workbench ID and not error 4350
        logger.error(f"Could not find workbench ID in response (attempt {attempt}): {result}")
        raise ValueError("Travelport did not return a valid workbench ID.")

    raise ValueError("Workbench creation failed after all retries.")


# ── STEP 5: Add Offer to Workbench ────────────────────────────────────────────

def _get_leg_offerings(raw_offering: dict) -> list:
    """
    Normalize raw_offering (one-way / round-trip {"outbound","inbound"} /
    multi-city {"legs":[...]}) into a flat list of per-leg raw offering
    dicts. Shared by add_offer_to_workbench and confirm_price so both walk
    the same leg structure the same way.
    """
    is_round_trip = isinstance(raw_offering, dict) and "outbound" in raw_offering and "inbound" in raw_offering
    is_multi_leg = isinstance(raw_offering, dict) and "legs" in raw_offering
    if is_round_trip:
        return [raw_offering["outbound"], raw_offering["inbound"]]
    elif is_multi_leg:
        return raw_offering["legs"]
    return [raw_offering]


def _build_offering_selection(raw_offering: dict) -> dict:
    """
    Build a single CatalogProductOfferingSelection entry (offer id + product refs)
    from one leg's raw offering object.
    """
    offer_id = raw_offering.get("id", raw_offering.get("offer_id", ""))

    product_refs = []
    try:
        brand_options = raw_offering.get("ProductBrandOptions", [])
        for pbo in brand_options:
            brand_offering = pbo.get("ProductBrandOffering", [])
            if brand_offering:
                product_list = brand_offering[0].get("Product", [])
                for p in product_list:
                    ref = p.get("productRef", "")
                    if ref and ref not in product_refs:
                        product_refs.append(ref)
    except Exception:
        pass

    if not product_refs:
        product_refs = ["p0"]  # fallback default

    return {
        "CatalogProductOfferingIdentifier": {
            "Identifier": {
                "value": offer_id
            }
        },
        "ProductIdentifier": [
            {
                "Identifier": {
                    "value": ref
                }
            } for ref in product_refs
        ]
    }


def _build_specific_flight_criteria(segments: list, cabin: str | None = None, class_of_service: str | None = None, content_source: str | None = None) -> list:
    """
    Build the SpecificFlightCriteria array (one entry per physical flight segment)
    for the full-payload AddOffer request, from one leg's parsed `segments` list
    (search_service.py's segments_list, carried through on raw_offering).

    cabin / class_of_service pin the exact booking class that was priced and
    shown to the user. Without these, Travelport can reprice the same flight
    number against a different available class within the same cabin/brand —
    confirmed live: omitting them let a flydubai fare reprice ~3.4x higher at
    commit than what was quoted at search.

    content_source: carried through from the leg's own fare_source (GDS/NDC/LCC)
    per Travelport's GDS full-payload certification reference (booking_HMZ9HH/
    5.Add Offer RQ), which sends "ContentSource" on every segment.
    """
    criteria = []
    for idx, seg in enumerate(segments, start=1):
        dep_date, _, dep_time = (seg.get("departure_time") or "").partition("T")
        arr_date, _, arr_time = (seg.get("arrival_time") or "").partition("T")
        entry = {
            "@type": "SpecificFlightCriteria",
            "carrier": seg.get("carrier", ""),
            "flightNumber": seg.get("raw_number", ""),
            "from": seg.get("departure_airport", ""),
            "to": seg.get("arrival_airport", ""),
            "departureDate": dep_date,
            "segmentSequence": idx,
        }
        if dep_time:
            entry["departureTime"] = dep_time
        if arr_date:
            entry["arrivalDate"] = arr_date
        if arr_time:
            entry["arrivalTime"] = arr_time
        if cabin:
            entry["cabin"] = cabin
        if class_of_service:
            entry["classOfService"] = class_of_service
        # Both carried straight through from the search response (see
        # search_service.py's segments_list build) — never hardcoded.
        # AvailabilitySourceCode is "optional but recommended" per
        # Travelport's Add Offer Full Payload API Reference; boundFlightsInd
        # is only included when the search response actually marked this
        # segment bound to the next (omitted otherwise, same as Travelport's
        # own response does).
        if seg.get("availability_source_code"):
            entry["AvailabilitySourceCode"] = seg["availability_source_code"]
        if seg.get("bound_flights_ind"):
            entry["boundFlightsInd"] = True
        if content_source:
            entry["ContentSource"] = content_source
        criteria.append(entry)
    return criteria


def _extract_passenger_criteria_from_offering(raw_offering: dict) -> list:
    """
    Derive PassengerCriteria (passenger type + count) from the priced offer's
    own PriceBreakdown — the full-payload AddOffer request requires passenger
    info, but travelers (names/passports) aren't collected until STEP 6, so we
    reconstruct just the type/count/age from what was already priced at search
    time. Ordered Adult -> Infant -> Child per Travelport GDS certification.
    """
    pax_by_type = {}
    try:
        for pbo in raw_offering.get("ProductBrandOptions", []):
            for bo in pbo.get("ProductBrandOffering", []):
                for pb in bo.get("BestCombinablePrice", {}).get("PriceBreakdown", []):
                    ptc = pb.get("requestedPassengerType")
                    qty = int(pb.get("quantity", 1))
                    if ptc:
                        pax_by_type[ptc] = max(pax_by_type.get(ptc, 0), qty)
    except Exception:
        pass
    if not pax_by_type:
        pax_by_type = {"ADT": 1}

    age_map = {"ADT": 25, "INF": 1, "CNN": 10}
    passenger_type_order = {"ADT": 0, "INF": 1, "CNN": 2}
    return [
        {
            "@type": "PassengerCriteria",
            "number": pax_by_type[ptc],
            "age": age_map.get(ptc, 25),
            "passengerTypeCode": ptc
        }
        for ptc in sorted(pax_by_type.keys(), key=lambda t: passenger_type_order.get(t, 99))
    ]


def _build_full_payload_offer(leg_offerings: list) -> dict:
    """
    Build the OfferQueryBuildFromProducts (full payload) request body for the
    given leg(s) — Travelport GDS certification guidance for GDS carrier
    bookings. Not used for NDC/LCC content (Travelport: full payload not
    supported for NDC).
    """
    product_criteria = []
    for i, leg in enumerate(leg_offerings, start=1):
        cos_list = leg.get("classes_of_service") or []
        product_criteria.append({
            "@type": "ProductCriteriaAir",
            "sequence": i,
            "SpecificFlightCriteria": _build_specific_flight_criteria(
                leg.get("segments", []),
                cabin=leg.get("cabin_class"),
                class_of_service=cos_list[0] if cos_list else None,
                content_source=leg.get("fare_source")
            )
        })

    request_air = {
        "@type": "BuildFromProductsRequestAir",
        "ProductCriteriaAir": product_criteria,
        "PassengerCriteria": _extract_passenger_criteria_from_offering(leg_offerings[0])
    }

    # Pricing modifier pins the request to the exact fare brand the user selected
    # and was priced for — without it, Travelport substitutes its own default
    # ("auto stored fare") instead of the one that was shown and picked.
    brand_id = leg_offerings[0].get("brand_id")
    brand_name = leg_offerings[0].get("brand_name")
    if brand_id or brand_name:
        brand_obj = {"@type": "Brand", "name": brand_name or "Standard"}
        if brand_id:
            brand_obj["BrandRef"] = brand_id
        request_air["PricingModifiersAir"] = {
            "@type": "PricingModifiersAir",
            "Brand": brand_obj
        }

    return {
        "@type": "OfferQueryBuildFromProducts",
        # Per Travelport's own GDS full-payload certification reference
        # (booking_HMZ9HH/5.Add Offer RQ) — asks Travelport to check live
        # inventory before adding the offer, rather than trusting the
        # search-time snapshot. Confirmed present on their reference request.
        "validateInventoryInd": True,
        "BuildFromProductsRequest": request_air
    }


def confirm_price(raw_offering: dict) -> str:
    """
    STEP 3b: Confirm pricing via AirPrice, called for every content source
    (GDS and NDC) — see run_booking_flow's docstring. Serves Travelport
    support's request to validate availability/fare between Search and Book
    and catch a closed-for-sale class ("0 Avail Closed") before spending a
    full workbench create/add-offer/add-traveler/commit attempt on it.

    Payload shape differs by content source, per Travelport certification
    guidance (2026-09-16 call): for GDS content, Price uses the SAME
    full-payload construction as the full-payload Add Offer call (STEP 5) —
    see _build_full_payload_offer — POSTed to
    TravelportEndpoints.AIRPRICE_FULL_PAYLOAD. NDC/LCC content (and GDS
    content with no segments) keeps the lightweight reference payload
    (offer id + product refs only, via _build_reference_payload_offer)
    POSTed to TravelportEndpoints.AIRPRICE_REFERENCE, against the same
    cached Search transaction (relies on the same offersPerPage caching that
    Add Offer already depends on).

    Not tied to any workbench either way — POSTs directly to /air/price/....

    Returns:
        str: The AirPrice response's transactionId. Per Travelport docs,
        "If you add the offer after a price request, you can send the
        transaction identifier from the AirPrice response" — the caller
        should use this value as the CatalogProductOfferingsIdentifier for
        the subsequent Add Offer call instead of the original Search one.
    """
    leg_offerings = _get_leg_offerings(raw_offering)
    is_gds = all(leg.get("fare_source") == "GDS" for leg in leg_offerings)
    has_segments = all(leg.get("segments") for leg in leg_offerings)

    if is_gds and has_segments:
        payload = _build_full_payload_offer(leg_offerings)
        url = TravelportEndpoints.AIRPRICE_FULL_PAYLOAD
        logger.info("Confirming price via AirPrice (GDS offer, full payload)...")
    else:
        payload = _build_reference_payload_offer(leg_offerings)
        url = TravelportEndpoints.AIRPRICE_REFERENCE
        logger.info("Confirming price via AirPrice (NDC/LCC offer, reference payload)...")

    # AirPrice is not workbench-scoped, so no session_id.
    result = _api_post_with_retry(url, payload, session_id=None)

    errors = (
        result.get("OfferListResponse", {}).get("Result", {}).get("Error", []) or
        result.get("Result", {}).get("Error", [])
    )
    if errors:
        raise ValueError(f"Travelport AirPrice failed: {errors[0].get('Message')}")

    # Confirmed live: the AirPrice response does NOT carry a "transactionId"
    # field at all (despite the docs' mock example showing one) — the field
    # that actually replaces the original CatalogProductOfferingsIdentifier
    # for the subsequent Add Offer call is OfferListResponse.Identifier.value,
    # which Travelport returns as the original identifier with a "_PC" suffix
    # appended (e.g. "<original-uuid>_PC"). transactionId is kept as a
    # fallback only in case a different content source/carrier ever returns
    # that shape instead.
    offer_list = result.get("OfferListResponse", result)
    priced_transaction_id = (
        offer_list.get("Identifier", {}).get("value") or
        offer_list.get("transactionId")
    )
    if not priced_transaction_id:
        # Log the full raw response so any future shape mismatch can be
        # fixed from evidence rather than guessed at again (see
        # commit_workbench's multi-shape Reservation lookup, issue_ticket's
        # multi-shape workbench lookup, etc. — this codebase has repeatedly
        # found live Travelport responses to omit documented wrapper keys).
        logger.error(f"AirPrice did not return a usable identifier. Full response: {result}")
        raise ValueError("Travelport AirPrice did not return a transactionId.")

    logger.info(f"AirPrice confirmed. transactionId for Add Offer: {priced_transaction_id}")
    return priced_transaction_id


def _build_reference_payload_offer(leg_offerings: list) -> dict:
    """Reference-payload Add Offer request body — the proven path for
    NDC/LCC content, and the fallback for GDS content with no segments."""
    catalog_offerings_id = leg_offerings[0].get("CatalogProductOfferingsIdentifier", "")
    offering_selections = [_build_offering_selection(leg) for leg in leg_offerings]
    return {
        "OfferQueryBuildFromCatalogProductOfferings": {
            "BuildFromCatalogProductOfferingsRequest": {
                "@type": "BuildFromCatalogProductOfferingsRequestAir",
                "CatalogProductOfferingsIdentifier": {
                    "Identifier": {
                        "value": catalog_offerings_id
                    }
                },
                "CatalogProductOfferingSelection": offering_selections
            }
        }
    }


def add_offer_to_workbench(workbench_id: str, raw_offering: dict, force_reference_payload: bool = False) -> dict:
    """
    STEP 5: Add the selected flight offer(s) to the workbench.

    Args:
        workbench_id (str): Workbench ID from STEP 4
        raw_offering (dict): One of:
            - a single-leg raw offer object from the search response (one-way)
            - a round-trip combined object shaped {"outbound": ..., "inbound": ...}
              produced by search_service.pair_round_trip_offers
            - a multi-city combined object shaped {"legs": [<raw offering>, ...]}
              (2..N legs) produced by search_service.pair_multi_leg_offers
            All legs share the same CatalogProductOfferingsIdentifier since
            they come from the same search.
        force_reference_payload (bool): Skip the full-payload attempt
            entirely and go straight to reference payload. Set by
            run_booking_flow() on its whole-flow retry after a full-payload
            NDC/LCC attempt got past Add Offer but failed at Commit — see
            its docstring for why that's a real, confirmed failure mode.

    Returns:
        dict: Updated workbench item response
    """
    logger.info(f"Adding offer to workbench {workbench_id}...")

    leg_offerings = _get_leg_offerings(raw_offering)

    is_gds = all(leg.get("fare_source") == "GDS" for leg in leg_offerings)
    has_segments = all(leg.get("segments") for leg in leg_offerings)

    if has_segments and not force_reference_payload:
        # Per Travelport support's explicit request: include classOfService /
        # AvailabilitySourceCode / boundFlightsInd, which only exist in the
        # full-payload SpecificFlightCriteria block. Travelport's own Add
        # Offer docs say full payload is "not supported for NDC" — attempted
        # here anyway per instruction, but with a safety net: if it's
        # rejected for non-GDS content, fall back to the reference payload
        # immediately rather than letting the booking fail outright.
        payload = _build_full_payload_offer(leg_offerings)
        url = TravelportEndpoints.add_offer_to_workbench_full_payload(workbench_id)
        try:
            result = _api_post_with_retry(url, payload, session_id=workbench_id)
            _raise_if_error(result, "Add Offer (full payload)")
            logger.info("Offer added to workbench (full payload).")
            return result
        except (ValueError, httpx.HTTPStatusError) as e:
            if is_gds:
                raise  # GDS full payload is the proven path — a real failure here must surface
            logger.warning(
                f"Full-payload Add Offer failed for non-GDS content ({e}); "
                "falling back to reference payload (Travelport docs: full payload not supported for NDC)."
            )

    # Reference payload: either segments were unavailable, or (for non-GDS
    # content) the full-payload attempt above failed and this is the fallback.
    payload = _build_reference_payload_offer(leg_offerings)
    url = TravelportEndpoints.add_offer_to_workbench(workbench_id)
    result = _api_post_with_retry(url, payload, session_id=workbench_id)
    _raise_if_error(result, "Add Offer")
    logger.info("Offer added to workbench (reference payload).")
    return result


# ── STEP 6: Add Traveler(s) ───────────────────────────────────────────────────

def _person_prefix(gender: str, passenger_type: str) -> str:
    """
    PersonName.Prefix per Travelport/Galileo 1G convention:
    - Adults: MR / MRS
    - Children and infants (CNN / INF): MSTR (male) / MISS (female)
    """
    is_male = gender == "Male"
    if passenger_type in ("CNN", "INF"):
        return "MSTR" if is_male else "MISS"
    return "MR" if is_male else "MRS"


def _calculate_age(date_of_birth: str) -> int | None:
    """Whole-years age computed from a YYYY-MM-DD birth date to today."""
    try:
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d")
        today = datetime.now()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return max(age, 0)
    except (ValueError, TypeError):
        return None


def _build_traveler_payload(traveler: dict, traveler_id: str | None = None, is_gds: bool = True) -> dict:
    """
    Build a single Traveler object from our internal passenger dict. Shared by
    both the single-traveler request (add_traveler_to_workbench) and the
    multi-traveler list request (add_travelers_to_workbench).

    Args:
        traveler (dict): Passenger info with keys:
            - first_name, last_name (str)
            - date_of_birth (str)  YYYY-MM-DD
            - gender (str)         "Male" | "Female"
            - passenger_type (str) "ADT" | "CNN" | "INF"
            - passport_number, passport_expiry (str)
            - nationality (str)    ISO country code e.g. "LK"
            - email, phone (str)
        traveler_id (str | None): Set to "trav_1", "trav_2", ... when this
            traveler is part of a TravelerListRequest (required so Travelport
            can distinguish multiple passengers in one request).
        is_gds (bool): False for NDC/LCC content. Per Travelport's NDC guide
            (developer.travelport.com/docs/flights/ndc/ndc-guide), the Add
            Traveler request must omit Telephone/extension for NDC — it's
            listed there as a GDS-only field. docType is unaffected since this
            codebase always sends "Passport", never the other GDS-only
            "PassportCard" value the same guide calls out.
    """
    # Clean phone number and parse country code for Galileo 1G
    phone_clean = traveler.get("phone", "").replace("+", "").replace(" ", "").replace("-", "")
    country_code = "94"
    phone_num = phone_clean
    if phone_clean.startswith("94"):
        phone_num = phone_clean[2:]
    elif len(phone_clean) > 9:
        country_code = phone_clean[:-9]
        phone_num = phone_clean[-9:]

    prefix = _person_prefix(traveler.get("gender", "Male"), traveler.get("passenger_type", "ADT"))

    passenger_type = traveler.get("passenger_type", "ADT")

    # Telephone.id — local reference per traveler, matching Travelport's sample
    # (id "1"/"2"/"3" alongside trav_1/trav_2/trav_3). Derived from traveler_id
    # when part of a batch request, else defaults to "1".
    telephone_id = "1"
    if traveler_id:
        m = re.search(r"(\d+)$", traveler_id)
        if m:
            telephone_id = m.group(1)

    telephone_entry = {
        "@type": "Telephone",
        "countryAccessCode": country_code,
        "phoneNumber": phone_num,
        "id": telephone_id,
        "role": "Mobile"
    }
    if traveler.get("phone_area_city_code"):
        telephone_entry["areaCityCode"] = traveler.get("phone_area_city_code")
    if traveler.get("phone_extension") and is_gds:
        telephone_entry["extension"] = traveler.get("phone_extension")
    if traveler.get("phone_city_code"):
        telephone_entry["cityCode"] = traveler.get("phone_city_code")

    travel_document = {
        "@type": "TravelDocumentDetail",
        "docNumber": traveler.get("passport_number", ""),
        "docType": "Passport",
        "expireDate": traveler.get("passport_expiry", ""),
        # Passport issuing country — collected as its own field since it can
        # differ from the traveler's nationality; falls back to nationality
        # only if somehow not supplied.
        "issueCountry": traveler.get("passport_issue_country") or traveler.get("nationality", "LK"),
        # "Birth country as noted on document" — per Travelport's own
        # TravelDocumentDetail schema this field is named "Nationality"
        # (confirmed against support.travelport.com/.../APIRef_TravelerAdd.htm).
        # An earlier version of this code used a non-existent "birthCountry"
        # key, which Travelport silently ignored — this is the fix.
        "Nationality": traveler.get("nationality", "LK"),
        "birthDate": traveler.get("date_of_birth", ""),
        "Gender": traveler.get("gender", "Male"),
        "PersonName": {
            # Travelport's own sample uses PersonNameDetail here (same @type
            # as the top-level PersonName), not the bare "PersonName" type.
            "@type": "PersonNameDetail",
            "Given": traveler.get("first_name", "").upper(),
            "Surname": traveler.get("last_name", "").upper()
        }
    }
    if traveler.get("document_issue_date"):
        travel_document["issueDate"] = traveler.get("document_issue_date")
    if traveler.get("birth_place"):
        travel_document["birthPlace"] = traveler.get("birth_place")
    if traveler.get("issued_for_geo_political_area"):
        travel_document["IssuedForGeoPoliticalArea"] = {"value": traveler.get("issued_for_geo_political_area")}

    address_fields = (
        "address_street", "address_city", "address_state_name",
        "address_state_value", "address_country", "address_postal_code"
    )
    if any(traveler.get(f) for f in address_fields):
        address = {"@type": "Address", "role": "Destination"}
        if traveler.get("address_street"):
            address["Street"] = traveler.get("address_street")
        if traveler.get("address_city"):
            address["City"] = traveler.get("address_city")
        if traveler.get("address_state_name") or traveler.get("address_state_value"):
            state_prov = {}
            if traveler.get("address_state_name"):
                state_prov["name"] = traveler.get("address_state_name")
            if traveler.get("address_state_value"):
                state_prov["value"] = traveler.get("address_state_value")
            address["StateProv"] = state_prov
        if traveler.get("address_country"):
            address["Country"] = {"value": traveler.get("address_country")}
        if traveler.get("address_postal_code"):
            address["PostalCode"] = traveler.get("address_postal_code")
        travel_document["Address"] = address

    payload = {
        "@type": "Traveler",
        "gender": traveler.get("gender", "Male"),
        "birthDate": traveler.get("date_of_birth", ""),
        "passengerTypeCode": passenger_type,
        "PersonName": {
            "@type": "PersonNameDetail",
            "Prefix": prefix,  # Prefix required for Galileo 1G validation
            "Given": traveler.get("first_name", "").upper(),
            "Surname": traveler.get("last_name", "").upper()
        },
        "Telephone": [telephone_entry],
        "Email": [
            {
                "value": traveler.get("email", "")
            }
        ],
        "TravelDocument": [travel_document]
    }

    # Explicit numeric age (not just birthDate) for every passenger type, so
    # Travelport can validate the fare's PTC age bracket. Adult and Child use
    # the traveler's real computed age from date of birth. Infants are
    # always reported as age 1 regardless of actual DOB (real infants are
    # under 12 months, which would compute to 0) — per Travelport
    # certification guidance (2026-09-16 call), applies to both GDS and NDC.
    if passenger_type == "INF":
        payload["age"] = 1
    else:
        age = _calculate_age(traveler.get("date_of_birth", ""))
        if age is not None:
            payload["age"] = age

    if traveler_id:
        payload["id"] = traveler_id
    return payload


def add_traveler_to_workbench(workbench_id: str, traveler: dict, is_gds: bool = True, traveler_id: str | None = None) -> dict:
    """
    Add a SINGLE passenger/traveler to the workbench (.../travelers, not the
    batched .../travelers/list).

    Used both standalone by other callers, and by run_booking_flow() for NDC
    content specifically — Travelport's own NDC certification reference logs
    (TravelportNDC_6Aug/6_add adult.txt, 7_add infant.txt, 8_add child.txt)
    add each traveler with its own separate call to this same single-traveler
    endpoint, in Adult -> Infant -> Child order, rather than one combined
    TravelerListRequest. GDS content still uses the combined
    add_travelers_to_workbench below (matches the GDS full-payload
    certification logs, which use .../travelers/list).

    Returns:
        dict: Updated workbench response
    """
    logger.info(f"Adding traveler to workbench {workbench_id}: {traveler.get('first_name')} {traveler.get('last_name')}")

    payload = {"Traveler": _build_traveler_payload(traveler, traveler_id=traveler_id, is_gds=is_gds)}

    url = TravelportEndpoints.update_workbench(workbench_id)
    result = _api_post(url, payload, session_id=workbench_id)
    _raise_if_error(result, "Add Traveler")
    logger.info("Traveler added to workbench.")
    return result


def add_travelers_individually_to_workbench(workbench_id: str, travelers: list, is_gds: bool = False) -> None:
    """
    STEP 6 (NDC content): add each traveler with its own separate
    .../travelers call, in the order given by the caller (run_booking_flow
    already sorts Adult -> Infant -> Child before calling this). See
    add_traveler_to_workbench's docstring for why NDC uses this instead of
    the combined TravelerListRequest that GDS content uses.
    """
    for i, traveler in enumerate(travelers, start=1):
        add_traveler_to_workbench(workbench_id, traveler, is_gds=is_gds, traveler_id=f"Trav_{i - 1}")


def add_travelers_to_workbench(workbench_id: str, travelers: list, is_gds: bool = True) -> dict:
    """
    STEP 6: Add ALL passengers for a booking to the workbench in a SINGLE
    Travelport request (TravelerListRequest → .../travelers/list), instead of
    one request per traveler. This is what makes a booking with mixed
    passenger types (e.g. 1 Adult + 1 Child + 1 Infant) result in exactly one
    Travelport booking API call for that PNR, not N separate calls.

    Args:
        workbench_id (str): Workbench ID from STEP 4
        travelers (list[dict]): All passengers for this booking/PNR — see
            _build_traveler_payload for the expected dict shape.
        is_gds (bool): False for NDC/LCC content — passed through to
            _build_traveler_payload to omit the GDS-only Telephone/extension
            field per Travelport's NDC guide. Defaults True so any caller
            that doesn't know the offer's content source keeps prior behavior.

    Returns:
        dict: Updated workbench response
    """
    names = ", ".join(
        f"{t.get('first_name')} {t.get('last_name')} ({t.get('passenger_type', 'ADT')})"
        for t in travelers
    )
    logger.info(f"Adding {len(travelers)} traveler(s) to workbench {workbench_id} in a single request: {names}")

    traveler_payloads = [
        _build_traveler_payload(t, traveler_id=f"trav_{i}", is_gds=is_gds)
        for i, t in enumerate(travelers, start=1)
    ]

    payload = {
        "TravelerListRequest": {
            "@type": "TravelerListRequest",
            "Traveler": traveler_payloads
        }
    }

    url = TravelportEndpoints.add_travelers_list(workbench_id)
    result = _api_post(url, payload, session_id=workbench_id)
    _raise_if_error(result, "Add Traveler(s)")
    logger.info(f"{len(travelers)} traveler(s) added to workbench in a single Travelport request.")
    return result


def add_travel_agency_to_workbench(workbench_id: str) -> dict:
    """
    STEP 6b: Attach George Steuart Travel's own agency address/contact/
    corporate code to the workbench (Travelport GDS certification step 7 —
    booking_HMZ9HH/7.Add Travel Agency RQ). Optional per Travelport docs
    (mandatory only for AF/KL NDC bookings), called here for GDS content to
    match the certification reference. Values come from TravelportConfig,
    sourced from George Steuart Travel's own registered details, not
    fabricated.
    """
    logger.info(f"Adding travel agency details to workbench {workbench_id}...")

    payload = {
        "TravelAgencyQueryTravelAgencyWrapper": {
            "TravelAgencyQueryTravelAgency": {
                "Address": {
                    "AddressLine": TravelportConfig.AGENCY_ADDRESS_LINE,
                    "City": TravelportConfig.AGENCY_CITY,
                    "Country": {"name": TravelportConfig.AGENCY_COUNTRY},
                    "PostalCode": TravelportConfig.AGENCY_POSTAL_CODE,
                    "Addressee": TravelportConfig.AGENCY_NAME
                },
                "CorporateCode": TravelportConfig.AGENCY_CORPORATE_CODE,
                "Telephone": [
                    {
                        "countryAccessCode": TravelportConfig.AGENCY_PHONE_COUNTRY_CODE,
                        "areaCityCode": TravelportConfig.AGENCY_PHONE_AREA_CODE,
                        "phoneNumber": TravelportConfig.AGENCY_PHONE_NUMBER
                    }
                ],
                "Email": {
                    "value": TravelportConfig.AGENCY_EMAIL
                }
            }
        }
    }

    url = TravelportEndpoints.add_travel_agency(workbench_id)
    result = _api_post(url, payload, session_id=workbench_id)
    _raise_if_error(result, "Add Travel Agency")
    logger.info("Travel agency details added to workbench.")
    return result


def get_workbench_details(workbench_id: str) -> dict:
    """
    Retrieve the current state of an open workbench session — optional
    verification step available after adding all travelers, to confirm they
    were recorded correctly before committing. Not called automatically as
    part of run_booking_flow (it's an extra round-trip on every booking);
    call it explicitly wherever that confirmation is wanted.
    """
    logger.info(f"Retrieving workbench {workbench_id} details...")
    url = TravelportEndpoints.get_workbench(workbench_id)
    return _api_get(url, session_id=workbench_id)


# ── STEP 7: Commit Workbench → Generate PNR ───────────────────────────────────

def commit_workbench(workbench_id: str) -> dict:
    """
    STEP 7: Commit the workbench to generate a PNR/Locator Code.

    Args:
        workbench_id (str): The workbench to commit

    Returns:
        dict: Reservation response including locator code (PNR)

    Raises:
        ValueError: if no locator code is found in the response
    """
    logger.info(f"Committing workbench {workbench_id} to generate PNR...")

    url = TravelportEndpoints.commit_workbench(workbench_id)
    # Commit requires an empty string payload content=""
    result = _api_post(url, "", session_id=workbench_id)

    # ── Detect error 4350 at commit time ──────────────────────────────────────
    # Galileo may return error 4350 (COMMIT OR IGNORE RESERVATION WORKBENCH)
    # even at commit time — meaning there is a stale open workbench blocking the
    # GDS session. We ignore the stale workbench (DELETE it) and raise
    # StaleWorkbenchError so the caller can retry the entire booking flow fresh.
    if _is_stale_workbench_error(result):
        stale_id = _extract_stale_workbench_id(result)
        # If the error didn't embed an ID, fall back to the current workbench
        # which is now stuck open and must be cleared.
        target_id = stale_id or workbench_id
        logger.warning(
            f"Stale workbench error 4350 detected at commit. "
            f"Ignoring workbench {target_id} and raising StaleWorkbenchError for retry..."
        )
        _ignore_stale_workbench(target_id)
        # If stale_id differs from current, also ignore the current one
        if stale_id and stale_id != workbench_id:
            _ignore_stale_workbench(workbench_id)
        raise StaleWorkbenchError(
            "Galileo error 4350: stale open workbench cleared — please retry booking."
        )

    # Extract locator code
    locator_code = None
    try:
        receipts = result.get("Reservation", {}).get("Receipt", []) or \
                   result.get("ReservationResponse", {}).get("Reservation", {}).get("Receipt", [])
        if receipts and isinstance(receipts, list):
            # Must be the Receipt whose Locator.source is "1G" — NOT
            # necessarily receipts[0]. Confirmed live 2026-09-15 (PNRs
            # EK176UOF99XA4 and 97LB9M): for NDC content, Travelport lists
            # the airline's own OrderId/VendorLocator receipts BEFORE the 1G
            # one, in no fixed order (EK put OrderId first then
            # VendorLocator; AI put VendorLocator first then OrderId — 1G
            # was always last for both, but never first). Blindly taking
            # receipts[0] grabbed the airline's own locator instead of the
            # actual 1G agency locator, which then made every later
            # buildfromlocator call (ticket issuance, cancellation) fail
            # with "RECORD LOCATOR DOES NOT EXIST" — Travelport's 1G lookup
            # correctly couldn't find a value that was never registered
            # under 1G in the first place. This was not a Travelport/account
            # issue; the real 1G locator was in the response all along.
            locator_1g = next(
                (r.get("Confirmation", {}).get("Locator", {}).get("value")
                 for r in receipts
                 if r.get("Confirmation", {}).get("Locator", {}).get("source") == "1G"),
                None
            )
            locator_code = locator_1g or receipts[0].get("Confirmation", {}).get("Locator", {}).get("value")

        if not locator_code:
            locator_code = (
                result.get("Reservation", {}).get("Locator", {}).get("value") or
                result.get("ReservationResponse", {}).get("Reservation", {}).get("Locator", {}).get("value") or
                result.get("locatorCode")
            )
    except Exception:
        pass

    if not locator_code:
        logger.error(f"PNR generation failed. Response: {result}")
        error_msg = None
        try:
            errors = (
                result.get("ReservationResponse", {}).get("Result", {}).get("Error", []) or
                result.get("Result", {}).get("Error", [])
            )
            if errors and isinstance(errors, list):
                error_msg = errors[0].get("Message")
        except Exception:
            pass
        if error_msg:
            raise ValueError(f"Travelport booking failed: {error_msg}")
        raise ValueError("Travelport did not return a PNR locator code.")

    # Cache the Offer local id/UUID from THIS commit response — confirmed
    # live 2026-09-16 (PNR HN50PK and 6 others, all NDC multi-city): the
    # commit response itself always embeds Offer[] (with the real "id" and
    # Identifier.value ticket issuance needs), but a later buildfromlocator
    # call at ticketing time frequently comes back with no Offer[] at all for
    # NDC content — every one of those ticket attempts then failed at the
    # Payment step ("OFFER ID/IDENTIFIER VALUES MUST MATCH...", SourceCode
    # 4179) purely because we had no OfferIdentifier to send, not because the
    # PNR itself was unticketable. Saving it now, while we know it, lets
    # issue_ticket() fall back to this instead of leaving it unset.
    offer_local_id = None
    offer_uuid = None
    offer_authority = None
    try:
        reservation = result.get("ReservationResponse", {}).get("Reservation", {}) or result.get("Reservation", {})
        offers = reservation.get("Offer", [])
        if offers:
            offer_local_id = offers[0].get("id")
            offer_uuid = offers[0].get("Identifier", {}).get("value")
            offer_authority = offers[0].get("Identifier", {}).get("authority")
    except Exception:
        pass

    logger.info(f"PNR generated successfully: {locator_code}")
    return {
        "locator_code": locator_code,
        "raw_response": result,
        "offer_local_id": offer_local_id,
        "offer_uuid": offer_uuid,
        "offer_authority": offer_authority
    }


# ── Booking Flow Helper (Steps 4-7 with stale-workbench auto-retry) ───────────

def run_booking_flow(raw_offering: dict, travelers: list, max_retries: int = 3, reference_payload_only: bool = False) -> dict:
    """
    Execute the full GDS booking flow (Steps 4-7) with automatic retry on
    Galileo error 4350 (COMMIT OR IGNORE RESERVATION WORKBENCH).

    Steps:
        3b. Confirm price via AirPrice (GDS offers only — see note below)
        4. Create workbench
        5. Add offer
        6. Add all travelers
        7. Commit → PNR

    NOTE on AirPrice (STEP 3b) — scoped to GDS only, per Travelport support
    feedback on submitted logs asking for a Price step between Search and
    Book: confirmed live on this account that calling AirPrice before booking
    an NDC offer (Air India AI2275, CMB-BOM-DXB, 2026-09-09) reliably wedges
    the PCC — every subsequent workbench commit, even a brand-new one,
    immediately fails with Galileo error 4350 (COMMIT OR IGNORE RESERVATION
    WORKBENCH), with no identifier in the error for the existing
    stale-workbench recovery logic to target, so retries never recover. GDS
    bookings showed no such issue in testing, and GDS is also where
    Travelport support's own log review found the unchecked failure
    ("0 Avail Closed") this step is meant to catch early. So: AirPrice runs
    for GDS content only; NDC/LCC content still skips straight to Add Offer
    as before. Do not extend this to NDC without first re-verifying against
    a live NDC booking and/or hearing back from Travelport support on the
    PCC-lock behavior above.

    NOTE on full-payload Add Offer for non-GDS content: per Travelport
    support's explicit request to include classOfService/
    AvailabilitySourceCode/boundFlightsInd (full-payload-only fields),
    add_offer_to_workbench() attempts the full payload for NDC/LCC content
    too, despite Travelport's docs saying it's "not supported for NDC." Its
    own internal fallback only catches a failure AT the Add Offer call
    itself. Confirmed live: a full-payload NDC Add Offer can pass cleanly
    (HTTP 200, no error) and then fail at Commit with a real fare-validation
    error ("FARE IS NOT AVAILABLE FOR INPUT CRITERIA", Qatar Airways
    QR659 CMB-DXB, 2026-09-09) — a clean failure (no PCC lock, unlike the
    AirPrice incident), but still a booking that reference payload would
    have completed successfully. So: on any non-stale-workbench ValueError
    for non-GDS content, if this attempt used the full payload, this flow
    retries ONCE more with a fresh workbench and force_reference_payload=True
    rather than surfacing that failure to the caller.

    Args:
        raw_offering (dict): The raw flight offer from the catalog search.
        travelers (list[dict]): List of passenger dicts.
        max_retries (int): Max full-flow attempts on stale workbench errors.

    Returns:
        dict: {"locator_code": str, "raw_response": dict}

    Raises:
        ValueError: if all retries are exhausted or a non-recoverable error occurs.
    """
    # One TraceId for every Travelport call this flow makes (create workbench
    # -> add offer -> add travelers -> commit, across all retry attempts) —
    # per Travelport's own guidance that TraceId correlates one flow's linked
    # calls rather than being unique per individual request.
    with flow_trace_id():
        # STEP 3b — AirPrice/Pricing, called for ALL content sources (GDS and
        # NDC) as of 2026-09-15, matching Travelport's own NDC certification
        # reference (TravelportNDC_6Aug/3_Pricing.txt calls this same step).
        #
        # Whether its result gets substituted into the later Add Offer call
        # differs by content source, per the certification references:
        #   - GDS: the priced transactionId (Travelport returns the original
        #     identifier with a "_PC" suffix) DOES replace
        #     CatalogProductOfferingsIdentifier for Add Offer — this was
        #     already proven necessary for GDS.
        #   - NDC: confirmed from Travelport's own reference (5_addoffer.txt)
        #     that Add Offer's top-level CatalogProductOfferingsIdentifier
        #     stays the ORIGINAL search-time id even after Pricing runs —
        #     Pricing's own returned identifiers are only used for the
        #     offer/product refs inside Pricing itself, never substituted
        #     into Add Offer. Live-tested substituting it anyway on
        #     2026-09-15: Add Offer failed with "OFFER ID AND/OR PRODUCT ID
        #     DOES NOT EXIST" — confirms the reference's approach is correct
        #     and substitution must NOT happen for NDC. Do not change this
        #     without re-testing live first.
        leg_offerings = _get_leg_offerings(raw_offering)
        is_gds = all(leg.get("fare_source") == "GDS" for leg in leg_offerings)
        priced_transaction_id = confirm_price(raw_offering)
        if is_gds:
            for leg in leg_offerings:
                leg["CatalogProductOfferingsIdentifier"] = priced_transaction_id

        # See docstring above: non-GDS content attempts the full-payload Add
        # Offer first (for classOfService/etc.); if that gets past Add Offer
        # but fails later at Commit, this flips to True for one whole-flow
        # retry on a fresh workbench with reference payload forced.
        force_reference_payload = reference_payload_only
        already_fell_back_to_reference = False

        for attempt in range(1, max_retries + 1):
            workbench_id = None
            try:
                # STEP 4
                workbench_id = create_workbench()
                logger.info(f"[Flow attempt {attempt}/{max_retries}] Workbench: {workbench_id}")

                # STEP 5
                add_offer_to_workbench(workbench_id, raw_offering, force_reference_payload=force_reference_payload)

                # STEP 6 — Travelers must appear in this exact passenger-type
                # sequence: Adult, Infant, Child (both the GDS and NDC
                # certification reference logs agree on this order).
                #
                # Both GDS and NDC now send ONE combined TravelerListRequest
                # (.../travelers/list) per Travelport's explicit reply on the
                # certification logs we submitted: "For adding passenger will
                # suggest using single request only as it was done in the
                # certification flow of GDS." This supersedes the individual-
                # call approach NDC used before (reverted back to that on
                # 2026-09-23 per an earlier, different reference set) — this
                # latest guidance is the current standard for both content
                # sources.
                passenger_type_order = {"ADT": 0, "INF": 1, "CNN": 2}
                ordered_travelers = sorted(
                    travelers,
                    key=lambda t: passenger_type_order.get(t.get("passenger_type", "ADT"), 99)
                )
                add_travelers_to_workbench(workbench_id, ordered_travelers, is_gds=is_gds)

                # STEP 6b — Add Travel Agency, GDS content only (matches the
                # GDS certification reference, which includes this step; the
                # NDC certification reference does not call it at all, and per
                # Travelport docs it's only mandatory for AF/KL NDC bookings —
                # not extending to other NDC content without evidence it's
                # needed there, same caution as AirPrice's GDS-only scoping).
                if is_gds:
                    add_travel_agency_to_workbench(workbench_id)

                # STEP 7
                return commit_workbench(workbench_id)

            except StaleWorkbenchError as e:
                logger.warning(
                    f"[Flow attempt {attempt}/{max_retries}] Stale workbench error caught: {e}. "
                    f"{'Retrying...' if attempt < max_retries else 'All retries exhausted.'}"
                )
                if attempt < max_retries:
                    time.sleep(2)  # Brief pause before retry
                    continue
                raise ValueError(
                    f"Travelport booking failed after {max_retries} attempts due to persistent "
                    "stale workbench error (4350 COMMIT OR IGNORE RESERVATION WORKBENCH). "
                    "Please wait a moment and try again."
                )
            except ValueError as e:
                # A real (non-stale-workbench) failure — e.g. commit_workbench's
                # "did not return a PNR locator code" for a rejected fare.
                if workbench_id:
                    logger.warning(f"Error occurred during booking flow. Discarding workbench {workbench_id}...")
                    try:
                        discard_workbench(workbench_id)
                    except Exception as discard_ex:
                        logger.warning(f"Failed to discard workbench: {discard_ex}")
                if not is_gds and not force_reference_payload and not already_fell_back_to_reference and attempt < max_retries:
                    logger.warning(
                        f"[Flow attempt {attempt}/{max_retries}] Full-payload booking attempt failed for "
                        f"non-GDS content ({e}); retrying with reference payload forced (Travelport docs: "
                        "full payload not supported for NDC)."
                    )
                    force_reference_payload = True
                    already_fell_back_to_reference = True
                    continue
                raise
            except Exception as e:
                # If any other error occurs, we must discard the workbench we just created
                # so that it doesn't stay open and lock the GDS PCC session!
                if workbench_id:
                    logger.warning(f"Error occurred during booking flow. Discarding workbench {workbench_id}...")
                    try:
                        discard_workbench(workbench_id)
                    except Exception as discard_ex:
                        logger.warning(f"Failed to discard workbench: {discard_ex}")
                raise e

        raise ValueError("Booking flow failed after all retries.")


# ── NDC Instant Pay: book and ticket in the same workbench ───────────────────
# Travelport's NDC-only Instant Pay workflow: create workbench → add offer →
# add travelers → form of payment → payment → commit with Issuance=Ticket, all
# in ONE session. Needed for Emirates, whose post-commit (buildfromlocator)
# workbench carries no offer, so the normal book-then-ticket path cannot pay.

class InstantPayError(ValueError):
    pass


def _errors_of(result: dict) -> list:
    body = result.get("ReservationResponse", result)
    return (body.get("Result") or {}).get("Error") or []


def run_instant_pay_flow(raw_offering: dict, travelers: list, max_attempts: int = 3) -> dict:
    """
    Returns {"locator_code", "raw_response", "ticket_numbers": [(ptc, number)...],
    "total_fare", "currency"} for a booked AND ticketed NDC reservation.
    Raises InstantPayError if the airline refuses the offer or no ticket is issued;
    any PNR left on hold by a failed attempt is cancelled.
    """
    import uuid
    from services.ticket_service import cancel_reservation

    passenger_type_order = {"ADT": 0, "INF": 1, "CNN": 2}
    ordered = sorted(travelers, key=lambda t: passenger_type_order.get(t.get("passenger_type", "ADT"), 99))
    last_error = "unknown error"

    with flow_trace_id():
        confirm_price(raw_offering)
        for attempt in range(1, max_attempts + 1):
            workbench_id = create_workbench()
            logger.info(f"[Instant Pay {attempt}/{max_attempts}] Workbench: {workbench_id}")
            held_locator = None
            try:
                add_offer_to_workbench(workbench_id, raw_offering, force_reference_payload=True)
                add_travelers_to_workbench(workbench_id, ordered, is_gds=False)

                wb = get_workbench_details(workbench_id)
                wres = wb.get("ReservationResponse", {}).get("Reservation", {}) or wb.get("Reservation", {}) or wb
                offers = wres.get("Offer") or []
                traveler_refs = [{"passengerTypeCode": t.get("passengerTypeCode"), "id": t.get("id")}
                                 for t in wres.get("Traveler", []) or []]
                if not offers or len(traveler_refs) != len(ordered):
                    raise InstantPayError("Travelport workbench is missing the offer or travelers")
                offer = offers[0]

                fop_uuid = str(uuid.uuid4()).upper()
                _api_post(TravelportEndpoints.add_fop_to_workbench(workbench_id), {
                    "FormOfPaymentCash": {"id": "formOfPayment_1", "FormOfPaymentRef": "formOfPayment_1",
                                          "Identifier": {"authority": "Travelport", "value": fop_uuid}}
                }, session_id=workbench_id)
                wb2 = get_workbench_details(workbench_id)
                r2 = wb2.get("ReservationResponse", {}).get("Reservation", {}) or wb2.get("Reservation", {}) or wb2
                stored = (r2.get("FormOfPayment") or [{}])[0]
                fop_id = stored.get("Identifier", {}).get("value") or fop_uuid

                price = offer.get("Price") or {}
                total = float(price.get("TotalPrice") or 0)
                currency = (price.get("CurrencyCode") or {}).get("value") or "LKR"
                pay = _api_post(TravelportEndpoints.add_payment_to_workbench(workbench_id), {
                    "Payment": {
                        "id": "payment_1",
                        "Identifier": {"authority": "Travelport", "value": str(uuid.uuid4()).upper()},
                        "Amount": {"value": total, "code": currency, "minorUnit": 2,
                                   "currencySource": "Supplier", "approximateInd": True},
                        "FormOfPaymentIdentifier": {"id": "formOfPayment_1", "FormOfPaymentRef": "formOfPayment_1",
                                                    "Identifier": {"authority": "Travelport", "value": fop_id}},
                        "OfferIdentifier": [{"id": offer.get("id"), "offerRef": offer.get("id"),
                                             "Identifier": offer.get("Identifier")}],
                        "TravelerIdentifierRef": traveler_refs,
                    }
                }, session_id=workbench_id)
                pay_errors = (pay.get("PaymentResponse", {}).get("Result") or {}).get("Error") or []
                if pay_errors:
                    raise InstantPayError("; ".join(f"{e.get('Message')} ({e.get('SourceCode')})" for e in pay_errors))

                commit_url = (f"{TravelportConfig.base_path()}/air/book/reservation/reservations/"
                              f"{workbench_id}?Issuance=Ticket&DocumentValue=Retain")
                result = _api_post(commit_url, "", session_id=workbench_id)
                res = result.get("ReservationResponse", {}).get("Reservation", {}) or result.get("Reservation", {}) or {}

                ref_ptc = {t["id"]: t["passengerTypeCode"] for t in traveler_refs}
                tickets = []
                for rc in res.get("Receipt", []) or []:
                    for doc in rc.get("Document", []) or []:
                        if doc.get("@type") == "DocumentTicket" and doc.get("Number"):
                            ref = doc.get("TravelerIdentifierRef") or {}
                            tickets.append((ref.get("passengerTypeCode") or ref_ptc.get(ref.get("id")) or "ADT", doc["Number"]))
                for rc in res.get("Receipt", []) or []:
                    loc = (rc.get("Confirmation") or {}).get("Locator") or {}
                    if loc.get("source") == "1G":
                        held_locator = loc.get("value")

                if tickets and held_locator:
                    return {"locator_code": held_locator, "raw_response": result, "ticket_numbers": tickets,
                            "total_fare": total, "currency": currency}

                errs = _errors_of(result)
                last_error = "; ".join(f"{e.get('SourceID')}: {e.get('Message')} ({e.get('SourceCode')})" for e in errs) \
                    or "commit returned no ticket"
                retryable = any(str(e.get("SourceCode")) == "4243" for e in errs)
                logger.warning(f"[Instant Pay {attempt}/{max_attempts}] not ticketed: {last_error}")
            except InstantPayError as e:
                last_error = str(e)
                retryable = True
                logger.warning(f"[Instant Pay {attempt}/{max_attempts}] {last_error}")

            if held_locator:
                try:
                    cancel_reservation(held_locator)
                    logger.info(f"Cancelled unticketed PNR {held_locator}")
                except Exception as ce:
                    logger.warning(f"Could not cancel unticketed PNR {held_locator}: {ce}")
            else:
                try:
                    discard_workbench(workbench_id)
                except Exception:
                    pass
            if not retryable:
                break
            time.sleep(2)

    raise InstantPayError(f"Instant Pay ticketing failed: {last_error}")


# ── STEP 10: Live Seat Map Query ──────────────────────────────────────────────

def get_seat_map(workbench_id: str, offer_id: str) -> dict:
    """
    Query the Travelport API for the live seat map of the active workbench session.
    """
    logger.info(f"Querying seat map for workbench {workbench_id}, offer {offer_id}...")
    
    url = f"{TravelportConfig.base_path()}/air/search/seat/catalogofferingsancillaries/seatavailabilities"
    payload = {
      "CatalogOfferingsQuerySeatAvailability": {
        "SeatAvailabilityOfferings": {
          "@type": "SeatAvailabilityOfferingsBuildFromReservationWorkbench",
          "BuildFromReservationWorkbench": {
            "ReservationIdentifier": {
              "Identifier": {
                "value": workbench_id,
                "authority": "Travelport"
              }
            },
            "OfferIdentifier": {
              "Identifier": {
                "value": offer_id,
                "authority": "Travelport"
              }
            }
          }
        }
      }
    }
    
    # Use retry helper — seat map call also frequently 504s on sandbox
    result = _api_post_with_retry(url, payload, session_id=workbench_id)
    return parse_seat_map_response(result)


# ── STEP 10b: Live Ancillary (Baggage/Meal) Catalogue Query ──────────────────

def get_ancillary_offers(workbench_id: str, offer_id: str) -> list[dict]:
    """
    Query the Travelport Ancillary Shop API for the bookable non-seat
    ancillaries (extra baggage, meals, etc.) available on the active
    workbench session. Mirrors get_seat_map()'s request shape exactly —
    same BuildFromReservationWorkbench discriminator pattern, same
    ReservationIdentifier/OfferIdentifier — against the general ancillary
    endpoint instead of the seat-specific one.
    """
    logger.info(f"Querying ancillary offers for workbench {workbench_id}, offer {offer_id}...")

    url = f"{TravelportConfig.base_path()}/air/ancillaryshop/catalogofferingsancillaries"
    payload = {
      "CatalogOfferingsQueryAncillaries": {
        "AncillaryOfferings": {
          "@type": "AncillaryOfferingsBuildFromReservationWorkbench",
          "BuildFromReservationWorkbench": {
            "ReservationIdentifier": {
              "Identifier": {
                "value": workbench_id,
                "authority": "Travelport"
              }
            },
            "OfferIdentifier": {
              "Identifier": {
                "value": offer_id,
                "authority": "Travelport"
              }
            }
          }
        }
      }
    }

    # Use retry helper — same sandbox 504 behavior seen on seat map/other calls.
    result = _api_post_with_retry(url, payload, session_id=workbench_id)
    return parse_ancillary_response(result)


def parse_ancillary_response(result: dict) -> list[dict]:
    """
    Parse the raw Travelport CatalogOfferingsAncillaryListResponse (ancillary
    shop variant) into a flat list of purchasable ancillary items for the
    frontend. Each item: service_type (e.g. "BG" baggage, "ML" meal), name,
    description, price, currency, and the ids needed to add it to the
    workbench later (catalog_offering_id, product_id).
    """
    resp = result.get("CatalogOfferingsAncillaryListResponse", {})

    errors = resp.get("Result", {}).get("Error", [])
    if errors and not resp.get("CatalogOfferingsID"):
        raise ValueError(f"Travelport Ancillary Shop Error: {errors[0].get('Message')}")

    pricing_settings = get_pricing_settings()
    items: list[dict] = []

    for flight_group in resp.get("CatalogOfferingsID", []) or []:
        # Needed later to actually purchase the ancillary (Ancillary Book,
        # BuildAncillaryOffersFromCatalogOfferings) — per Travelport's own
        # API reference (support.travelport.com APIRef_AncillaryBook):
        #   CatalogOfferingsIdentifier.id <- CatalogOfferingsID/id
        #   TravelerIdentifierRef.id      <- CatalogOfferingsID/TravelerIdentifierRef/id
        # Confirmed live that TravelerIdentifierRef is NOT the same value as
        # CatalogOfferingsID/id (e.g. "CT1") — an earlier version of this
        # code conflated the two, which is why Ancillary Book consistently
        # failed with "OFFER IDENTIFIER IS NOT VALID"/"RESERVATION OR OFFER
        # ID ARE NOT VALID". TravelerIdentifierRef is its own list field,
        # one entry per traveler (e.g. [{"id": "travelerRefId_1", ...}]).
        catalog_offerings_id = flight_group.get("id")
        tir_list = flight_group.get("TravelerIdentifierRef") or []
        traveler_identifier_ref = (tir_list[0] or {}).get("id") if tir_list else None

        for offering in flight_group.get("CatalogOffering", []) or []:
            catalog_offering_id = offering.get("id")
            price_detail = offering.get("Price", {})
            price = float(price_detail.get("TotalPrice", 0) or 0)
            price, _ = apply_markup(price, pricing_settings, "ancillary")
            currency = price_detail.get("CurrencyCode", {}).get("value", "LKR")

            for prod_opt in offering.get("ProductOptions", []) or []:
                for prod in prod_opt.get("Product", []) or []:
                    if prod.get("@type") != "ProductAncillary":
                        continue
                    product_id = prod.get("id")
                    ancillary = prod.get("Ancillary", {}) or {}
                    # Confirmed live shape (Travelport Ancillary Shop, GDS
                    # content): the human-readable name/category/ssr code
                    # live in Ancillary.Description[0], not a top-level
                    # "commercialName"/"ServiceDetails" field.
                    desc = (ancillary.get("Description") or [{}])[0]
                    weight = None
                    for m in ancillary.get("Measurement", []) or []:
                        if m.get("measurementType") == "Weight":
                            weight = f"{m.get('value')} {m.get('unit', '')}".strip()
                            break
                    name = desc.get("value") or "Ancillary Service"
                    if weight:
                        description = f"{name} ({weight})"
                    elif desc.get("code") == "BG":
                        # Confirmed live: some baggage SSRs (e.g. XWBG "EXCESS
                        # BAGGAGE WEIGHT") are a generic per-kg overweight
                        # charge rather than a fixed package like "UPTO33LB
                        # 15KG BAGGAGE" — Travelport gives no fixed Measurement
                        # for these, so say so explicitly instead of silently
                        # showing no weight at all (looked like missing data).
                        description = f"{name} — priced per excess kg, not a fixed weight package"
                    else:
                        description = name
                    items.append({
                        "catalog_offerings_id": catalog_offerings_id,
                        "traveler_identifier_ref": traveler_identifier_ref,
                        "catalog_offering_id": catalog_offering_id,
                        "product_id": product_id,
                        "service_type": ancillary.get("@type"),
                        "category_code": desc.get("code"),
                        "ssr_code": desc.get("ssrCode"),
                        "name": name,
                        "description": description,
                        "price": price,
                        "currency": currency,
                    })

    return items


def book_ancillary_offer(workbench_id: str, ancillary: dict, quantity: int = 1) -> dict:
    """
    Purchase one ancillary item (extra baggage, meal, etc.) that was returned
    by get_ancillary_offers()/parse_ancillary_response() — i.e. `ancillary`
    must be one of the dicts from that list, carrying catalog_offerings_id,
    traveler_identifier_ref, catalog_offering_id and product_id.

    Calls Travelport's Ancillary Book API (BuildAncillaryOffersFromCatalog
    Offerings). Confirmed correct against Travelport's own API reference
    (support.travelport.com APIRef_AncillaryBook) after live testing showed
    the identifiers below are the ones that get an actual GDS-side response
    (SourceID "1G") instead of a client-request-shape rejection:

      CatalogOfferingsIdentifier.id         <- catalog_offerings_id (CatalogOfferingsID/id, e.g. "CT1")
      CatalogOfferingsIdentifier.Identifier <- {value: workbench_id, authority: "Travelport"} —
          this account's Ancillary Shop response has no nested
          CatalogOffering/Identifier object to copy (unlike Travelport's own
          docs example), and omitting this sub-object entirely gets the
          request rejected at the API gateway before it reaches the GDS
          (SourceID "API", not "1G") — so the workbench id is sent here as
          the one genuinely valid Travelport-scoped identifier available.
      CatalogOfferingIdentifier.id  <- catalog_offering_id (CatalogOfferingsID/CatalogOffering/id, e.g. "anc_off1")
      ProductIdentifier.id          <- product_id (.../ProductOptions/Product/id, e.g. "an1")
      TravelerIdentifierRef.id      <- traveler_identifier_ref (CatalogOfferingsID/TravelerIdentifierRef/id,
          e.g. "travelerRefId_1" — NOT the same value as catalog_offerings_id;
          conflating the two was the original bug).

    NOTE: even with these corrected identifiers, Travelport still returns a
    business-logic error ("NO MATCHING SSR SEGMENT") for every GDS carrier/
    route tested — confirmed NOT caused by request shape (ruled out: airline,
    departure date, seat-map ordering, AirPrice/unpriced-segment conversion —
    the latter also uncovered a reproducible Travelport-side 500 on their own
    buildfromunpricedsegments + Ancillary Shop combination). This needs
    Travelport support to resolve; do not re-guess at identifier combinations
    without new evidence from them — see the dated findings in
    TRAVELPORT_ANCILLARY_BOOK_SUPPORT_NOTES (support request draft).
    """
    logger.info(f"Booking ancillary (catalog_offering_id={ancillary.get('catalog_offering_id')}) on workbench {workbench_id}...")

    url = f"{TravelportConfig.base_path()}/air/book/airoffer/reservationworkbench/{workbench_id}/offers/buildancillaryoffersfromcatalogofferings"
    payload = {
        "@type": "OfferQueryBuildAncillaryOffersFromCatalogOfferings",
        "BuildAncillaryOffersFromCatalogOfferings": [
            {
                "@type": "BuildAncillaryOffersFromCatalogOfferings",
                "CatalogOfferingsIdentifier": {
                    "id": ancillary.get("catalog_offerings_id"),
                    "Identifier": {"value": workbench_id, "authority": "Travelport"},
                },
                "CatalogOfferingIdentifier": {"id": ancillary.get("catalog_offering_id")},
                "ProductIdentifier": {"id": ancillary.get("product_id")},
                "TravelerIdentifierRef": {"id": ancillary.get("traveler_identifier_ref")},
                "Quantity": quantity,
            }
        ],
    }
    result = _api_post_with_retry(url, payload, session_id=workbench_id)
    _raise_if_error(result, "Ancillary Book")
    return result


def parse_seat_map_response(result: dict) -> list[dict]:
    """
    Parse the raw Travelport seat map response into a clean structure for the
    frontend. Returns a LIST of seat maps, one per flight/leg — a round-trip
    workbench returns one ReferenceListSeatingChart entry AND one
    CatalogOfferingsID entry PER FLIGHT (index 0 = outbound/first leg,
    index 1 = return/second leg, matching the order the offer's products
    were added to the workbench). A one-way booking's list always has
    exactly one entry.

    Confirmed live: the earlier version only ever read index [0] of both
    lists, silently dropping the return leg's seat map for every round-trip
    booking — this is the fix for that.
    """
    resp = result.get("CatalogOfferingsAncillaryListResponse", {})

    # 1. Parse ALL Seating Charts from ReferenceList (one per flight)
    ref_list = resp.get("ReferenceList", [])
    seating_charts: list[dict] = []
    for ref in ref_list:
        if ref.get("@type") == "ReferenceListSeatingChart":
            seating_charts.extend(ref.get("SeatingChart", []))

    if not seating_charts:
        logger.warning("No seating chart found in Travelport response.")
        errors = resp.get("Result", {}).get("Error", [])
        if errors:
            raise ValueError(f"Travelport Seating Chart Error: {errors[0].get('Message')}")
        raise ValueError("Travelport did not return a seating chart for this flight.")

    # 2. Parse ALL per-flight CatalogOffering lists (seat pricing/availability)
    traveler_flights = resp.get("CatalogOfferingsID", [])
    pricing_settings = get_pricing_settings()

    seat_maps = []
    for i, seating_chart in enumerate(seating_charts):
        catalog_offerings = traveler_flights[i].get("CatalogOffering", []) if i < len(traveler_flights) else []
        seat_maps.append(_parse_single_seating_chart(seating_chart, catalog_offerings, pricing_settings))
    return seat_maps


def _parse_single_seating_chart(seating_chart: dict, catalog_offerings: list, pricing_settings: dict) -> dict:
    """Parse one flight's seating chart + its catalog offerings (pricing/
    availability) into the flat structure the frontend seat picker expects.
    Extracted from parse_seat_map_response so it can be called once per
    flight/leg on a round-trip booking."""
    cabin = seating_chart.get("Cabin", [])[0] if seating_chart.get("Cabin") else {}

    # 2. Parse Layout (Rows range and Columns layout)
    layout_list = cabin.get("Layout", [])
    start_row = 1
    end_row = 30
    columns = []
    
    for lay in layout_list:
        if "startRow" in lay:
            start_row = lay.get("startRow", start_row)
            end_row = lay.get("endRow", end_row)
        elif "value" in lay:
            col_val = lay.get("value")
            pos_list = lay.get("position", [])
            pos = "Center"
            if "W" in pos_list:
                pos = "Window"
            elif "A" in pos_list:
                pos = "Aisle"
            columns.append({
                "value": col_val,
                "position": pos
            })
            
    # 3. Parse Catalog Offerings for seat pricing & availability
    reserved_seats = set()
    available_seats = set()
    seat_prices = {}
    seat_currencies = {}
    seat_brands = {}

    for offering in catalog_offerings:
        price_detail = offering.get("Price", {})
        price = float(price_detail.get("TotalPrice", 0))
        price, _ = apply_markup(price, pricing_settings, "seat")
        currency = price_detail.get("CurrencyCode", {}).get("value", "LKR")
        
        brand_name = "STANDARD SEAT"
        try:
            prod_opts = offering.get("ProductOptions", [])
            if prod_opts:
                prods = prod_opts[0].get("Product", [])
                if prods:
                    brand_name = prods[0].get("Brand", {}).get("name", brand_name)
        except Exception:
            pass
            
        for prod_opt in offering.get("ProductOptions", []):
            for prod in prod_opt.get("Product", []):
                if prod.get("@type") == "ProductSeatAvailability":
                    for avail in prod.get("SeatAvailability", []):
                        status = avail.get("seatAvailabilityStatus")
                        seats_list = avail.get("value", [])
                        for seat_num in seats_list:
                            if status == "Reserved":
                                reserved_seats.add(seat_num)
                            elif status == "Available":
                                available_seats.add(seat_num)
                                seat_prices[seat_num] = price
                                seat_currencies[seat_num] = currency
                                seat_brands[seat_num] = brand_name
                                
    # 4. Construct Row by Row layout
    parsed_rows = []
    rows_list = cabin.get("Row", [])
    for row in rows_list:
        row_label = row.get("label")
        seats = []
        for space in row.get("Space", []):
            col = space.get("location")
            seat_num = f"{row_label}{col}"
            characteristics = space.get("Characteristic", [])
            
            # Check if it is a seat (it has chair characteristic "CH", or matches layout columns)
            is_seat = "CH" in characteristics or col in [c["value"] for c in columns]
            if not is_seat:
                seats.append({
                    "col": col,
                    "type": "empty",
                    "status": "empty",
                    "seat_number": "",
                    "price": 0,
                    "currency": "LKR"
                })
                continue
                
            seat_type = "Standard"
            if "FC" in characteristics:
                seat_type = "Front Cabin"
            elif "E" in characteristics or "LE" in characteristics:
                seat_type = "Extra Legroom"
                
            status = "blocked"
            if seat_num in reserved_seats or "O" in characteristics:
                status = "occupied"
            elif seat_num in available_seats:
                status = "available"
                
            seats.append({
                "col": col,
                "seat_number": seat_num,
                "type": seat_brands.get(seat_num, seat_type),
                "status": status,
                "price": seat_prices.get(seat_num, 0.0),
                "currency": seat_currencies.get(seat_num, "LKR")
            })
            
        parsed_rows.append({
            "row_number": int(row_label) if row_label.isdigit() else row_label,
            "seats": seats
        })
        
    return {
        "start_row": start_row,
        "end_row": end_row,
        "columns": columns,
        "rows": parsed_rows
    }


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
from config.travelport_config import TravelportConfig
from config.api_endpoints import TravelportEndpoints
from services.auth_service import get_auth_headers, invalidate_token

logger = logging.getLogger(__name__)


def _api_post(url: str, payload: dict | str, session_id: str | None = None) -> dict:
    """Internal helper for POST requests with automatic token retry."""
    headers = get_auth_headers(session_id)
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT) as client:
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


def _api_delete(url: str, session_id: str | None = None) -> None:
    """Internal helper for DELETE requests (used to ignore/cancel a stale workbench)."""
    headers = get_auth_headers(session_id)
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT) as client:
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
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT) as client:
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


def add_offer_to_workbench(workbench_id: str, raw_offering: dict) -> dict:
    """
    STEP 5: Add the selected flight offer(s) to the workbench.

    Args:
        workbench_id (str): Workbench ID from STEP 4
        raw_offering (dict): Either a single-leg raw offer object from the search
            response (one-way / multi-city), or a round-trip combined object shaped
            {"outbound": <raw offering>, "inbound": <raw offering>} produced by
            search_service.pair_round_trip_offers. Both legs share the same
            CatalogProductOfferingsIdentifier since they come from the same search.

    Returns:
        dict: Updated workbench item response
    """
    logger.info(f"Adding offer to workbench {workbench_id}...")

    is_round_trip = isinstance(raw_offering, dict) and "outbound" in raw_offering and "inbound" in raw_offering
    leg_offerings = [raw_offering["outbound"], raw_offering["inbound"]] if is_round_trip else [raw_offering]

    catalog_offerings_id = leg_offerings[0].get("CatalogProductOfferingsIdentifier", "")
    offering_selections = [_build_offering_selection(leg) for leg in leg_offerings]

    payload = {
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

    url = TravelportEndpoints.add_offer_to_workbench(workbench_id)
    # Subsequent calls must include the workbench_id as session_id
    # Use retry helper — this call frequently 504s on the Travelport sandbox
    result = _api_post_with_retry(url, payload, session_id=workbench_id)
    logger.info("Offer added to workbench.")
    return result


# ── STEP 6: Add Traveler(s) ───────────────────────────────────────────────────

def add_traveler_to_workbench(workbench_id: str, traveler: dict) -> dict:
    """
    STEP 6: Add passenger/traveler details to the workbench.

    Args:
        workbench_id (str): Workbench ID from STEP 4
        traveler (dict): Passenger info with keys:
            - first_name (str)
            - last_name (str)
            - date_of_birth (str)  YYYY-MM-DD
            - gender (str)         "Male" | "Female"
            - passport_number (str)
            - passport_expiry (str) YYYY-MM-DD
            - nationality (str)    ISO country code e.g. "LK"
            - email (str)
            - phone (str)

    Returns:
        dict: Updated workbench response
    """
    logger.info(f"Adding traveler to workbench {workbench_id}: {traveler.get('first_name')} {traveler.get('last_name')}")

    # Clean phone number and parse country code for Galileo 1G
    phone_clean = traveler.get("phone", "").replace("+", "").replace(" ", "").replace("-", "")
    country_code = "94"
    phone_num = phone_clean
    if phone_clean.startswith("94"):
        phone_num = phone_clean[2:]
    elif len(phone_clean) > 9:
        country_code = phone_clean[:-9]
        phone_num = phone_clean[-9:]

    payload = {
        "Traveler": {
            "@type": "Traveler",
            "gender": traveler.get("gender", "Male"),
            "birthDate": traveler.get("date_of_birth", ""),
            "passengerTypeCode": traveler.get("passenger_type", "ADT"),
            "PersonName": {
                "@type": "PersonNameDetail",
                "Prefix": "MR" if traveler.get("gender", "Male") == "Male" else "MRS",  # Prefix required for Galileo 1G validation
                "Given": traveler.get("first_name", "").upper(),
                "Surname": traveler.get("last_name", "").upper()
            },
            "Telephone": [
                {
                    "@type": "Telephone",
                    "countryAccessCode": country_code,
                    "phoneNumber": phone_num,
                    "role": "Mobile"
                }
            ],
            "Email": [
                {
                    "value": traveler.get("email", "")
                }
            ],
            "TravelDocument": [
                {
                    "@type": "TravelDocumentDetail",
                    "docNumber": traveler.get("passport_number", ""),
                    "docType": "Passport",
                    "expireDate": traveler.get("passport_expiry", ""),
                    "issueCountry": traveler.get("nationality", "LK"),
                    "birthDate": traveler.get("date_of_birth", ""),
                    "Gender": traveler.get("gender", "Male"),
                    "PersonName": {
                        "@type": "PersonName",
                        "Given": traveler.get("first_name", "").upper(),
                        "Surname": traveler.get("last_name", "").upper()
                    }
                }
            ]
        }
    }

    url = TravelportEndpoints.update_workbench(workbench_id)
    # Traveler addition in v11 is a POST call
    result = _api_post(url, payload, session_id=workbench_id)
    logger.info("Traveler added to workbench.")
    return result


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
            locator_code = receipts[0].get("Confirmation", {}).get("Locator", {}).get("value")
            
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

    logger.info(f"PNR generated successfully: {locator_code}")
    return {
        "locator_code": locator_code,
        "raw_response": result
    }


# ── Booking Flow Helper (Steps 4-7 with stale-workbench auto-retry) ───────────

def run_booking_flow(raw_offering: dict, travelers: list, max_retries: int = 3) -> dict:
    """
    Execute the full GDS booking flow (Steps 4-7) with automatic retry on
    Galileo error 4350 (COMMIT OR IGNORE RESERVATION WORKBENCH).

    Steps:
        4. Create workbench
        5. Add offer
        6. Add all travelers
        7. Commit → PNR

    Args:
        raw_offering (dict): The raw flight offer from the catalog search.
        travelers (list[dict]): List of passenger dicts.
        max_retries (int): Max full-flow attempts on stale workbench errors.

    Returns:
        dict: {"locator_code": str, "raw_response": dict}

    Raises:
        ValueError: if all retries are exhausted or a non-recoverable error occurs.
    """
    for attempt in range(1, max_retries + 1):
        workbench_id = None
        try:
            # STEP 4
            workbench_id = create_workbench()
            logger.info(f"[Flow attempt {attempt}/{max_retries}] Workbench: {workbench_id}")

            # STEP 5
            add_offer_to_workbench(workbench_id, raw_offering)

            # STEP 6 — Travelport GDS certification requires travelers to be
            # added in this exact passenger-type sequence: Adult, Infant, Child.
            passenger_type_order = {"ADT": 0, "INF": 1, "CNN": 2}
            ordered_travelers = sorted(
                travelers,
                key=lambda t: passenger_type_order.get(t.get("passenger_type", "ADT"), 99)
            )
            for t in ordered_travelers:
                add_traveler_to_workbench(workbench_id, t)

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


def parse_seat_map_response(result: dict) -> dict:
    """
    Parse the raw Travelport seat map response into a clean structure for the frontend.
    """
    resp = result.get("CatalogOfferingsAncillaryListResponse", {})
    
    # 1. Parse Seating Chart from ReferenceList
    ref_list = resp.get("ReferenceList", [])
    seating_chart = None
    for ref in ref_list:
        if ref.get("@type") == "ReferenceListSeatingChart":
            charts = ref.get("SeatingChart", [])
            if charts:
                seating_chart = charts[0]
                break
                
    if not seating_chart:
        logger.warning("No seating chart found in Travelport response.")
        errors = resp.get("Result", {}).get("Error", [])
        if errors:
            raise ValueError(f"Travelport Seating Chart Error: {errors[0].get('Message')}")
        raise ValueError("Travelport did not return a seating chart for this flight.")
        
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
    catalog_offerings = []
    traveler_flights = resp.get("CatalogOfferingsID", [])
    if traveler_flights:
        catalog_offerings = traveler_flights[0].get("CatalogOffering", [])
        
    reserved_seats = set()
    available_seats = set()
    seat_prices = {}
    seat_currencies = {}
    seat_brands = {}
    
    for offering in catalog_offerings:
        price_detail = offering.get("Price", {})
        price = float(price_detail.get("TotalPrice", 0))
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


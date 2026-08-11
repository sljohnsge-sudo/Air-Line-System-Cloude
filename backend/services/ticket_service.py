"""
services/ticket_service.py
===========================
STEPS 8 & 9 — Retrieve PNR Details & Issue Ticket
Handles retrieving reservation info from Travelport and issuing
electronic tickets after PNR generation.

To modify ticket issuance or retrieval logic: edit only this file.
"""

import httpx
import logging
from config.travelport_config import TravelportConfig
from config.api_endpoints import TravelportEndpoints
from services.auth_service import get_auth_headers, invalidate_token
from services.search_service import parse_iso_duration, parse_naive_datetime, minutes_to_iso_duration, IATA_AIRLINE_NAMES

logger = logging.getLogger(__name__)


def _api_get(url: str) -> dict:
    """Internal helper for GET requests with automatic token retry."""
    headers = get_auth_headers()
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT) as client:
        response = client.get(url, headers=headers)
        if response.status_code == 401:
            invalidate_token()
            response = client.get(url, headers=get_auth_headers())
        response.raise_for_status()
        return response.json()


def _api_post(url: str, payload: dict | str) -> dict:
    """Internal helper for POST requests with automatic token retry."""
    headers = get_auth_headers()
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT) as client:
        kwargs = {"json": payload} if not isinstance(payload, str) else {"content": payload}
        response = client.post(url, headers=headers, **kwargs)
        if response.status_code == 401:
            invalidate_token()
            response = client.post(url, headers=get_auth_headers(), **kwargs)
        response.raise_for_status()
        return response.json()


# ── STEP 8: Retrieve Reservation ──────────────────────────────────────────────

def clean_passenger_name(given: str, surname: str) -> str:
    """
    Normalizes legacy GDS passenger name formatting.
    Galileo GDS appends prefixes/titles like 'MR' or 'MRS' to the end of the Given name 
    (e.g., Given: 'SANKA MR', Surname: 'LASITHA'), which creates 'SANKA MR LASITHA'.
    This function cleans it to 'MR SANKA LASITHA'.
    """
    full_raw = f"{given} {surname}".strip().upper()
    parts = full_raw.split()
    
    prefixes = ["MR", "MRS", "MS", "MSTR", "DR", "PROF", "MISS"]
    found_prefix = ""
    clean_parts = []
    
    for part in parts:
        matched_prefix = ""
        for pref in prefixes:
            if part == pref:
                matched_prefix = pref
                break
            elif len(part) > len(pref) and part.endswith(pref):
                matched_prefix = pref
                part = part[:-len(pref)]
                break
        
        if matched_prefix:
            found_prefix = matched_prefix
            if part and part != matched_prefix:
                clean_parts.append(part)
        else:
            clean_parts.append(part)
            
    if found_prefix:
        return f"{found_prefix} {' '.join(clean_parts)}".upper()
    return " ".join(clean_parts).upper()


def retrieve_reservation(locator_code: str) -> dict:
    """
    STEP 8: Retrieve full PNR / itinerary details by locator code.

    Args:
        locator_code (str): The PNR locator code from STEP 7

    Returns:
        dict: Simplified ticket/reservation details suitable for the frontend popup
    """
    logger.info(f"Retrieving reservation: {locator_code}")

    url = TravelportEndpoints.retrieve_reservation(locator_code)
    raw = _api_get(url)

    return _parse_reservation(raw, locator_code)


def _parse_reservation_product(product: dict) -> dict | None:
    """
    Parse a single Offer.Product entry (one direction/leg of the PNR) into a
    flat leg dict — same shape as the flat top-level ticket fields, so a
    one-way PNR's single leg and each leg of a round-trip PNR look identical.
    """
    source_code = product.get("ContentSource", "GDS")
    fare_source = "LCC" if source_code == "APIPAC" else source_code

    flight_segments = product.get("FlightSegment", [])
    if not flight_segments:
        return None

    segments_list = []
    for f_seg in flight_segments:
        flight = f_seg.get("Flight", {})
        if not flight:
            continue
        carrier = flight.get("carrier", "")
        number = flight.get("number", "")
        dep = flight.get("Departure", {})
        arr = flight.get("Arrival", {})

        segments_list.append({
            "carrier": carrier,
            "carrier_name": IATA_AIRLINE_NAMES.get(carrier, carrier),
            "flight_number": f"{carrier}{number}",
            "departure_airport": dep.get("location", ""),
            "arrival_airport": arr.get("location", ""),
            "departure_time": (dep.get("date", "") + " " + dep.get("time", "")).strip(),
            "arrival_time": (arr.get("date", "") + " " + arr.get("time", "")).strip(),
            "duration": flight.get("duration", "")
        })

    if not segments_list:
        return None

    # Calculate layovers
    for i in range(len(segments_list) - 1):
        arr_dt = parse_naive_datetime(segments_list[i]["arrival_time"])
        dep_dt = parse_naive_datetime(segments_list[i + 1]["departure_time"])
        layover_minutes = int((dep_dt - arr_dt).total_seconds() / 60)
        segments_list[i]["layover_minutes"] = max(0, layover_minutes)

    first_seg = segments_list[0]
    last_seg = segments_list[-1]

    total_minutes = 0
    for seg in segments_list:
        total_minutes += parse_iso_duration(seg["duration"])
        total_minutes += seg.get("layover_minutes", 0)

    cabin_class = "Economy"
    fare_basis = ""
    passenger_flights = product.get("PassengerFlight", [])
    if passenger_flights:
        flight_products = passenger_flights[0].get("FlightProduct", [])
        if flight_products:
            fp = flight_products[0]
            cabin_class = fp.get("cabin", "Economy")
            fare_basis = fp.get("fareBasisCode", "")

    return {
        "fare_source": fare_source,
        "flight_number": first_seg["flight_number"],
        "airline": first_seg["carrier_name"],
        "airline_code": first_seg["carrier"],
        "departure_airport": first_seg["departure_airport"],
        "arrival_airport": last_seg["arrival_airport"],
        "departure_time": first_seg["departure_time"],
        "arrival_time": last_seg["arrival_time"],
        "duration": minutes_to_iso_duration(total_minutes),
        "segments": segments_list,
        "cabin_class": cabin_class,
        "fare_basis": fare_basis
    }


def _parse_reservation(raw: dict, locator_code: str) -> dict:
    """
    Parse the raw Travelport reservation response into a clean ticket record.

    Travelport returns multiple PNRs in Receipt[]:
    - Agency/GDS PNR: Receipt where Locator.source == '1G' (or the one created first)
    - Airline PNR:    Receipt where Locator.source == airline IATA code (e.g. 'FZ', 'UL')
    Real ticket number only comes from Ticket[] after issuance — never generated here.
    """
    ticket = {
        "locator_code": locator_code,      # Agency/GDS PNR (always available from STEP 7)
        "pnr": locator_code,               # Same as locator_code for clarity
        "agency_pnr": locator_code,        # The 1G / GDS agency PNR
        "airline_pnr": None,               # Airline's own PNR (from Receipt after booking)
        "airline_pnr_source": None,        # Airline IATA code that owns this PNR
        "ticket_number": None,             # Real 13-digit e-ticket number (only set after issuance)
        "status": "Confirmed",
        "passenger_name": "",
        "email": "",
        "flight_number": "",
        "airline": "",
        "airline_code": "",
        "departure_airport": "",
        "arrival_airport": "",
        "departure_time": "",
        "arrival_time": "",
        "duration": "",
        "cabin_class": "Economy",
        "fare_basis": "",
        "total_fare": 0.0,
        "currency": "USD",
        "booking_date": "",
        "seat_number": "",
        "baggage_allowance": "",
    }

    try:
        reservation = raw.get("Reservation", raw.get("ReservationResponse", {}).get("Reservation", {}))

        # ── Traveler ──────────────────────────────────────────────────────────
        travelers = reservation.get("Traveler", [])
        if travelers:
            t = travelers[0]
            name = t.get("PersonName", {})
            ticket["passenger_name"] = clean_passenger_name(name.get('Given', ''), name.get('Surname', ''))
            emails = t.get("ContactInformation", {}).get("Email", []) or t.get("Email", [])
            if emails:
                ticket["email"] = emails[0].get("value", "")

        # ── Offers → Flight details, Cabin, Price ─────────────────────────────
        offers = reservation.get("Offer", [])
        if offers:
            offer = offers[0]

            # A PNR has one Product per direction — one for one-way/multi-city legs,
            # two for round trip (outbound + return). Parse every product into a
            # "leg" dict; the first leg's fields are mirrored onto the flat
            # top-level ticket fields for backward compatibility, and the full
            # list is exposed as ticket["legs"] for round-trip display.
            products = offer.get("Product", [])
            legs = [_parse_reservation_product(p) for p in products]
            legs = [l for l in legs if l is not None]

            if legs:
                ticket["legs"] = legs
                first_leg = legs[0]
                last_leg = legs[-1]
                ticket["fare_source"] = first_leg["fare_source"]
                ticket["flight_number"] = first_leg["flight_number"]
                ticket["airline"] = first_leg["airline"]
                ticket["airline_code"] = first_leg["airline_code"]
                ticket["departure_airport"] = first_leg["departure_airport"]
                ticket["arrival_airport"] = last_leg["arrival_airport"]
                ticket["departure_time"] = first_leg["departure_time"]
                ticket["arrival_time"] = last_leg["arrival_time"]
                ticket["duration"] = first_leg["duration"]
                ticket["segments"] = first_leg["segments"]
                ticket["cabin_class"] = first_leg["cabin_class"]
                ticket["fare_basis"] = first_leg["fare_basis"]

            # Pricing
            price = offer.get("Price", {})
            if price:
                ticket["total_fare"] = float(price.get("TotalPrice", 0))
                ticket["currency"] = price.get("CurrencyCode", {}).get("value", "USD")

            # Baggage allowance from TermsAndConditionsFull
            terms = offer.get("TermsAndConditionsFull", [])
            for term in terms:
                baggage_list = term.get("BaggageAllowance", [])
                if baggage_list:
                    bag_text = baggage_list[0].get("Text", [])
                    if bag_text:
                        ticket["baggage_allowance"] = bag_text[0]
                    break

        # ── Ticket number (ONLY from Travelport — set after real issuance) ───
        # Never generate a mock here. If not issued, ticket_number stays None.
        issued_tickets = reservation.get("Ticket", [])
        if issued_tickets:
            ticket["ticket_number"] = issued_tickets[0].get("number", None)
            ticket["status"] = "Ticketed"

        # ── Agency PNR + Airline PNRs from Receipt[] ──────────────────────────
        # Receipt[] contains one entry per PNR:
        #   - GDS/agency PNR has source like '1G'
        #   - Airline PNR has source = airline IATA code (e.g. 'UL', 'FZ')
        receipts = reservation.get("Receipt", [])
        for receipt in receipts:
            confirmation = receipt.get("Confirmation", {})
            locator_info = confirmation.get("Locator", {})
            source = locator_info.get("source", "")
            value = locator_info.get("value", "")
            creation_date = locator_info.get("creationDate", "")

            if not creation_date:
                # Also look at receipt-level
                creation_date = receipt.get("creationDate", "")

            # GDS agency PNR (source is '1G' or matches our locator_code)
            if source == "1G" or value == locator_code:
                ticket["agency_pnr"] = value
                ticket["pnr"] = value
                ticket["locator_code"] = value
                if creation_date:
                    ticket["booking_date"] = creation_date
            elif source and value:
                # This is the airline's own PNR
                ticket["airline_pnr"] = value
                ticket["airline_pnr_source"] = source

        # ── Seat ──────────────────────────────────────────────────────────────
        seats = reservation.get("Seat", [])
        if seats:
            seat = seats[0]
            ticket["seat_number"] = f"{seat.get('row', '')}{seat.get('column', '')}"

    except Exception as e:
        logger.warning(f"Partial reservation parse error: {e}", exc_info=True)

    return ticket


# ── STEP 9: Issue Ticket ───────────────────────────────────────────────────────

def issue_ticket(locator_code: str) -> dict:
    """
    STEP 9: Issue the electronic ticket for a confirmed/held reservation.

    Correct Travelport v11 ticketing flow (per developer.travelport.com/apis/flights/ticketing):
      1. POST buildfromlocator  — open post-commit workbench from existing PNR.
      2. POST /formofpayment    — add Cash FOP; capture the FOP Identifier returned.
      3. POST /paymentoffer/.../payments — link the FOP to the offer with the fare amount.
                                  THIS is what actually arms the ticket for issuance.
      4. POST /reservations/{id}?Issuance=Ticket&DocumentValue=Retain — commit & issue.

    Without step 3, Travelport accepts the commit but returns ConfirmationHold (no ticket).
    """
    logger.info(f"Issuing ticket for PNR: {locator_code} via post-commit workbench buildfromlocator")

    issued_ticket_number = None

    try:
        # ── Step 1: Create post-commit workbench from locator ─────────────────
        base_path = TravelportConfig.base_path()
        wb_url = f"{base_path}/air/book/session/reservationworkbench/buildfromlocator?Locator={locator_code}"
        logger.info(f"Step 1: POST buildfromlocator for PNR {locator_code}")
        wb_result = _api_post(wb_url, {})

        reservation = (
            wb_result.get("ReservationResponse", {}).get("Reservation", {}) or
            wb_result.get("ReservationWorkbench", {}) or
            wb_result
        )
        workbench_id = reservation.get("Identifier", {}).get("value") or wb_result.get("id")

        if not workbench_id:
            logger.warning(f"Could not extract workbench_id from: {str(wb_result)[:500]}")
            raise ValueError("No workbench ID returned from post-commit workbench creation")

        logger.info(f"Step 1 OK: workbench ID = {workbench_id}")

        # Extract the Offer local id and UUID from the workbench (needed for Payment step)
        offer_local_id = None    # e.g. "offer_1"
        offer_uuid = None        # Travelport UUID
        try:
            offers = reservation.get("Offer", [])
            if offers:
                offer_local_id = offers[0].get("id")
                offer_uuid = offers[0].get("Identifier", {}).get("value")
        except Exception:
            pass
        logger.info(f"Step 1: Offer local id = {offer_local_id}, UUID = {offer_uuid}")

        # Extract total fare from offer for Payment step
        total_fare = 0.0
        currency_code = "USD"
        try:
            offers = reservation.get("Offer", [])
            if offers:
                price = offers[0].get("Price", {})
                total_fare = float(price.get("TotalPrice", 0.0))
                currency_code = price.get("CurrencyCode", {}).get("value", "USD")
        except Exception:
            pass
        logger.info(f"Step 1: Fare for payment = {currency_code} {total_fare}")

        # ── Step 2: Add Cash FOP if not already present ───────────────────────
        existing_fop = reservation.get("FormOfPayment", [])
        fop_local_id = None   # e.g. "formOfPayment_1"
        fop_uuid = None       # Travelport UUID for FOP

        if not existing_fop:
            logger.info("Step 2: No existing FOP — adding FormOfPaymentCash...")
            fop_url = TravelportEndpoints.add_fop_to_workbench(workbench_id)
            fop_payload = {
                "FormOfPayment": {
                    "@type": "FormOfPaymentCash",
                    "id": "formOfPayment_1"
                }
            }
            fop_result = _api_post(fop_url, fop_payload)

            # Extract both local id and UUID from FOP response
            try:
                fop_resp_data = fop_result.get("FormOfPaymentResponse", {})
                fop_obj = fop_resp_data.get("FormOfPayment") or fop_result.get("FormOfPayment")
                if isinstance(fop_obj, list) and fop_obj:
                    fop_local_id = fop_obj[0].get("id") or fop_obj[0].get("FormOfPaymentRef")
                    fop_uuid = fop_obj[0].get("Identifier", {}).get("value")
                elif isinstance(fop_obj, dict):
                    fop_local_id = fop_obj.get("id") or fop_obj.get("FormOfPaymentRef")
                    fop_uuid = fop_obj.get("Identifier", {}).get("value")
            except Exception as e:
                logger.warning(f"Error parsing FOP response: {e}")
            if not fop_local_id:
                fop_local_id = "formOfPayment_1"
            logger.info(f"Step 2 OK: FOP added. local id={fop_local_id}, UUID={fop_uuid}")
        else:
            logger.info("Step 2: FOP already present — extracting local id and UUID...")
            try:
                fop_local_id = existing_fop[0].get("id") or existing_fop[0].get("FormOfPaymentRef")
                fop_uuid = existing_fop[0].get("Identifier", {}).get("value")
            except Exception:
                pass
            if not fop_local_id:
                fop_local_id = "formOfPayment_1"
            logger.info(f"Step 2: FOP local id={fop_local_id}, UUID={fop_uuid}")

        # ── Step 3: Link FOP to Offer via Payment ─────────────────────────────
        # CRITICAL: FormOfPaymentIdentifier MUST include BOTH the local "id" field
        # AND the Travelport "Identifier" UUID together — error 4178 if either is missing.
        # OfferIdentifier must also use BOTH the local "id" / "OfferRef" AND the
        # Travelport "Identifier" UUID (Variant X3 format) to link properly.
        logger.info(f"Step 3: Linking FOP '{fop_local_id}' (UUID={fop_uuid}) to offer '{offer_local_id}'...")
        payment_url = TravelportEndpoints.add_payment_to_workbench(workbench_id)

        # Build FormOfPaymentIdentifier with BOTH local id + UUID (Variant E format)
        fop_identifier_block: dict = {}
        if fop_local_id:
            fop_identifier_block["id"] = fop_local_id
        if fop_uuid:
            fop_identifier_block["Identifier"] = {
                "authority": "Travelport",
                "value": fop_uuid
            }

        # Build OfferIdentifier block (Variant X3 format)
        offer_identifier_block: dict = {}
        if offer_local_id:
            offer_identifier_block["id"] = offer_local_id
            offer_identifier_block["OfferRef"] = offer_local_id
        if offer_uuid:
            offer_identifier_block["Identifier"] = {
                "authority": "Travelport",
                "value": offer_uuid
            }

        payment_payload: dict = {
            "Payment": {
                "@type": "Payment",
                "Amount": {
                    "@type": "Amount",
                    "CurrencyCode": {"value": currency_code},
                    "value": total_fare
                },
                "FormOfPaymentIdentifier": fop_identifier_block
            }
        }

        if offer_identifier_block:
            payment_payload["Payment"]["OfferIdentifier"] = [offer_identifier_block]

        payment_result = _api_post(payment_url, payment_payload)
        payment_errors = (
            payment_result.get("ReservationResponse", {}).get("Result", {}).get("Error", []) or
            payment_result.get("Result", {}).get("Error", [])
        )
        if payment_errors:
            for pe in payment_errors:
                logger.warning(f"Payment step warning [{pe.get('SourceCode')}]: {pe.get('Message')}")
        else:
            logger.info("Step 3 OK: Payment applied to workbench.")

        # ── Step 4: Commit workbench with Issuance=Ticket ─────────────────────
        commit_url = f"{base_path}/air/book/reservation/reservations/{workbench_id}?Issuance=Ticket&DocumentValue=Retain"
        logger.info(f"Step 4: Committing workbench {workbench_id} to issue ticket")
        commit_result = _api_post(commit_url, "")

        # Extract ticket number from commit response
        res4 = (
            commit_result.get("Reservation") or
            commit_result.get("ReservationResponse", {}).get("Reservation", {})
        )
        issued_tickets = res4.get("Ticket", []) if res4 else commit_result.get("Ticket", [])
        if issued_tickets:
            issued_ticket_number = issued_tickets[0].get("number")
            logger.info(f"Step 4 OK: Ticket issued — number: {issued_ticket_number}")
        else:
            errors = (
                commit_result.get("ReservationResponse", {}).get("Result", {}).get("Error", []) or
                commit_result.get("Result", {}).get("Error", [])
            )
            for err in errors:
                logger.warning(
                    f"Commit warning [{err.get('SourceCode')}]: {err.get('Message')} "
                    f"(category={err.get('category')})"
                )
            if not errors:
                logger.warning(
                    "Commit returned HTTP 200 but no Ticket[] and no Error[]. "
                    "Check that FOP + Payment were applied correctly."
                )

    except httpx.HTTPStatusError as e:
        logger.warning(
            f"Ticketing flow step failed ({e.response.status_code}) for PNR {locator_code}. "
            f"Response: {e.response.text[:500]}"
        )
    except ValueError as e:
        logger.error(f"Ticketing flow error for {locator_code}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during ticket issuance for {locator_code}: {e}", exc_info=True)

    # Retrieve the live reservation state to return complete ticket info
    ticket = retrieve_reservation(locator_code)

    # If Travelport returned a real ticket number, apply it
    if issued_ticket_number:
        ticket["ticket_number"] = issued_ticket_number
        ticket["status"] = "Ticketed"

    return ticket



# ── Cancel Reservation ────────────────────────────────────────────────────────


def cancel_reservation(locator_code: str) -> bool:
    """
    Cancel a Travelport reservation by locator code.

    There is no DELETE on /air/book/reservation/reservations/{Identifier} — that path
    only accepts GET and POST (confirmed live: DELETE returns 405, Allow: POST, GET).
    Cancellation instead follows the same post-commit-workbench pattern as ticket
    issuance:
      1. POST buildfromlocator — open a workbench against the existing PNR.
      2. POST cancelitems (cancelAllInd: true) — cancel the offer.
      3. POST commit — finalize the cancellation on the reservation.

    Args:
        locator_code (str): PNR to cancel

    Returns:
        bool: True if successful
    """
    logger.info(f"Cancelling reservation: {locator_code}")
    try:
        base_path = TravelportConfig.base_path()
        wb_url = f"{base_path}/air/book/session/reservationworkbench/buildfromlocator?Locator={locator_code}"
        wb_result = _api_post(wb_url, {})

        reservation = (
            wb_result.get("ReservationResponse", {}).get("Reservation", {}) or
            wb_result.get("ReservationWorkbench", {}) or
            wb_result
        )
        workbench_id = reservation.get("Identifier", {}).get("value") or wb_result.get("id")
        if not workbench_id:
            raise ValueError("No workbench ID returned from post-commit workbench creation")

        cancel_url = TravelportEndpoints.cancel_items(workbench_id)
        _api_post(cancel_url, {"cancelAllInd": True})

        commit_url = TravelportEndpoints.commit_workbench(workbench_id)
        _api_post(commit_url, "")

        logger.info(f"Reservation {locator_code} cancelled.")
        return True
    except httpx.HTTPStatusError as e:
        logger.error(f"Cancel failed: {e.response.status_code} — {e.response.text}")
        return False

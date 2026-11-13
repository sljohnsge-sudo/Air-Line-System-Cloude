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
import time
import uuid
import database
from config.travelport_config import TravelportConfig
from config.api_endpoints import TravelportEndpoints
from services.auth_service import get_auth_headers, invalidate_token
from services.search_service import parse_iso_duration, parse_naive_datetime, minutes_to_iso_duration, IATA_AIRLINE_NAMES
from services.special_services import ssr_code_of, ssr_kind
from services.segment_status import segment_statuses, booking_problem
from utils import tp_logger

logger = logging.getLogger(__name__)


def retrieve_ndc_ticket(locator_code: str, ticket_number: str) -> dict:
    """Ticket Retrieve for an NDC ticket (POST /air/ticket/tickets/getbylocator).
    Raises ValueError if Travelport answers with an Error[] instead of a ticket."""
    payload = {
        "@type": "TicketQueryGetByLocator",
        "detailViewInd": True,
        "Locator": {"value": locator_code, "locatorType": "Locator"},
        "TicketNumber": [{"value": ticket_number, "contentSource": "NDC"}],
    }
    result = _api_post(TravelportEndpoints.TICKET_RETRIEVE_BY_LOCATOR, payload)
    body = result.get("TicketListResponse", result)
    errors = (body.get("Result") or {}).get("Error") or []
    if errors:
        raise ValueError("; ".join(f"{e.get('Message')} ({e.get('SourceCode')})" for e in errors))
    return result


def _find_ticket_numbers(data) -> list[tuple[str, str]]:
    """Recursively search a Receipt List (or any Travelport response) for
    DocumentTicket entries and return their (passengerTypeCode, Number)
    pairs. Used by issue_ticket()'s Step 4b fallback — the Receipt List
    endpoint's exact nesting isn't pinned down in Travelport's own docs
    (schema shown collapsed), so this walks the whole structure rather than
    assuming one fixed path, matching how found_tickets is built from the
    commit response's own Receipt[]/Document[] above."""
    found: list[tuple[str, str]] = []
    if isinstance(data, dict):
        if data.get("@type") == "DocumentTicket" and data.get("Number"):
            ptc = data.get("TravelerIdentifierRef", {}).get("passengerTypeCode", "ADT")
            found.append((ptc, data["Number"]))
        for v in data.values():
            found.extend(_find_ticket_numbers(v))
    elif isinstance(data, list):
        for item in data:
            found.extend(_find_ticket_numbers(item))
    return found


def _api_get(url: str) -> dict:
    """Internal helper for GET requests with automatic token retry."""
    headers = get_auth_headers(with_body=False)
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.get(url, headers=headers)
        if response.status_code == 401:
            invalidate_token()
            response = client.get(url, headers=get_auth_headers(with_body=False))
        response.raise_for_status()
        return response.json()


def _schedule_change_pending(wb_result: dict) -> bool:
    """True when the airline changed the schedule after booking: an Offer of
    @type OfferModify with scheduleChangeInd=true, or a GDS segment status TK
    (Travelport's Workbench Commit docs: "a value in OfferStatus/StatusAir/
    code of either TK or UN indicates the flight has schedule change
    information"). Such a PNR can't be ticketed until the change is
    accepted — Jetstar JJ0VDH / JJ0V8S (2026-11-02) answered "NO FARES"."""
    res = (wb_result.get("ReservationResponse", {}).get("Reservation", {})
           or wb_result.get("Reservation", {}) or wb_result or {})
    if any(o.get("scheduleChangeInd") for o in res.get("Offer", []) or []):
        return True
    for rc in res.get("Receipt", []) or []:
        for st in ((rc.get("Confirmation") or {}).get("OfferStatus") or {}).get("StatusAir", []) or []:
            if st.get("code") == "TK":
                return True
    return False


class FareCheckUnavailable(Exception):
    """Travelport couldn't be asked right now (network / 5xx) — no verdict."""


def pre_payment_fare_check(locator_code: str, fare_source: str | None = "GDS") -> str | None:
    """Before taking payment for a held booking: is its stored fare still
    ticketable? Read-only — a reservation retrieve and an AirPrice; nothing
    is booked, changed or ticketed. Returns None when the fare is fine, or
    the reason it isn't (Travelport's own words). Raises FareCheckUnavailable
    when Travelport can't be reached, so the caller can refuse to charge
    without blaming the fare.

    Checks: (1) the PNR still has a stored fare; (2) its fare guarantee
    doesn't run out within the next 15 minutes; (3) GDS only — AirPrice of
    the booked flights and booking classes (no inventory check: the seats
    are already held) still returns a fare, at no more than the stored
    price. Catches "NO FARES" / "FILED FARE IS NO LONGER VALID" (Jetstar,
    2026-11-02) before the customer is charged rather than at ticketing."""
    from datetime import datetime, timedelta, timezone
    try:
        raw = _api_get(f"{TravelportConfig.base_path()}/air/book/reservation/reservations/{locator_code}?detailViewInd=true")
    except (httpx.HTTPError, ValueError) as e:
        raise FareCheckUnavailable(str(e))
    body0 = raw.get("ReservationResponse", {}) or {}
    res = body0.get("Reservation", {}) or {}
    if not res:
        # e.g. "PSEUDO CITY CODE MUST HAVE AN AGREEMENT" — no verdict on the fare
        errs = (body0.get("Result") or {}).get("Error") or []
        raise FareCheckUnavailable("; ".join(f"{e.get('Message')} ({e.get('SourceCode')})" for e in errs)
                                   or "Travelport returned no reservation")
    offers = res.get("Offer") or []
    priced = [o for o in offers if (o.get("Price") or {}).get("TotalPrice")]
    if not priced:
        return "The booking has no stored fare at Travelport"
    stored_total = sum(float(o["Price"]["TotalPrice"]) for o in priced)
    for o in priced:
        for tc in o.get("TermsAndConditionsFull") or []:
            exp = tc.get("ExpiryDate")
            if exp:
                try:
                    when = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    if when < datetime.now(timezone.utc) + timedelta(minutes=15):
                        return f"The stored fare's guarantee ends at {exp}"
                except ValueError:
                    pass
    if (fare_source or "GDS").upper() != "GDS":
        return None   # NDC: full-payload AirPrice isn't supported; stored-fare checks only

    criteria, seq = [], 0
    for o in priced:
        for prod in o.get("Product") or []:
            classes = {}
            for pf in prod.get("PassengerFlight") or []:
                for fp in pf.get("FlightProduct") or []:
                    for n in fp.get("segmentSequence") or []:
                        classes[n] = (fp.get("classOfService"), fp.get("cabin"))
            flights = []
            for i, seg in enumerate(prod.get("FlightSegment") or []):
                f = seg.get("Flight") or {}
                cos, cabin = classes.get(seg.get("sequence"), (None, None))
                item = {"@type": "SpecificFlightCriteria", "carrier": f.get("carrier"), "flightNumber": f.get("number"),
                        "from": (f.get("Departure") or {}).get("location"), "to": (f.get("Arrival") or {}).get("location"),
                        "departureDate": (f.get("Departure") or {}).get("date"),
                        "departureTime": (f.get("Departure") or {}).get("time"),
                        "arrivalDate": (f.get("Arrival") or {}).get("date"),
                        "arrivalTime": (f.get("Arrival") or {}).get("time"),
                        "segmentSequence": i + 1, "ContentSource": "GDS"}
                if cos:
                    item["classOfService"] = cos
                if cabin:
                    item["cabin"] = cabin
                flights.append(item)
            if flights:
                seq += 1
                criteria.append({"@type": "ProductCriteriaAir", "sequence": seq, "SpecificFlightCriteria": flights})
    # The booking's own passengers and their real ages on the first flight
    # (13/11/2026: was a made-up 25 / 20 / 10 / 1 per type). Adults go
    # without an age, as Travelport recommends; nothing is assumed.
    from services.search_service import passenger_criteria_for
    first_day = next((c["SpecificFlightCriteria"][0].get("departureDate") for c in criteria
                      if c.get("SpecificFlightCriteria")), None)
    counts = {"ADT": 0, "CNN": 0, "INF": 0, "STU": 0}
    ages = {"CNN": [], "INF": [], "STU": []}
    for t in res.get("Traveler") or []:
        ptc = (t.get("passengerTypeCode") or "").upper()
        if ptc not in counts:
            return None  # an unexpected passenger type: no check rather than a guessed one
        counts[ptc] += 1
        if ptc in ages:
            try:
                b = datetime.strptime((t.get("birthDate") or "")[:10], "%Y-%m-%d").date()
                d = datetime.strptime((first_day or "")[:10], "%Y-%m-%d").date()
            except ValueError:
                return None  # no readable birth date: skip this check rather than guess an age
            ages[ptc].append(d.year - b.year - ((d.month, d.day) < (b.month, b.day)))
    if not any(counts.values()):
        return None
    payload = {"@type": "OfferQueryBuildFromProducts", "validateInventoryInd": False,
               "BuildFromProductsRequest": {"@type": "BuildFromProductsRequestAir", "ProductCriteriaAir": criteria,
                                            "PassengerCriteria": passenger_criteria_for(
                                                counts["ADT"], counts["CNN"], counts["INF"], counts["STU"],
                                                ages["CNN"], ages["INF"], ages["STU"])}}
    try:
        result = _api_post(TravelportEndpoints.AIRPRICE_FULL_PAYLOAD, payload)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code < 500:
            return f"Travelport could not price the booked flights (HTTP {e.response.status_code})"
        raise FareCheckUnavailable(str(e))
    except httpx.HTTPError as e:
        raise FareCheckUnavailable(str(e))
    body = result.get("OfferListResponse", result)
    errors = (body.get("Result") or {}).get("Error") or []
    if errors:
        if all(e.get("category") == "TEMPORARY" for e in errors):
            raise FareCheckUnavailable(errors[0].get("Message"))
        return "Travelport: " + "; ".join(f"{e.get('Message')} ({e.get('SourceCode')})" for e in errors)
    new_offers = body.get("OfferID") or body.get("Offer") or []
    new_total = sum(float((o.get("Price") or {}).get("TotalPrice") or 0) for o in new_offers)
    if not new_total:
        return "Travelport returned no fare for the booked flights"
    if new_total > stored_total + 1:
        return f"The fare for the booked flights is now {new_total:,.0f}, above the stored {stored_total:,.0f}"
    return None


def _api_post(url: str, payload: dict | str | None) -> dict:
    """Internal helper for POST requests with automatic token retry.
    payload=None sends no body and no Content-Type (Post Commit Workbench)."""
    with_body = payload is not None
    headers = get_auth_headers(with_body=with_body)
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        if payload is None:
            kwargs = {}
        else:
            kwargs = {"json": payload} if not isinstance(payload, str) else {"content": payload}
        response = client.post(url, headers=headers, **kwargs)
        if response.status_code == 401:
            invalidate_token()
            response = client.post(url, headers=get_auth_headers(with_body=with_body), **kwargs)
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
    # Right after a booking or ticket is made, Travelport can answer 4392
    # "RESERVATION RETRIEVE FAILED DUE TO A RESERVATION UPDATE IN PROGRESS.
    # RETRY RESERVATION RETRIEVE" while the airline is still writing it — it
    # asks for a retry, so wait and ask again (2 + 4 + 6 s at most).
    for attempt in range(4):
        raw = _api_get(url)
        errors = ((raw.get("ReservationResponse") or {}).get("Result") or {}).get("Error") or []
        busy = any(str(e.get("SourceCode")) == "4392" for e in errors)
        if busy and attempt < 3 and "Reservation" not in (raw.get("ReservationResponse") or {}):
            logger.warning(f"{locator_code}: reservation update in progress (4392) — retrying the retrieve")
            time.sleep(2 * (attempt + 1))
            continue
        return _parse_reservation(raw, locator_code)


def parse_commit_response(raw: dict, locator_code: str) -> dict:
    """
    Parse a workbench commit response (STEP 7's own response body) the same
    way as a Reservation Retrieve response.

    For NDC content, Travelport's commit response embeds the full Offer[]
    (with Product[].FlightSegment[]) inline — confirmed live — but a
    subsequent, separate GET .../reservations/{pnr} (what
    retrieve_reservation() calls) does not return that Offer[] for NDC
    bookings the way it does for GDS ones. So the itinerary is only ever
    available in this one response; callers should use this as a same-request
    fallback to fill in flight/segment fields when retrieve_reservation()
    comes back without them, not as a replacement for it (Receipt/Ticket data
    still needs the live retrieve).
    """
    return _parse_reservation(raw, locator_code)


def get_tickets_by_locator(locator_code: str) -> dict:
    """
    Dedicated Ticket Retrieve lookup — separate from Reservation Retrieve's
    embedded Ticket[]. Used as a fallback check after a commit that returns
    no Ticket[] and no Error[], in case ticketing completed asynchronously
    moments after the commit response was returned.
    https://developer.travelport.com/apis/flights/ticketing/ticketgetbylocator

    Returns the raw TicketListResponse.responseData-equivalent dict exactly
    as Travelport returns it — no data is fabricated here.
    """
    logger.info(f"Ticket Retrieve (getbylocator) for PNR: {locator_code}")
    payload = {
        "@type": "TicketQueryGetByLocator",
        "detailViewInd": True,
        "Locator": {
            "value": locator_code,
            "locatorType": "Confirmation Number",
            "source": "1G",
        },
    }
    url = TravelportEndpoints.TICKET_RETRIEVE_BY_LOCATOR
    return _api_post(url, payload)


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
            "departure_terminal": dep.get("terminal"),
            "arrival_terminal": arr.get("terminal"),
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
        "departure_terminal": first_seg.get("departure_terminal"),
        "arrival_terminal": last_seg.get("arrival_terminal"),
        "departure_time": first_seg["departure_time"],
        "arrival_time": last_seg["arrival_time"],
        "duration": minutes_to_iso_duration(total_minutes),
        "segments": segments_list,
        "cabin_class": cabin_class,
        "fare_basis": fare_basis
    }


def _parse_special_services(reservation: dict) -> list[dict]:
    """
    Meal and wheelchair SSRs on a reservation, one row per passenger and
    code: {passenger_name, kind, code, status}, status from the airline
    ("confirmed", "pending", ...). Other SSRs are left out.

    Confirmed live 2026-10-29 (UL PNR HNH8DW): the retrieve lists each SSR
    twice — typed (SpecialServiceMeal / SpecialServiceWheelchairAirlineSupplied)
    and by IATA code (SpecialService + SSRCode) — and once per flight, so
    they're merged; a request counts as confirmed only when every copy is.
    """
    names = {}
    for t in reservation.get("Traveler", []) or []:
        name = t.get("PersonName", {})
        full_name = clean_passenger_name(name.get("Given", ""), name.get("Surname", ""))
        for ref in (t.get("id"), (t.get("Identifier") or {}).get("value")):
            if ref:
                names[ref] = full_name

    rows: dict[tuple, dict] = {}
    for item in reservation.get("SpecialService", []) or []:
        code = ssr_code_of(item)
        kind = ssr_kind(code)
        if not kind:
            continue
        trav = item.get("TravelerIdentifier") or {}
        ref = trav.get("TravelerRef") or trav.get("id") or (trav.get("Identifier") or {}).get("value")
        name = names.get(ref) or names.get((trav.get("Identifier") or {}).get("value"), "")
        status = ((item.get("Status") or {}).get("value") or "").lower()
        row = rows.setdefault((name, code), {"passenger_name": name, "kind": kind, "code": code, "statuses": set()})
        if status:
            row["statuses"].add(status)

    out = []
    for row in rows.values():
        statuses = row.pop("statuses")
        if statuses == {"confirmed"}:
            row["status"] = "confirmed"
        elif "pending" in statuses or not statuses:
            row["status"] = "pending"
        else:
            row["status"] = sorted(statuses - {"confirmed"})[0]
        out.append(row)
    return out


def _parse_reservation(raw: dict, locator_code: str) -> dict:
    """
    Parse the raw Travelport reservation response into a clean ticket record.

    Travelport returns multiple PNRs in Receipt[]:
    - Agency/GDS PNR: Receipt where Locator.source == '1G' (or the one created first)
    - Airline PNR:    Receipt where Locator.source == airline IATA code (e.g. 'FZ', 'UL')
    Real ticket number only comes from Ticket[] after issuance — never generated here.

    Raises ValueError if Travelport answers with an Error[] (e.g. "RECORD
    LOCATOR DOES NOT EXIST") instead of a Reservation — confirmed live: a
    purged/expired or nonexistent PNR still returns HTTP 200 with no
    Reservation key at all, which this used to silently treat as an empty-
    but-valid booking (every field blank, status left at the "Confirmed"
    default) instead of surfacing the real problem. The caller
    (main.py's retrieve_booking) already has a try/except that falls back to
    the local cache or a clean 404 — this was the missing check that let a
    nonexistent PNR through as if it were a real, empty reservation.
    """
    reservation_response = raw.get("ReservationResponse", {}) or {}
    top_level_errors = (reservation_response.get("Result") or {}).get("Error") or []
    has_reservation = "Reservation" in raw or "Reservation" in reservation_response
    if top_level_errors and not has_reservation:
        raise ValueError(
            "; ".join(f"{e.get('Message')} ({e.get('SourceCode')})" for e in top_level_errors)
        )

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
        "fare_breakdown": None,
        "booking_date": "",
        "seat_number": "",
        "baggage_allowance": "",
    }

    try:
        reservation = raw.get("Reservation", raw.get("ReservationResponse", {}).get("Reservation", {}))

        # ── Travelers — ALL passengers on this PNR (Adult/Child/Infant) ───────
        # A single booking/PNR can hold multiple travelers of different types;
        # map every one of them back, not just the lead passenger.
        raw_travelers = reservation.get("Traveler", [])
        parsed_travelers = []
        for t in raw_travelers:
            name = t.get("PersonName", {})
            full_name = clean_passenger_name(name.get('Given', ''), name.get('Surname', ''))
            # Since the Add Travel Agency step was added, a traveler's Email[]
            # can carry TWO entries — the agency's own address (emailType
            # "FROM") alongside the passenger's (emailType "TO") — confirmed
            # live (booking_HN4Q32): Travelport returned FROM=agency email
            # before TO=passenger email in the array. Blindly taking index
            # [0] picked the agency's email. Prefer the "TO"-typed entry;
            # fall back to [0] for older/GDS responses with a single
            # untyped entry (no emailType at all).
            emails = t.get("ContactInformation", {}).get("Email", []) or t.get("Email", [])
            to_email = next((e for e in emails if e.get("emailType") == "TO"), None)
            email = (to_email or (emails[0] if emails else {})).get("value", "")
            travel_docs = t.get("TravelDocument", [])
            passport_number = travel_docs[0].get("docNumber", "") if travel_docs else ""
            parsed_travelers.append({
                "passenger_type": t.get("passengerTypeCode", "ADT"),
                "given_name": name.get("Given", ""),
                "surname": name.get("Surname", ""),
                "full_name": full_name,
                "email": email,
                "passport_number": passport_number,
                "date_of_birth": t.get("birthDate", ""),
            })
        ticket["travelers"] = parsed_travelers

        # Flat top-level fields mirror the lead (first) traveler for backward compat
        if parsed_travelers:
            ticket["passenger_name"] = parsed_travelers[0]["full_name"]
            ticket["email"] = parsed_travelers[0]["email"]

        # Every passenger's e-ticket: Receipt[].Document[] DocumentTicket, each
        # pointing at its traveler (confirmed live, JJ2N21 3 adults: one
        # DocumentTicket per travelerRefId). Shown per passenger on the receipt.
        pos = {t.get("id"): i for i, t in enumerate(raw_travelers) if t.get("id")}
        found, seen = [], set()
        for rcpt in reservation.get("Receipt", []) or []:
            for doc in rcpt.get("Document", []) or []:
                num = doc.get("Number")
                if doc.get("@type") != "DocumentTicket" or not num or num in seen:
                    continue
                seen.add(num)
                tid = (doc.get("TravelerIdentifierRef") or {}).get("id")
                entry = {"passenger_type": (doc.get("TravelerIdentifierRef") or {}).get("passengerTypeCode")
                         or (raw_travelers[pos[tid]].get("passengerTypeCode") if tid in pos else "ADT"),
                         "number": num}
                if tid in pos:
                    entry.update(traveler_index=pos[tid], traveler_name=parsed_travelers[pos[tid]]["full_name"])
                found.append(entry)
        if found:
            ticket["ticket_numbers"] = sorted(found, key=lambda e: e.get("traveler_index", 99))

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

                # Fare breakdown for the final ticket (boss's requirements doc:
                # "airline tax and fuel charges and air line other charges
                # should show") -- Base = the airline's own fare; fuel
                # surcharge (YQ/YR -- the two IATA-standard fuel/carrier-
                # imposed surcharge codes) separated out from every other
                # government/airport tax, which is summed into one "other
                # taxes" figure since those are dozens of per-country codes
                # (airport tax, security fee, etc.) not meaningful to list
                # individually on a customer receipt. Confirmed live against
                # a real commit response (PriceBreakdown[].Amount.Taxes.Tax[]).
                base_fare = float(price.get("Base", 0) or 0)
                fuel_surcharge = 0.0
                other_taxes = 0.0
                seen_tax_ids = set()
                itemised = False
                for pb in price.get("PriceBreakdown", []) or []:
                    taxes = (pb.get("Amount", {}) or {}).get("Taxes", {}) or {}
                    itemised = itemised or bool(taxes.get("Tax"))
                    # One PriceBreakdown per passenger TYPE: its taxes are for ONE
                    # passenger, and "quantity" says how many (3 adults -> x3).
                    # Summing them once under-counted multi-passenger bookings
                    # (JJ2N21: taxes for 1 of 3 adults on the receipt).
                    qty = int(pb.get("quantity") or 1)
                    for tax in taxes.get("Tax", []) or []:
                        code = tax.get("taxCode", "")
                        value = float(tax.get("value", 0) or 0) * qty
                        if code in ("YQ", "YR"):
                            fuel_surcharge += value
                        else:
                            other_taxes += value
                # The itemised taxes must add up to Travelport's own tax total.
                total_taxes = float(price.get("TotalTaxes", 0) or 0)
                listed = fuel_surcharge + other_taxes
                if total_taxes and listed and abs(listed - total_taxes) > 1:
                    factor = total_taxes / listed
                    fuel_surcharge, other_taxes = round(fuel_surcharge * factor, 2), round(other_taxes * factor, 2)
                # Some content sources (confirmed live: Qatar Airways NDC
                # offers) return only Taxes.TotalTaxes with no itemized
                # Tax[] array at all — fuel_surcharge and other_taxes both
                # come out 0 even though TotalTaxes is nonzero, which would
                # show as "Fuel Surcharge: 0 / Taxes: 0" on the receipt
                # despite a real tax charge. Fall back to the known total
                # so the breakdown still sums to it.
                if not fuel_surcharge and not other_taxes:
                    other_taxes = float(price.get("TotalTaxes", 0) or 0)
                ticket["fare_breakdown"] = {
                    "base_fare": base_fare,
                    # False: only a tax total came back, so the fuel surcharge
                    # is inside other_taxes (the receipt says "Included in taxes").
                    "taxes_itemised": itemised,
                    "fuel_surcharge": fuel_surcharge,
                    "other_taxes": other_taxes,
                    "total_fees": float(price.get("TotalFees", 0) or 0),
                    "total": float(price.get("TotalPrice", 0) or 0),
                    # One passenger's airline price per passenger type, so the
                    # ERP invoice can put each ticket on its own line.
                    "per_type": {
                        pb.get("requestedPassengerType") or pb.get("passengerTypeCode"):
                            float(((pb.get("Amount") or {}).get("Total")) or 0)
                        for pb in price.get("PriceBreakdown", []) or []
                        if (pb.get("requestedPassengerType") or pb.get("passengerTypeCode"))
                        and ((pb.get("Amount") or {}).get("Total"))
                    },
                }

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
        else:
            # Confirmed live 2026-09-15 (PNR HN4Q4N): a ticketed GDS booking
            # doesn't always echo its ticket in the top-level Ticket[] array
            # on a Retrieve Reservation response — the real number is instead
            # nested in Receipt[], as a ReceiptPayment entry whose Document[]
            # holds a DocumentTicket with a "Number" field (this is the same
            # place issue_ticket()'s own commit response was found to hide
            # it). Without this fallback, an already-ticketed PNR looks
            # permanently "on hold" on every later retrieve/Sync PNR too.
            for receipt in reservation.get("Receipt", []):
                if receipt.get("@type") != "ReceiptPayment":
                    continue
                for doc in receipt.get("Document", []):
                    if doc.get("@type") == "DocumentTicket" and doc.get("Number"):
                        ticket["ticket_number"] = doc["Number"]
                        ticket["status"] = "Ticketed"
                        break
                if ticket["ticket_number"]:
                    break

        # ── Agency PNR + Airline PNRs from Receipt[] ──────────────────────────
        # Receipt[] contains one entry per PNR:
        #   - GDS/agency PNR has source like '1G'
        #   - Airline PNR has source = airline IATA code (e.g. 'UL', 'FZ')
        receipts = reservation.get("Receipt", [])
        for receipt in receipts:
            # A cancelled PNR's ReceiptCancellation carries the same Locator
            # under "Cancellation" instead of "Confirmation".
            confirmation = receipt.get("Confirmation") or receipt.get("Cancellation") or {}
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

        # ── Cancelled PNR ─────────────────────────────────────────────────────
        # Confirmed live 2026-10-29 (HNHCWB, HNHB47 — held GDS bookings
        # cancelled through Cancel Workbench Items): the retrieve then has no
        # Offer[] and only ReceiptCancellation entries (CancellationHold, one
        # per PNR), and the "Confirmed" default above showed them as live on
        # Check My Ticket Status. A live NDC PNR also comes back without
        # Offer[] but always keeps its ReceiptConfirmation (65 of 65 retrieves
        # in our logs), so that is checked too.
        receipt_types = {r.get("@type") for r in receipts}
        if not offers and "ReceiptCancellation" in receipt_types and "ReceiptConfirmation" not in receipt_types:
            ticket["status"] = "Cancelled"

        # ── Seat ──────────────────────────────────────────────────────────────
        seats = reservation.get("Seat", [])
        if seats:
            seat = seats[0]
            ticket["seat_number"] = f"{seat.get('row', '')}{seat.get('column', '')}"

        # ── Meal / wheelchair SSRs with the airline's status ──────────────────
        ticket["special_services"] = _parse_special_services(reservation)

        # ── EMDs for paid seats/extras ────────────────────────────────────────
        # Confirmed live 2026-10-22 (PNR HNFNHK): the reservation retrieve
        # lists every issued EMD as a DocumentEMD in Receipt[], so EMD numbers
        # always come from Travelport, not only from our saved copy.
        emds = []
        for receipt in reservation.get("Receipt", []) or []:
            for doc in receipt.get("Document", []) or []:
                if doc.get("@type") == "DocumentEMD" and doc.get("Number") and \
                        not any(e["number"] == doc["Number"] for e in emds):
                    emds.append({
                        "number": doc["Number"],
                        "description": (doc.get("EMDDescription") or {}).get("value"),
                        "amount": (doc.get("Amount") or {}).get("Total"),
                        "currency": ticket.get("currency"),
                        "passenger_type": (doc.get("TravelerIdentifierRef") or {}).get("passengerTypeCode") or "ADT",
                    })
        if emds:
            ticket["emd_numbers"] = emds

        # ── Airline status per flight (HK confirmed, HX cancelled, ...) ──────
        # A PNR can still exist after the airline dropped its flight; it is
        # then not "Confirmed" (see services/segment_status.py).
        statuses = segment_statuses(reservation)
        if statuses:
            ticket["segment_statuses"] = {f: s[0] for f, s in statuses.items()}
        problem = booking_problem(statuses)
        if problem:
            ticket["status"] = problem["status"]
            ticket["airline_status_note"] = problem["message"]

    except Exception as e:
        logger.warning(f"Partial reservation parse error: {e}", exc_info=True)

    return ticket


# ── STEP 9: Issue Ticket ───────────────────────────────────────────────────────

def _wait_for_airline_locator(locator_code: str, max_wait: int = 90, step: int = 5) -> None:
    """GDS: the airline's own booking reference (a second Receipt locator,
    e.g. {"source": "1A", "value": "FG9DDX"}) arrives some seconds after the
    booking. Ticketing before it is there fails — Qatar "ELECTRONIC TICKET NOT
    ISSUED VENDOR LOCATOR REQUIRED FOR QR" (2999; JJ2CWC, JJ2CX4), Emirates
    "INTERNAL SERVER ERROR" (JJ2CPW), all paid ~35 s after booking on
    2026-11-07 — while every GDS PNR ticketed that day had one. Waits for it
    (read-only retrieves) and then lets ticketing go ahead either way."""
    deadline = time.monotonic() + max_wait
    waited = False
    while True:
        try:
            raw = _api_get(TravelportEndpoints.retrieve_reservation(locator_code))
        except Exception as e:
            logger.warning(f"{locator_code}: airline reference check failed ({e}) — ticketing anyway")
            return
        res = (raw.get("ReservationResponse") or {}).get("Reservation") or {}
        if not res:
            return  # no verdict (e.g. 4392) — ticketing goes ahead as before
        airline_refs = [((r.get("Confirmation") or {}).get("Locator") or {}).get("value")
                        for r in res.get("Receipt") or [] if isinstance(r, dict)]
        if any(v and v != locator_code for v in airline_refs):
            if waited:
                logger.info(f"{locator_code}: airline booking reference has arrived — issuing the ticket")
            return
        if time.monotonic() >= deadline:
            logger.warning(f"{locator_code}: no airline booking reference after {max_wait} s — ticketing anyway")
            return
        logger.info(f"{locator_code}: waiting for the airline's booking reference before ticketing")
        waited = True
        time.sleep(step)


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
    ticket_issuance_diagnostic = None
    found_tickets: list = []
    found_emds: list = []
    extra_offers: list = []

    try:
        # ── Step 0: GDS — the airline's own booking reference must be there ──
        if ((database.get_booking_by_locator(locator_code) or {}).get("fare_source") or "GDS").upper() != "NDC":
            _wait_for_airline_locator(locator_code)

        # ── Step 1: Create post-commit workbench from locator ─────────────────
        wb_url = TravelportEndpoints.create_workbench_from_locator(locator_code)
        logger.info(f"Step 1: POST buildfromlocator for PNR {locator_code}")
        wb_result = _api_post(wb_url, None)

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

        # ── Step 1b: accept an airline schedule change first ──────────────
        # Workbench Commit with scheduleChangeAcceptedInd=true updates the
        # booking with the airline's new schedule (TK -> HK). That commit
        # closes this workbench, so a fresh one is opened for ticketing.
        if _schedule_change_pending(wb_result):
            logger.info(f"Step 1b: {locator_code} has an airline schedule change — accepting it before ticketing")
            # Same wrapper as the booking commit (workbench_service); the bare
            # {"scheduleChangeAcceptedInd": true} from the spec sample is
            # refused live: 400 INVALID INPUT FORMAT (1586).
            accept = _api_post(f"{TravelportConfig.base_path()}/air/book/reservation/reservations/{workbench_id}",
                               {"ReservationQueryCommitReservation": {"scheduleChangeAcceptedInd": True}})
            accept_errors = (accept.get("ReservationResponse", {}).get("Result", {}) or {}).get("Error", []) or []
            if accept_errors:
                raise ValueError("Schedule change could not be accepted: " +
                                 "; ".join(f"{e.get('Message')} ({e.get('SourceCode')})" for e in accept_errors))
            wb_result = _api_post(wb_url, None)
            reservation = (
                wb_result.get("ReservationResponse", {}).get("Reservation", {}) or
                wb_result.get("ReservationWorkbench", {}) or
                wb_result
            )
            workbench_id = reservation.get("Identifier", {}).get("value") or wb_result.get("id")
            if not workbench_id:
                raise ValueError("No workbench ID returned after accepting the schedule change")
            if _schedule_change_pending(wb_result):
                raise ValueError("The airline's schedule change is still pending after accepting it")
            logger.info(f"Step 1b OK: schedule change accepted; new workbench ID = {workbench_id}")

        # Extract the Offer local id and UUID from the workbench (needed for Payment step)
        offer_local_id = None    # e.g. "offer_1"
        offer_uuid = None        # Travelport UUID
        offer_authority = None   # "Travelport" for GDS, the carrier code for NDC (per NDC guide)
        try:
            offers = reservation.get("Offer", [])
            if offers:
                offer_local_id = offers[0].get("id")
                offer_uuid = offers[0].get("Identifier", {}).get("value")
                offer_authority = offers[0].get("Identifier", {}).get("authority")
        except Exception:
            pass

        if not offer_local_id:
            # Tried defaulting to "offer_1" (the conventional single-offer
            # local id seen on every other workbench this session) when
            # buildfromlocator returns no Offer[] at all — tested live on PNR
            # HN4RJV and Travelport's own Payment step explicitly REJECTED it:
            # "OFFER ID/IDENTIFIER VALUES MUST MATCH WITH THE RESERVATION
            # WORKBENCH OFFER ID/IDENTIFIER VALUES" (SourceCode 4179). Guessing
            # is confirmed wrong — but the REAL value isn't actually unknown:
            # confirmed live 2026-09-16 (7 consecutive NDC multi-city PNRs,
            # including HN50PK) that the ORIGINAL commit response always
            # embeds the real Offer[] (id + Identifier.value), even when this
            # later buildfromlocator comes back without it. /create and
            # /confirm now cache that value onto the booking record
            # (see workbench_service.commit_workbench's docstring) — use it
            # here instead of leaving the field unset.
            cached_booking = database.get_booking_by_locator(locator_code)
            if cached_booking and cached_booking.get("offer_local_id"):
                offer_local_id = cached_booking["offer_local_id"]
                offer_uuid = cached_booking.get("offer_uuid")
                offer_authority = cached_booking.get("offer_authority")
                if not offer_authority and cached_booking.get("fare_source") == "NDC":
                    offer_authority = cached_booking.get("airline_code")
                logger.warning(
                    "Step 1: buildfromlocator returned no Offer[] — using the "
                    f"OfferIdentifier cached from this PNR's original commit instead: "
                    f"id={offer_local_id}, uuid={offer_uuid}"
                )
            else:
                logger.warning(
                    "Step 1: buildfromlocator (and a follow-up workbench GET) returned "
                    "no Offer[], and no cached OfferIdentifier was found for this PNR "
                    "(booked before this fallback existed) — proceeding without one."
                )
        logger.info(f"Step 1: Offer local id = {offer_local_id}, UUID = {offer_uuid}")

        # Extract total fare from offer for Payment step. A PNR with paid
        # seats/extras booked (add_seats_and_extras_to_pnr) carries one extra
        # Offer per item ("an_o1", "an_o2"...) next to the air "offer_1" —
        # pay for ALL of them in this one Payment so the commit issues the
        # ticket AND the EMDs together (confirmed live 2026-10-22, PNR
        # HNFNCM: ticket + 2 EMDs from one Issuance=Ticket commit).
        total_fare = 0.0
        currency_code = None  # only Travelport's own currency, never assumed (13/11/2026)
        extra_offers: list = []
        try:
            offers = reservation.get("Offer", [])
            if offers:
                price = offers[0].get("Price", {})
                total_fare = float(price.get("TotalPrice", 0.0))
                currency_code = (price.get("CurrencyCode") or {}).get("value")
            # Safety lock: only pay Travelport for seat/extra offers the
            # customer was actually charged for at /confirm (addon_results
            # status "confirmed" only, matched by offer id). Anything else on
            # the PNR — including airline-"pending" requests — is left unpaid,
            # so the agency never pays the airline for an item the customer
            # didn't pay us for.
            cached_for_addons = database.get_booking_by_locator(locator_code) or {}
            addon_results = cached_for_addons.get("addon_results") or {}
            charged_offer_ids = {
                i.get("offer_id") for i in (addon_results.get("seats") or []) + (addon_results.get("extras") or [])
                if i.get("offer_id") and i.get("status") == "confirmed"
            }
            for o in offers[1:]:
                o_price = float(o.get("Price", {}).get("TotalPrice", 0.0) or 0.0)
                if not (o.get("id") and o.get("Identifier", {}).get("value") and o_price):
                    continue
                if o.get("id") not in charged_offer_ids:
                    logger.warning(f"Step 1: NOT paying for offer {o.get('id')} ({o_price}) — not charged to the customer")
                    continue
                extra_offers.append(o)
                total_fare += o_price
            if extra_offers:
                logger.info(f"Step 1: Also paying for {len(extra_offers)} seat/extra offer(s): {[o.get('id') for o in extra_offers]}")
        except Exception:
            pass

        if not total_fare:
            # Confirmed live 2026-09-15 (PNR HN4RJV): buildfromlocator does not
            # always return Offer[] for NDC content — same gap already
            # documented in cancel_reservation() above (top-level keys were
            # only @type, Identifier, Traveler, Receipt, ReservationComment).
            # Without a real fare here, the Payment step below was silently
            # sending Amount.value=0 against a real fare, so Travelport
            # accepted the commit but never actually ticketed it — a clean
            # "HTTP 200, no Ticket[], no Error[]" result that looked like a
            # generic failure but was really just a missing amount. Fall back
            # to the local cache's total_fare/currency (saved at /confirm
            # time from the original commit response), the same source
            # cancel_reservation() already trusts for fare_source on NDC PNRs.
            #
            # 13/11/2026: that cache used to be total_fare — OUR charge to the
            # customer (Travelport's price + markup + seats/extras) — so
            # Travelport was paid our marked-up total. Now only Travelport's
            # own AirPrice total, saved at booking as supplier_total /
            # supplier_currency, is used. A booking without it is not paid
            # with a guessed amount: ticketing stops and the paid-not-ticketed
            # path files the refund request.
            cached_booking = database.get_booking_by_locator(locator_code) or {}
            if cached_booking.get("supplier_total") and cached_booking.get("supplier_currency"):
                total_fare = float(cached_booking["supplier_total"])
                currency_code = cached_booking["supplier_currency"]
                logger.warning(
                    f"Step 1: buildfromlocator returned no usable Offer/Price — using Travelport's own "
                    f"AirPrice total saved at booking: {currency_code} {total_fare}"
                )
            else:
                raise ValueError("Travelport returned no price for this booking and no Travelport price was "
                                 "saved when it was made — not paying Travelport a guessed amount")
        if not currency_code:
            raise ValueError("Travelport returned no currency for this booking's price — not paying a guessed currency")
        logger.info(f"Step 1: Fare for payment = {currency_code} {total_fare}")

        # Extract every traveler's own workbench-assigned ref id (Travelport's
        # own sample names these "travelerRefId_N") for Payment.TravelerIdentifierRef.
        traveler_refs = []
        try:
            for t in reservation.get("Traveler", []):
                ptc = t.get("passengerTypeCode")
                tid = t.get("id")
                if ptc and tid:
                    pn = t.get("PersonName") or {}
                    name = " ".join(x for x in (pn.get("Given"), pn.get("Surname")) if x).strip()
                    traveler_refs.append({"passengerTypeCode": ptc, "id": tid, "name": name})
        except Exception:
            pass
        logger.info(f"Step 1: Traveler refs for payment = {traveler_refs}")

        # ── Step 2: Add Cash FOP if not already present ───────────────────────
        existing_fop = reservation.get("FormOfPayment", [])
        fop_local_id = None   # e.g. "formOfPayment_1"
        fop_uuid = None       # Travelport UUID for FOP

        if not existing_fop:
            logger.info("Step 2: No existing FOP — adding FormOfPaymentCash...")
            fop_url = TravelportEndpoints.add_fop_to_workbench(workbench_id)
            # Per Travelport's own certification guidance: generate the
            # Identifier client-side and send it directly in the FOP create
            # request (authority "Travelport" + a UUID we mint), rather than
            # only discovering the UUID by parsing whatever shape the create
            # response happens to return. This removes a dependency on that
            # parsing succeeding — previously, if the response shape didn't
            # match what we expected, fop_uuid silently stayed None and the
            # follow-on Payment step (which requires it) failed with no
            # visible error, leaving the ticket unissued.
            fop_uuid = str(uuid.uuid4()).upper()
            fop_local_id = "formOfPayment_1"
            fop_payload = {
                "FormOfPaymentCash": {
                    "id": fop_local_id,
                    "FormOfPaymentRef": fop_local_id,
                    "Identifier": {
                        "authority": "Travelport",
                        "value": fop_uuid
                    }
                }
            }
            fop_result = _api_post(fop_url, fop_payload)

            # The FOP create response does NOT echo back the "id"/
            # "FormOfPaymentRef" fields we sent — only a (different)
            # Identifier value, which live testing confirmed is actually the
            # workbench's own reservation Identifier, not a distinct
            # per-FOP one. Sending that mismatched pair on to the Payment
            # step causes Travelport error 4178 "FOP ID/IDENTIFIER VALUES
            # MUST MATCH WITH THE RESERVATION WORKBENCH FOP ID/IDENTIFIER
            # VALUES". Fix: re-fetch the workbench's own state via GET and
            # read back the FormOfPayment object exactly as Travelport
            # actually stored it, instead of guessing from the create
            # response's shape.
            try:
                wb_state = _api_get(TravelportEndpoints.get_workbench(workbench_id))
                wb_reservation = (
                    wb_state.get("ReservationResponse", {}).get("Reservation", {}) or
                    wb_state.get("Reservation", {}) or
                    wb_state
                )
                stored_fops = wb_reservation.get("FormOfPayment", [])
                if stored_fops:
                    stored_fop = stored_fops[0]
                    fop_local_id = stored_fop.get("id") or stored_fop.get("FormOfPaymentRef") or fop_local_id
                    fop_uuid = stored_fop.get("Identifier", {}).get("value") or fop_uuid
                    logger.info(f"Step 2: Re-fetched workbench — stored FOP local id={fop_local_id}, UUID={fop_uuid}")
                else:
                    logger.warning("Step 2: Workbench GET returned no FormOfPayment — using client-generated identifier as a fallback.")
            except Exception as e:
                logger.warning(f"Error re-fetching workbench state (using client-generated identifier instead): {e}")
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
        # Rebuilt to match Travelport's own documented example exactly
        # (support.travelport.com/.../APIRef_AddFOP.htm) after live testing
        # proved the previous shape — which added an unverified "@type":
        # "FormOfPaymentPaymentCash" and an "activeInd" field, neither of
        # which appear in Travelport's own sample — was rejected with error
        # 4178 "FOP ID/IDENTIFIER VALUES MUST MATCH..." even when the FOP
        # id/Identifier values themselves were byte-for-byte confirmed
        # correct against the workbench's own stored state. Their real
        # example also includes a Payment-level id/Identifier and a
        # TravelerIdentifierRef array, both previously missing here.
        logger.info(f"Step 3: Linking FOP '{fop_local_id}' (UUID={fop_uuid}) to offer '{offer_local_id}'...")
        payment_url = TravelportEndpoints.add_payment_to_workbench(workbench_id)

        fop_identifier_block: dict = {}
        if fop_local_id:
            fop_identifier_block["id"] = fop_local_id
            fop_identifier_block["FormOfPaymentRef"] = fop_local_id
        if fop_uuid:
            fop_identifier_block["Identifier"] = {
                "authority": "Travelport",
                "value": fop_uuid
            }

        offer_identifier_block: dict = {}
        if offer_local_id:
            offer_identifier_block["id"] = offer_local_id
            offer_identifier_block["offerRef"] = offer_local_id
        if offer_uuid:
            offer_identifier_block["Identifier"] = {
                "authority": offer_authority or "Travelport",
                "value": offer_uuid
            }

        payment_payload: dict = {
            "Payment": {
                "id": "payment_1",
                "Identifier": {
                    "authority": "Travelport",
                    "value": str(uuid.uuid4()).upper()
                },
                "Amount": {
                    "value": total_fare,
                    "code": currency_code,
                    # minorUnit/currencySource/approximateInd match Travelport's
                    # own GDS certification reference (booking_HMZ9HH/
                    # 11.Payment_RQ). minorUnit=2 is standard decimal-currency
                    # precision (matches every currency this system prices in).
                    "minorUnit": 2,
                    "currencySource": "Supplier",
                    "approximateInd": True
                },
                "FormOfPaymentIdentifier": fop_identifier_block
            }
        }

        if offer_identifier_block:
            payment_payload["Payment"]["OfferIdentifier"] = [offer_identifier_block] + [
                {"id": o["id"], "offerRef": o["id"], "Identifier": {
                    "authority": o["Identifier"].get("authority") or "Travelport",
                    "value": o["Identifier"]["value"]}}
                for o in extra_offers
            ]
        if traveler_refs:
            payment_payload["Payment"]["TravelerIdentifierRef"] = traveler_refs

        payment_result = _api_post(payment_url, payment_payload)
        payment_errors = (
            payment_result.get("PaymentResponse", {}).get("Result", {}).get("Error", []) or
            payment_result.get("ReservationResponse", {}).get("Result", {}).get("Error", []) or
            payment_result.get("Result", {}).get("Error", [])
        )
        if payment_errors:
            for pe in payment_errors:
                logger.warning(f"Payment step warning [{pe.get('SourceCode')}]: {pe.get('Message')}")
        else:
            logger.info("Step 3 OK: Payment applied to workbench.")

        # ── Step 4: Commit workbench with Issuance=Ticket ─────────────────────
        # A 2-second pause was tried here on 2026-09-16 on the theory that
        # Commit was racing Payment's persistence — tested on 7 consecutive
        # NDC multi-city PNRs WITH the pause and all 7 failed to ticket
        # (vs. succeeding on the very first attempt right after the
        # offer_local_id/fare caching fix, before this pause existed).
        # Removed — no live evidence it helps, and some evidence it may
        # correlate with the post-buildfromlocator workbench session
        # degrading before Commit runs. Do not re-add without new evidence.
        commit_url = f"{TravelportConfig.base_path()}/air/book/reservation/reservations/{workbench_id}?Issuance=Ticket&DocumentValue=Retain"
        logger.info(f"Step 4: Committing workbench {workbench_id} to issue ticket")
        commit_result = _api_post(commit_url, "")

        # Extract ticket number(s) from commit response. Two possible
        # locations, both checked: the top-level Ticket[] array, and (per a
        # gap confirmed live 2026-09-15 on PNR HN4Q4N and several earlier
        # bookings wrongly assumed to have failed ticketing) Receipt[] — a
        # ReceiptPayment entry whose Document[] holds a DocumentTicket with
        # a "Number". Every "ticketless" booking (HTTP 200, no Ticket[], no
        # Error[]) that was previously assumed to have failed actually had a
        # real ticket number sitting in Receipt[] the whole time — this was
        # a parsing gap in our code, not a failed issuance.
        res4 = (
            commit_result.get("Reservation") or
            commit_result.get("ReservationResponse", {}).get("Reservation", {})
        )
        issued_tickets = res4.get("Ticket", []) if res4 else commit_result.get("Ticket", [])
        found_tickets: list[tuple[str, str]] = []  # [(passengerTypeCode, number), ...]
        ticket_traveler: dict = {}  # ticket number -> Travelport traveler id (who the ticket is for)

        # EMDs for paid seats/extras come back as DocumentEMD next to the
        # DocumentTicket, each with its own amount and description.
        ref_ptc_map = {t["id"]: t["passengerTypeCode"] for t in traveler_refs}
        for receipt in (res4.get("Receipt", []) if res4 else []):
            for doc in receipt.get("Document", []) or []:
                if doc.get("@type") != "DocumentEMD" or not doc.get("Number"):
                    continue
                if any(e["number"] == doc["Number"] for e in found_emds):
                    continue
                tref = doc.get("TravelerIdentifierRef", {}) or {}
                found_emds.append({
                    "number": doc["Number"],
                    "description": (doc.get("EMDDescription") or {}).get("value"),
                    "amount": (doc.get("Amount") or {}).get("Total"),
                    "currency": currency_code,
                    "passenger_type": tref.get("passengerTypeCode") or ref_ptc_map.get(tref.get("id")) or "ADT",
                })
        if found_emds:
            logger.info(f"Step 4: EMD(s) issued — {[e['number'] for e in found_emds]}")
        if issued_tickets:
            # One Ticket per passenger: keep every one (only the first used to be kept,
            # so a 3-passenger booking showed one ticket number).
            for t in issued_tickets:
                number = t.get("number") or t.get("Number")
                if not number or any(n == number for _, n in found_tickets):
                    continue
                tref = t.get("TravelerIdentifierRef") or {}
                ptc = tref.get("passengerTypeCode") or ref_ptc_map.get(tref.get("id")) or "ADT"
                found_tickets.append((ptc, number))
                if tref.get("id"):
                    ticket_traveler[number] = tref["id"]
            issued_ticket_number = found_tickets[0][1] if found_tickets else None
            logger.info(f"Step 4 OK: Ticket issued — number(s): {[n for _, n in found_tickets]}")
        else:
            seen_numbers = set()
            for receipt in (res4.get("Receipt", []) if res4 else []):
                if receipt.get("@type") != "ReceiptPayment":
                    continue
                for doc in receipt.get("Document", []):
                    if doc.get("@type") != "DocumentTicket":
                        continue
                    number = doc.get("Number")
                    if not number or number in seen_numbers:
                        continue
                    seen_numbers.add(number)
                    tref = doc.get("TravelerIdentifierRef", {})
                    ref_ptc = {t["id"]: t["passengerTypeCode"] for t in traveler_refs}
                    ptc = tref.get("passengerTypeCode") or ref_ptc.get(tref.get("id")) or "ADT"
                    found_tickets.append((ptc, number))
                    if tref.get("id"):
                        ticket_traveler[number] = tref["id"]
                    if not issued_ticket_number:
                        issued_ticket_number = number

            if issued_ticket_number:
                logger.info(f"Step 4 OK: Ticket issued (via Receipt/DocumentTicket) — number: {issued_ticket_number}")
            else:
                errors = (
                    commit_result.get("ReservationResponse", {}).get("Result", {}).get("Error", []) or
                    commit_result.get("Result", {}).get("Error", [])
                )
                warnings = (
                    commit_result.get("ReservationResponse", {}).get("Result", {}).get("Warning", []) or
                    commit_result.get("Result", {}).get("Warning", [])
                )
                said = "; ".join(f"{w.get('Message')}" + (f" ({w.get('SourceCode')})" if w.get("SourceCode") else "")
                                 for w in [*errors, *warnings] if w.get("Message"))
                if said:
                    ticket_issuance_diagnostic = ticket_issuance_diagnostic or f"Travelport: {said}"
                for err in errors:
                    logger.warning(
                        f"Commit warning [{err.get('SourceCode')}]: {err.get('Message')} "
                        f"(category={err.get('category')})"
                    )
                if not errors:
                    logger.warning(
                        "Commit returned HTTP 200 but no Ticket[] and no Error[], and no "
                        "DocumentTicket found in Receipt[] either. Check that FOP + Payment "
                        "were applied correctly."
                    )

                # ── Step 4b: Receipt List fallback ─────────────────────────────
                # NDC content confirmed live to sometimes omit Ticket[]/Receipt[]
                # from the commit response itself even when the ticket WAS
                # issued (per developer.travelport.com/apis/flights/
                # retrieve-bookings-and-tickets/getreceipts — a dedicated
                # endpoint for exactly this: GET .../receipt/reservations/
                # {id}/receipts?ReceiptType=ConfirmationTicket). Try it before
                # giving up, rather than only relying on the commit response.
                try:
                    receipts_url = TravelportEndpoints.get_reservation_receipts(
                        workbench_id, receipt_type="ConfirmationTicket"
                    )
                    receipts_result = _api_get(receipts_url)
                    for ptc, number in _find_ticket_numbers(receipts_result):
                        if number in seen_numbers:
                            continue
                        seen_numbers.add(number)
                        found_tickets.append((ptc, number))
                        if not issued_ticket_number:
                            issued_ticket_number = number
                    if issued_ticket_number:
                        logger.info(
                            f"Step 4b OK: Ticket issued (found via Receipt List fallback) — "
                            f"number: {issued_ticket_number}"
                        )
                except Exception as rl_e:
                    logger.warning(f"Step 4b: Receipt List fallback failed (non-fatal): {rl_e}")

        # ── Step 5: Retrieve Ticket (one call per passenger's ticket number)
        # — matches Travelport's GDS certification reference (booking_HMZ9HH/
        # 14-16.Retrieve Ticket ADT/INF/CHD). Purely informational (fetches
        # full document detail for a ticket that's already issued) —
        # best-effort, never fails the booking if it errors.
        # NDC tickets must use Ticket Retrieve (POST .../tickets/getbylocator,
        # contentSource NDC) — the GET-by-number endpoint is GDS only per
        # Travelport's docs and answers "TICKET IS NOT VALID" for NDC tickets.
        _cached = database.get_booking_by_locator(locator_code) or {}
        is_ndc = _cached.get("fare_source") == "NDC" or bool(offer_authority and offer_authority != "Travelport")
        for ptc, number in found_tickets:
            try:
                if is_ndc:
                    retrieve_ndc_ticket(locator_code, number)
                else:
                    _api_get(TravelportEndpoints.retrieve_ticket_by_number(number))
                logger.info(f"Step 5 OK: Retrieved ticket document for {ptc} — {number}")
            except Exception as rt_e:
                logger.warning(f"Step 5: Retrieve Ticket failed for {ptc} {number} (non-fatal): {rt_e}")

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
        # Which passenger each ticket is for: Travelport's traveler id -> the
        # traveler's position and name in the booking (shown on the receipt).
        ref_pos = {r["id"]: i for i, r in enumerate(traveler_refs)}
        ref_name = {r["id"]: r.get("name") for r in traveler_refs}
        ticket["ticket_numbers"] = [
            {"passenger_type": ptc, "number": num,
             **({"traveler_index": ref_pos[ticket_traveler[num]], "traveler_name": ref_name.get(ticket_traveler[num])}
                if ticket_traveler.get(num) in ref_pos else {})}
            for ptc, num in found_tickets]
    ticket["emd_numbers"] = found_emds
    # Seat/extra offers that were paid for here but came back without an
    # EMD — never shown as complete; the caller flags these for the admin.
    ticket["emd_expected"] = len(extra_offers)

    # Surface the Ticket Retrieve fallback's diagnostic (a real Travelport
    # error message, e.g. "DOCUMENT HISTORY NOT FOUND FOR REQUESTED
    # RESERVATION") when no ticket could be found by either path — useful
    # evidence for reporting the account/PCC-level issue to Travelport.
    if ticket_issuance_diagnostic:
        ticket["ticket_issuance_diagnostic"] = ticket_issuance_diagnostic

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

    Step 3's commit body differs by content source (developer.travelport.com/
    docs/flights/ndc/ndc-guide): NDC requires an explicit
    {"RetainFlag": false} body to signal a cancel rather than a retain/modify;
    GDS keeps the existing empty-string commit body used for both booking and
    cancellation there.

    Content source is read from the LOCAL database cache's fare_source
    (saved at /confirm time from the original commit response), not from
    this buildfromlocator response — confirmed live that buildfromlocator
    returns no Offer[] at all here (top-level keys: @type, Identifier,
    Traveler, FormOfPayment, Receipt, ReservationComment), the same gap
    already documented elsewhere in this file for retrieve_reservation() on
    NDC PNRs. Falls back to the (likely always-empty) Offer/Product lookup
    only if there's no local cache row, so an uncached PNR still defaults
    safely to the existing GDS-style empty-body commit rather than guessing.

    Args:
        locator_code (str): PNR to cancel

    Returns:
        bool: True if successful
    """
    logger.info(f"Cancelling reservation: {locator_code}")
    try:
        wb_url = TravelportEndpoints.create_workbench_from_locator(locator_code)
        wb_result = _api_post(wb_url, None)

        reservation = (
            wb_result.get("ReservationResponse", {}).get("Reservation", {}) or
            wb_result.get("ReservationWorkbench", {}) or
            wb_result
        )
        workbench_id = reservation.get("Identifier", {}).get("value") or wb_result.get("id")
        if not workbench_id:
            raise ValueError("No workbench ID returned from post-commit workbench creation")

        cached_booking = database.get_booking_by_locator(locator_code)
        if cached_booking and cached_booking.get("fare_source"):
            content_source = cached_booking["fare_source"]
        else:
            offers = reservation.get("Offer", [])
            products = offers[0].get("Product", []) if offers else []
            content_source = products[0].get("ContentSource", "GDS") if products else "GDS"
        is_ndc = content_source == "NDC"

        cancel_url = TravelportEndpoints.cancel_items(workbench_id)
        _api_post(cancel_url, {"cancelAllInd": True})

        commit_url = TravelportEndpoints.commit_workbench(workbench_id)
        if is_ndc:
            logger.info(f"NDC content — committing cancellation with RetainFlag: false ({locator_code})")
            _api_post(commit_url, {"RetainFlag": False})
        else:
            _api_post(commit_url, "")

        logger.info(f"Reservation {locator_code} cancelled.")
        return True
    except httpx.HTTPStatusError as e:
        logger.error(f"Cancel failed: {e.response.status_code} — {e.response.text}")
        return False
    except Exception as e:
        # Confirmed live: a nonexistent/already-gone locator doesn't always
        # make Travelport's buildfromlocator call itself fail with an HTTP
        # error status (the httpx.HTTPStatusError case above) -- it can come
        # back 200 with no workbench Identifier at all, which raised the
        # ValueError above uncaught, crashing this call with a 500 instead
        # of the documented "bool: True if successful" contract every caller
        # (main.py's cancel_booking) relies on to tell a genuine Travelport-
        # side failure apart from "nothing to cancel."
        logger.error(f"Cancel failed for {locator_code} (non-HTTP error): {e}")
        return False

"""
services/invoice_service.py
============================
Invoice Data Retrieval -- Travelport Live API Only
Retrieves full booking details for invoicing purposes.

All data is sourced exclusively from the Travelport GET /reservations/{pnr} API.
No local database, no mock data, no system-generated values.
"""

import httpx
import logging
from config.travelport_config import TravelportConfig
from config.api_endpoints import TravelportEndpoints
from services.auth_service import get_auth_headers, invalidate_token
from services.search_service import (
    parse_iso_duration, parse_naive_datetime,
    minutes_to_iso_duration, IATA_AIRLINE_NAMES
)
from services.ticket_service import clean_passenger_name

logger = logging.getLogger(__name__)


def _api_get(url: str) -> dict:
    """Internal GET with automatic token retry."""
    headers = get_auth_headers()
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT) as client:
        response = client.get(url, headers=headers)
        if response.status_code == 401:
            invalidate_token()
            response = client.get(url, headers=get_auth_headers())
        response.raise_for_status()
        return response.json()


def _parse_pax_type_label(ptc: str) -> str:
    return {"ADT": "Adult", "CNN": "Child", "INF": "Infant"}.get(ptc.upper(), ptc)


def _parse_invoice_product(product: dict) -> dict | None:
    """
    Parse a single Offer.Product entry (one direction/leg of the PNR) into a
    flat leg dict matching the invoice's flat top-level fields — same shape
    for a one-way PNR's single leg and each leg of a round-trip PNR.
    """
    source_code = product.get("ContentSource", "GDS")
    fare_source = "LCC" if source_code == "APIPAC" else source_code
    flight_segments = product.get("FlightSegment", [])
    if not flight_segments:
        return None

    # Map flight segments to their FlightProduct to get cabin, classOfService
    # (booking/seat class), and fareBasisCode
    seg_product_map = {}
    pax_flights = product.get("PassengerFlight", [])
    if pax_flights:
        fp_list = pax_flights[0].get("FlightProduct", [])
        for fp in fp_list:
            for seq in fp.get("segmentSequence", []):
                seg_product_map[seq] = fp

    segments_list = []
    for f_seg in flight_segments:
        leg = f_seg.get("Flight", {})
        if not leg:
            continue
        carrier = leg.get("carrier", "")
        number = leg.get("number", "")
        dep = leg.get("Departure", {})
        arr = leg.get("Arrival", {})
        duration = leg.get("duration", "")
        sequence = f_seg.get("sequence", 1)

        fp_info = seg_product_map.get(sequence, {})
        cabin = fp_info.get("cabin", "")
        class_of_service = fp_info.get("classOfService", "")
        fare_basis = fp_info.get("fareBasisCode", "")

        dep_time = f"{dep.get('date', '')} {dep.get('time', '')}".strip()
        arr_time = f"{arr.get('date', '')} {arr.get('time', '')}".strip()

        segments_list.append({
            "carrier": carrier,
            "carrier_name": IATA_AIRLINE_NAMES.get(carrier, carrier),
            "flight_number": f"{carrier}{number}",
            "departure_airport": dep.get("location", ""),
            "arrival_airport": arr.get("location", ""),
            "departure_time": dep_time,
            "arrival_time": arr_time,
            "duration": duration,
            "equipment": leg.get("equipment", ""),
            "cabin": cabin,
            "class_of_service": class_of_service,
            "fare_basis": fare_basis,
        })

    if not segments_list:
        return None

    for i in range(len(segments_list) - 1):
        try:
            arr_dt = parse_naive_datetime(segments_list[i]["arrival_time"])
            dep_dt = parse_naive_datetime(segments_list[i + 1]["departure_time"])
            layover = int((dep_dt - arr_dt).total_seconds() / 60)
            segments_list[i]["layover_minutes"] = max(0, layover)
        except Exception:
            segments_list[i]["layover_minutes"] = 0

    first_seg = segments_list[0]
    last_seg = segments_list[-1]
    total_minutes = sum(
        parse_iso_duration(s["duration"]) + s.get("layover_minutes", 0)
        for s in segments_list
    )

    return {
        "fare_source": fare_source,
        "flight_number": first_seg["flight_number"],
        "airline": first_seg["carrier_name"],
        "airline_code": first_seg["carrier"],
        "departure_airport": first_seg["departure_airport"],
        "arrival_airport": last_seg["arrival_airport"],
        "departure_time": first_seg["departure_time"],
        "arrival_time": last_seg["arrival_time"],
        "segments": segments_list,
        "cabin_class": first_seg.get("cabin", ""),
        "class_of_service": first_seg.get("class_of_service", ""),
        "fare_basis": first_seg.get("fare_basis", ""),
        "duration": minutes_to_iso_duration(total_minutes),
    }


def retrieve_invoice_data(locator_code: str) -> dict:
    """
    Retrieve full invoice data for a booking directly from Travelport.
    Calls: GET /air/book/reservation/reservations/{locator_code}

    Returns a structured invoice payload with all passengers, segments,
    pricing, and PNR info -- sourced entirely from Travelport API.
    """
    logger.info(f"[INVOICE] Retrieving reservation from Travelport: {locator_code}")
    url = TravelportEndpoints.retrieve_reservation(locator_code)
    raw = _api_get(url)
    return _parse_invoice(raw, locator_code)


def _parse_invoice(raw: dict, locator_code: str) -> dict:
    """
    Parse the full Travelport reservation response into a structured
    invoice payload. All fields come exclusively from the API response.
    """
    invoice = {
        "source": "Travelport TripServices v11",
        "agency_pnr": locator_code,
        "airline_pnr": None,
        "airline_pnr_source": None,
        "booking_date": None,
        "status": "Confirmed",
        "ticket_number": None,
        "fare_source": "GDS",
        # All passengers parsed from Reservation.Traveler[]
        "all_passengers": [],
        # Flight info
        "flight_number": None,
        "airline": None,
        "airline_code": None,
        "departure_airport": None,
        "arrival_airport": None,
        "departure_time": None,
        "arrival_time": None,
        "duration": None,
        "cabin_class": None,
        "fare_basis": None,
        "segments": [],
        # Pricing from Offer.Price
        "total_fare": 0.0,
        "currency": None,
        "price_breakdown": {},
        "baggage_allowance": None,
        # Seats from Reservation.Seat[]
        "seat_assignments": [],
    }

    try:
        reservation = raw.get(
            "Reservation",
            raw.get("ReservationResponse", {}).get("Reservation", {})
        )

        # -- SECTION 1: All Travelers from Reservation.Traveler[] --------------
        travelers_raw = reservation.get("Traveler", [])
        logger.info(f"[INVOICE] Travelport returned {len(travelers_raw)} traveler(s)")

        for idx, t in enumerate(travelers_raw):
            name_obj  = t.get("PersonName", {})
            given     = name_obj.get("Given", "")
            surname   = name_obj.get("Surname", "")
            full_name = clean_passenger_name(given, surname)

            emails = (
                t.get("ContactInformation", {}).get("Email", [])
                or t.get("Email", [])
            )
            email = emails[0].get("value", "") if emails else ""

            phones = (
                t.get("ContactInformation", {}).get("Phone", [])
                or t.get("Phone", [])
            )
            phone = phones[0].get("number", "") if phones else ""

            pax_type = t.get("passengerTypeCode", t.get("type", "ADT")).upper()
            if pax_type not in ["ADT", "CNN", "INF"]:
                pax_type = "ADT"

            dob    = t.get("birthDate", t.get("BirthDate", ""))
            gender = t.get("gender", t.get("Gender", ""))

            passport_number = ""
            passport_expiry = ""
            nationality     = ""
            for doc in t.get("TravelDocument", []):
                doc_type = doc.get("docType", doc.get("documentType", "")).upper()
                if "PASSPORT" in doc_type or doc_type in ["P", "PP"]:
                    passport_number = doc.get("docNumber", doc.get("number", ""))
                    passport_expiry = doc.get("expireDate", doc.get("expiryDate", ""))
                    nationality     = doc.get("nationality", doc.get("issuingCountry", ""))
                    break

            invoice["all_passengers"].append({
                "passenger_index":      idx + 1,
                "passenger_type":       pax_type,
                "passenger_type_label": _parse_pax_type_label(pax_type),
                "full_name":            full_name,
                "given_name":           given,
                "surname":              surname,
                "date_of_birth":        dob,
                "gender":               gender,
                "passport_number":      passport_number,
                "passport_expiry":      passport_expiry,
                "nationality":          nationality,
                "email":                email,
                "phone":                phone,
            })

        # -- SECTION 2: Flight Segments + Pricing from Offer -------------------
        offers = reservation.get("Offer", [])
        if offers:
            offer = offers[0]

            # A PNR has one Product per direction — one for one-way/multi-city
            # legs, two for round trip (outbound + return). Parse every
            # product into a "leg" dict; the first leg's fields are mirrored
            # onto the flat top-level invoice fields for backward
            # compatibility, and the full list is exposed as invoice["legs"].
            products = offer.get("Product", [])
            legs = [_parse_invoice_product(p) for p in products]
            legs = [l for l in legs if l is not None]

            if legs:
                invoice["legs"] = legs
                first_leg = legs[0]
                last_leg = legs[-1]
                invoice["fare_source"]       = first_leg["fare_source"]
                invoice["flight_number"]     = first_leg["flight_number"]
                invoice["airline"]           = first_leg["airline"]
                invoice["airline_code"]      = first_leg["airline_code"]
                invoice["departure_airport"] = first_leg["departure_airport"]
                invoice["arrival_airport"]   = last_leg["arrival_airport"]
                invoice["departure_time"]    = first_leg["departure_time"]
                invoice["arrival_time"]      = last_leg["arrival_time"]
                invoice["segments"]          = first_leg["segments"]
                invoice["cabin_class"]       = first_leg["cabin_class"]
                invoice["class_of_service"]  = first_leg["class_of_service"]
                invoice["fare_basis"]        = first_leg["fare_basis"]
                invoice["duration"]          = first_leg["duration"]

            # Pricing — TotalPrice is the authoritative grand total from Travelport
            price = offer.get("Price", {})
            if price:
                invoice["total_fare"]  = float(price.get("TotalPrice", 0))
                invoice["currency"]    = price.get("CurrencyCode", {}).get("value", "LKR")

                # Offer-level fare summary (directly from Travelport Price object)
                invoice["fare_summary"] = {
                    "base_fare_total":   float(price.get("Base", 0)),
                    "total_taxes":       float(price.get("TotalTaxes", 0)),
                    "total_fees":        float(price.get("TotalFees", 0)),
                    "grand_total":       float(price.get("TotalPrice", 0)),
                    "currency":          price.get("CurrencyCode", {}).get("value", "LKR"),
                    "source":            "Travelport Offer.Price (PriceDetail)",
                    # Commission/Discount note:
                    # Travelport's GET /reservations/{pnr} does NOT return commission or
                    # agent incentive/discount in the Offer object (offer_keys: Product, Price,
                    # TermsAndConditionsFull only). Commission data is available via Travelport's
                    # separate Commission API or the Ticketing/Queue flow after ticketing.
                    "commission_note":   "Commission/discount not returned by Travelport Reservation Retrieve API. Available via Travelport Commission API post-ticketing.",
                }

                # Known Travelport tax codes → human-readable descriptions
                TAX_CODE_LABELS = {
                    "LK": "Airport Tax (LK)",
                    "YQ": "Fuel Surcharge (YQ)",
                    "YR": "Carrier Surcharge (YR)",
                    "IN": "India Entry Tax (IN)",
                    "WO": "Welfare Cess (WO)",
                    "JN": "Service Tax (JN)",
                    "P2": "Passenger Service Fee (P2)",
                    "SQ": "Security Tax (SQ)",
                    "K3": "Education Cess (K3)",
                    "XT": "Combined Tax (XT)",
                    "E7": "Passenger Facility Charge (E7)",
                    "MY": "Malaysia Departure Tax (MY)",
                    "D8": "Domestic Departure Tax (D8)",
                    "OO": "Malaysia Aviation Levy (OO)",
                    "F6": "Passenger Service Charge (F6)",
                    "MF": "Malaysia Tourism Tax (MF)",
                    "WY": "Oman Airport Tax (WY)",
                    "O4": "Oman Tourism Tax (O4)",
                    "SA": "S.Africa Departure Tax (SA)",
                    "ZR": "Zimbabwe Tax (ZR)",
                }

                # Travelport returns TWO PriceBreakdown entries per pax type:
                #   1) Filed amount in USD  (no currencySource / currencySource absent)
                #   2) Charged amount in LKR (currencySource = "Charged")
                # We want the CHARGED (LKR) entry for display in local currency.
                # Group by requestedPassengerType, pick the Charged one.

                charged_by_ptc = {}   # ptc → charged Amount block
                usd_by_ptc     = {}   # ptc → USD filed Amount block
                fare_calc_by_ptc = {} # ptc → FareCalculation string

                for pb in price.get("PriceBreakdown", []):
                    ptc     = pb.get("requestedPassengerType", pb.get("passengerTypeCode", "ADT")).upper()
                    amounts = pb.get("Amount", {})
                    currency_source = amounts.get("currencySource", "")
                    fare_calc = pb.get("FareCalculation", "")

                    if currency_source == "Charged":
                        charged_by_ptc[ptc] = amounts
                        if fare_calc:
                            fare_calc_by_ptc[ptc] = fare_calc
                    else:
                        # USD filed amount
                        usd_by_ptc[ptc] = amounts

                for ptc, amounts in charged_by_ptc.items():
                    # Base fare per pax
                    base_per_pax = float(amounts.get("Base", 0))

                    # Total per pax (base + taxes)
                    total_per_pax = float(amounts.get("Total", base_per_pax))

                    # Individual taxes from Taxes.Tax[]
                    taxes_block  = amounts.get("Taxes", {})
                    total_taxes  = float(taxes_block.get("TotalTaxes", 0))
                    tax_entries  = taxes_block.get("Tax", [])

                    individual_taxes = []
                    # Tax can be: list of dicts, a string " ", or None
                    # Handle all cases safely
                    if isinstance(tax_entries, list):
                        for tx in tax_entries:
                            if not isinstance(tx, dict):
                                continue
                            code = tx.get("taxCode", tx.get("code", ""))
                            try:
                                val = float(tx.get("value", tx.get("amount", 0)))
                            except (ValueError, TypeError):
                                val = 0.0
                            if code:  # only add if we have a code
                                individual_taxes.append({
                                    "code":        code,
                                    "description": TAX_CODE_LABELS.get(code, f"Tax ({code})"),
                                    "amount":      round(val, 2),
                                    "currency":    tx.get("currencyCode", invoice["currency"]),
                                })
                    # else: Tax is a string/None — no individual breakdown available

                    # Filed USD amount (for reference)
                    filed_usd = None
                    if ptc in usd_by_ptc:
                        filed_usd = float(usd_by_ptc[ptc].get("Base", 0))

                    invoice["price_breakdown"][ptc] = {
                        "quantity":         1,   # each entry is for 1 pax
                        "passenger_type":   ptc,
                        "passenger_label":  {"ADT": "Adult", "CNN": "Child", "INF": "Infant"}.get(ptc, ptc),
                        "base_price":       round(base_per_pax, 2),
                        "taxes_total":      round(total_taxes, 2),
                        "total_price":      round(total_per_pax, 2),
                        "subtotal":         round(total_per_pax, 2),
                        "individual_taxes": individual_taxes,
                        "fare_calculation": fare_calc_by_ptc.get(ptc, ""),
                        "filed_usd_base":   filed_usd,
                        "source":           "Travelport PriceBreakdownAir (currencySource=Charged)",
                    }

                logger.info(
                    f"[INVOICE] Price breakdown parsed: "
                    f"{list(invoice['price_breakdown'].keys())} "
                    f"| TotalPrice={invoice['total_fare']} {invoice['currency']}"
                )




            # Baggage

            for term in offer.get("TermsAndConditionsFull", []):
                baggage_list = term.get("BaggageAllowance", [])
                if baggage_list:
                    bag_text = baggage_list[0].get("Text", [])
                    if bag_text:
                        invoice["baggage_allowance"] = bag_text[0]
                    break

        # -- SECTION 3: PNR Receipts -------------------------------------------
        for receipt in reservation.get("Receipt", []):
            confirmation  = receipt.get("Confirmation", {})
            locator_info  = confirmation.get("Locator", {})
            source        = locator_info.get("source", "")
            value         = locator_info.get("value", "")
            creation_date = locator_info.get("creationDate", "") or receipt.get("creationDate", "")

            if source == "1G" or value == locator_code:
                invoice["agency_pnr"] = value or locator_code
                if creation_date:
                    invoice["booking_date"] = creation_date
            elif source and value:
                invoice["airline_pnr"]        = value
                invoice["airline_pnr_source"] = source

        # -- SECTION 4: E-Ticket Number ----------------------------------------
        issued_tickets = reservation.get("Ticket", [])
        if issued_tickets:
            invoice["ticket_number"] = issued_tickets[0].get("number", None)
            invoice["status"]        = "Ticketed"

        # -- SECTION 5: Seat Assignments ---------------------------------------
        for seat in reservation.get("Seat", []):
            invoice["seat_assignments"].append({
                "row":    seat.get("row", ""),
                "column": seat.get("column", ""),
                "seat":   f"{seat.get('row', '')}{seat.get('column', '')}",
            })

    except Exception as e:
        logger.error(f"[INVOICE] Parse error for {locator_code}: {e}", exc_info=True)
        raise ValueError(f"Failed to parse Travelport reservation response: {e}")

    logger.info(
        f"[INVOICE] Done: {len(invoice['all_passengers'])} pax, "
        f"{len(invoice['segments'])} segments, "
        f"{invoice['total_fare']} {invoice['currency']}"
    )
    return invoice

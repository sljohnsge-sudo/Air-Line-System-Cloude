"""
services/search_service.py
===========================
STEP 2 — Flight Search (Catalog Product Offerings)
Searches available flights via the Travelport catalog endpoint.

All data comes live from Travelport — no mock/seed data is used.
To update search request structure: modify only this file.

API Reference:
  POST https://api.pp.travelport.net/11/air/catalog/search/catalogproductofferings
  Schema: CatalogProductOfferingsQueryRequest (TripServices v11)
  
Correct payload schema (from Travelport developer portal):
  PassengerCriteria is an ARRAY with {number, age, passengerTypeCode}
  SearchCriteriaFlight is an ARRAY with {departureDate, From:{value}, To:{value}}
"""

import httpx
import logging
import re
from datetime import datetime
from typing import Optional
from config.travelport_config import TravelportConfig
from config.api_endpoints import TravelportEndpoints
from services.auth_service import get_auth_headers, invalidate_token

logger = logging.getLogger(__name__)


IATA_AIRLINE_NAMES = {
    "AI": "Air India",           "EK": "Emirates",             "QR": "Qatar Airways",
    "EY": "Etihad Airways",      "SQ": "Singapore Airlines",   "UL": "SriLankan Airlines",
    "MH": "Malaysia Airlines",   "TG": "Thai Airways",         "CX": "Cathay Pacific",
    "BA": "British Airways",     "LH": "Lufthansa",            "AF": "Air France",
    "KL": "KLM",                 "TK": "Turkish Airlines",     "MS": "EgyptAir",
    "ET": "Ethiopian Airlines",  "KE": "Korean Air",           "OZ": "Asiana Airlines",
    "NH": "All Nippon Airways",  "JL": "Japan Airlines",       "CA": "Air China",
    "MU": "China Eastern",       "CZ": "China Southern",       "FZ": "flydubai",
    "G9": "Air Arabia",          "WY": "Oman Air",             "GF": "Gulf Air",
    "SV": "Saudi Arabian Airlines","ME": "Middle East Airlines","RJ": "Royal Jordanian",
    "AA": "American Airlines",   "UA": "United Airlines",      "DL": "Delta Air Lines",
    "WN": "Southwest Airlines",  "AC": "Air Canada",           "QF": "Qantas",
    "VA": "Virgin Australia",    "NZ": "Air New Zealand",      "SA": "South African Airways",
    "KQ": "Kenya Airways",       "RB": "Syrian Arab Airlines", "PC": "Pegasus Airlines",
    "FR": "Ryanair",             "U2": "easyJet",              "VY": "Vueling",
    "W6": "Wizz Air",            "6E": "IndiGo",               "SG": "SpiceJet",
    "IX": "Air India Express",   "UK": "Vistara",              "G8": "Go First",
    "S1": "Air Arabia Abu Dhabi","XY": "flynas",               "F3": "Flyadeal",
    "OD": "Batik Air Malaysia",  "AK": "AirAsia",              "FD": "Thai AirAsia",
    "QZ": "Indonesia AirAsia",   "D7": "AirAsia X",            "XT": "Indonesia AirAsia X",
    "5J": "Cebu Pacific",        "PR": "Philippine Airlines",  "GA": "Garuda Indonesia",
    "JT": "Lion Air",            "SL": "Thai Lion Air",        "TR": "Scoot",
    "3K": "Jetstar Asia",        "GK": "Jetstar Japan",        "BX": "Air Busan",
    "LX": "Swiss International", "OS": "Austrian Airlines",    "SN": "Brussels Airlines",
    "AZ": "ITA Airways",         "IB": "Iberia",               "VU": "Vietravel Airlines",
    "VN": "Vietnam Airlines",    "QH": "Bamboo Airways",       "SZ": "Somon Air",
}

TRAVELPORT_SEAT_MAP_SUPPORTED_CARRIERS = {
    # Middle East
    "EK", "QR", "EY", "FZ", "G9", "WY", "GF", "SV", "ME", "RJ", "XY",
    # South Asia
    "UL", "AI", "UK", "6E", "SG", "IX",
    # Europe
    "BA", "LH", "AF", "KL", "TK", "LX", "OS", "SN", "AZ", "IB", "VY", "FR", "U2", "W6",
    # North America
    "AA", "UA", "DL", "AC", "WN",
    # Asia-Pacific
    "SQ", "CX", "MH", "TG", "GA", "PR", "NH", "JL", "KE", "OZ", "CA", "MU", "CZ", "QF", "NZ", "TR", "AK", "FD", "5J",
    # Africa
    "ET", "KQ", "SA", "MS"
}


def parse_iso_duration(dur_str: str) -> int:
    """Parse ISO 8601 duration string (e.g. PT4H30M) into total minutes."""
    if not dur_str:
        return 0
    s = dur_str.replace("PT", "")
    hours = 0
    minutes = 0
    h_match = re.search(r"(\d+)H", s)
    m_match = re.search(r"(\d+)M", s)
    if h_match:
        hours = int(h_match.group(1))
    if m_match:
        minutes = int(m_match.group(1))
    return hours * 60 + minutes


def parse_naive_datetime(dt_str: str) -> datetime:
    """Parse ISO datetime string to naive datetime, ignoring timezone offsets."""
    if not dt_str:
        return datetime.now()
    cleaned = dt_str.replace(" ", "T")
    # Match standard ISO format YYYY-MM-DDTHH:MM:SS or YYYY-MM-DDTHH:MM
    match = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}|\d{2}:\d{2})", cleaned)
    if match:
        dt_part = f"{match.group(1)}T{match.group(2)}"
        if len(match.group(2)) == 5:
            dt_part += ":00"
        try:
            return datetime.strptime(dt_part, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
    try:
        # Fallback using fromisoformat and stripping offset
        dt = datetime.fromisoformat(cleaned)
        return dt.replace(tzinfo=None)
    except Exception:
        return datetime.now()


def minutes_to_iso_duration(total_minutes: int) -> str:
    """Convert total minutes back into an ISO 8601 duration string (e.g. PT4H30M)."""
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0 and minutes > 0:
        return f"PT{hours}H{minutes}M"
    elif hours > 0:
        return f"PT{hours}H"
    else:
        return f"PT{minutes}M"



def search_flights(
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    departure_date: Optional[str] = None,
    adult_count: int = 1,
    child_count: int = 0,
    infant_count: int = 0,
    cabin_preference: Optional[str] = None,
    legs: Optional[list] = None
) -> dict:
    """
    STEP 2: Search for available flights from Travelport.

    Args:
        origin (str): IATA airport code for departure (e.g. "CMB")
        destination (str): IATA airport code for arrival (e.g. "DXB")
        departure_date (str): ISO date string YYYY-MM-DD (e.g. "2026-10-01")
        adult_count (int): Number of adult passengers (default 1)
        child_count (int): Number of child passengers (default 0)
        infant_count (int): Number of infant passengers (default 0)
        cabin_preference (str|None): "Economy", "Business", "First" — or None for all
        legs (list|None): List of flight search legs for round-trip or multi-city

    Returns:
        dict: Raw Travelport catalog search response (parsed JSON)

    Raises:
        httpx.HTTPStatusError: on API errors
        Exception: on network failures
    """
    if legs:
        leg_str = " | ".join([f"{l.get('origin')}->{l.get('destination')} on {l.get('departure_date') or l.get('date')}" for l in legs])
        logger.info(f"Searching flights: {leg_str}, Adults={adult_count}, Children={child_count}, Infants={infant_count}")
    else:
        logger.info(f"Searching flights: {origin} -> {destination} on {departure_date}, "
                    f"Adults={adult_count}, Children={child_count}, Infants={infant_count}")

    # ── Passenger Criteria (array format per Travelport v11 spec) ─────────────
    passenger_criteria = []
    if adult_count > 0:
        passenger_criteria.append({
            "@type": "PassengerCriteria",
            "number": adult_count,
            "age": 25,
            "passengerTypeCode": "ADT"
        })
    if child_count > 0:
        passenger_criteria.append({
            "@type": "PassengerCriteria",
            "number": child_count,
            "age": 10,
            "passengerTypeCode": "CNN"
        })
    if infant_count > 0:
        passenger_criteria.append({
            "@type": "PassengerCriteria",
            "number": infant_count,
            "age": 1,
            "passengerTypeCode": "INF"
        })

    # ── SearchCriteriaFlight (per Travelport v11 spec) ─────────────────────────
    search_criteria_flight = []
    if legs:
        for leg in legs:
            dep_dt = leg.get("departure_date") or leg.get("date")
            search_criteria_flight.append({
                "@type": "SearchCriteriaFlight",
                "departureDate": dep_dt,
                "From": {"value": (leg.get("origin") or "").upper()},
                "To": {"value": (leg.get("destination") or "").upper()}
            })
    else:
        search_criteria_flight = [
            {
                "@type": "SearchCriteriaFlight",
                "departureDate": departure_date,
                "From": {"value": origin.upper()},
                "To": {"value": destination.upper()}
            }
        ]

    # ── Full Travelport v11 Payload ────────────────────────────────────────────
    # Per official Travelport TripServices v11 API documentation
    payload = {
        "@type": "CatalogProductOfferingsQueryRequest",
        "CatalogProductOfferingsRequest": {
            "@type": "CatalogProductOfferingsRequestAir",
            "maxNumberOfUpsellsToReturn": 1,
            "offersPerPage": 50,
            "contentSourceList": ["GDS", "NDC", "APIPAC"],
            "PassengerCriteria": passenger_criteria,
            "SearchCriteriaFlight": search_criteria_flight
        }
    }

    # Add cabin preference if specified
    if cabin_preference:
        cabin_map = {
            "Economy": "Economy",
            "PremiumEconomy": "PremiumEconomy",
            "Business": "Business",
            "First": "First"
        }
        cabin_val = cabin_map.get(cabin_preference)
        if cabin_val:
            payload["CatalogProductOfferingsRequest"]["SearchModifiersAir"] = [
                {
                    "@type": "SearchModifiersAir",
                    "cabinPreference": cabin_val
                }
            ]

    headers = get_auth_headers()
    logger.info(f"Request payload: {payload}")

    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT) as client:
        try:
            response = client.post(
                TravelportEndpoints.FLIGHT_SEARCH,
                json=payload,
                headers=headers
            )

            # Auto-retry on token expiry
            if response.status_code == 401:
                invalidate_token()
                response = client.post(
                    TravelportEndpoints.FLIGHT_SEARCH,
                    json=payload,
                    headers=get_auth_headers()
                )

            # Detailed error logging
            if response.status_code != 200:
                try:
                    err_data = response.json()
                    errors = (
                        err_data.get("CatalogProductOfferingsResponse", {})
                               .get("Result", {})
                               .get("Error", [])
                    )
                    trace_id = err_data.get("CatalogProductOfferingsResponse", {}).get("traceId", "N/A")
                    for err in errors:
                        logger.error(
                            f"Travelport error [{err.get('SourceCode')}]: "
                            f"{err.get('Message')} | category: {err.get('category')} | TraceId: {trace_id}"
                        )
                except Exception:
                    logger.error(f"Raw error response: {response.text[:500]}")

            response.raise_for_status()
            result = response.json()
            logger.info("Flight search completed successfully.")
            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"Flight search API error: {e.response.status_code} -- {e.response.text[:500]}")
            raise
        except Exception as e:
            logger.error(f"Flight search network error: {e}")
            raise


def parse_baggage_allowance(terms: dict, prod_refs: list) -> list:
    if not terms:
        return []
    parsed_bags = []
    baggage_allowances = terms.get("BaggageAllowance", [])
    for detail in baggage_allowances:
        detail_product_refs = detail.get("ProductRef", [])
        if not any(r in prod_refs for r in detail_product_refs):
            continue
        
        baggage_type = detail.get("baggageType", "")
        display_type = baggage_type
        if baggage_type == "CarryOn":
            display_type = "Carry-on Baggage"
        elif baggage_type == "FirstCheckedBag":
            display_type = "Checked Baggage"
            
        items_desc = []
        for item in detail.get("BaggageItem", []):
            desc = ""
            quantity = item.get("quantity")
            measurements = item.get("Measurement", [])
            text = item.get("Text")
            
            if measurements:
                meas = measurements[0]
                value = meas.get("value")
                unit = meas.get("unit")
                unit_display = unit
                if unit == "Kilograms":
                    unit_display = "kg"
                elif unit == "Pounds":
                    unit_display = "lbs"
                desc = f"{value} {unit_display}"
            elif quantity:
                desc = f"{quantity} pc" + ("s" if quantity > 1 else "")
            elif text:
                desc = text
                
            if desc:
                items_desc.append(desc)
                
        if items_desc:
            parsed_bags.append({
                "type": display_type,
                "allowance": " + ".join(items_desc)
            })
    return parsed_bags


def parse_penalties(terms: dict, prod_refs: list) -> tuple:
    if not terms:
        return "Rules not provided", "Rules not provided"
    
    change_desc = "Rules not provided"
    cancel_desc = "Rules not provided"
    
    penalties_list = terms.get("Penalties", [])
    for penalty_group in penalties_list:
        # Check Change
        changes = penalty_group.get("Change", [])
        for chg in changes:
            chg_prod_refs = chg.get("ProductRefs", [])
            if any(r in prod_refs for r in chg_prod_refs):
                chg_types = chg.get("penaltyTypes", [])
                penalty_list = chg.get("Penalty", [])
                
                fee_str = ""
                if penalty_list:
                    p_detail = penalty_list[0]
                    amount_info = p_detail.get("Amount", {})
                    if amount_info:
                        val = amount_info.get("value", 0)
                        code = amount_info.get("code", "LKR")
                        if val == 0:
                            fee_str = "Free"
                        else:
                            fee_str = f"Fee: {code} {val:,}"
                
                type_desc = ", ".join(chg_types) if chg_types else ""
                if fee_str:
                    change_desc = f"Permitted ({fee_str})" + (f" - {type_desc}" if type_desc else "")
                else:
                    change_desc = f"Permitted" + (f" - {type_desc}" if type_desc else "")
                    
        # Check Cancel
        cancels = penalty_group.get("Cancel", [])
        for cnc in cancels:
            cnc_prod_refs = cnc.get("ProductRefs", [])
            if any(r in prod_refs for r in cnc_prod_refs):
                cnc_types = cnc.get("penaltyTypes", [])
                penalty_list = cnc.get("Penalty", [])
                
                fee_str = ""
                if penalty_list:
                    p_detail = penalty_list[0]
                    amount_info = p_detail.get("Amount", {})
                    if amount_info:
                        val = amount_info.get("value", 0)
                        code = amount_info.get("code", "LKR")
                        if val == 0:
                            fee_str = "Free"
                        else:
                            fee_str = f"Fee: {code} {val:,}"
                
                type_desc = ", ".join(cnc_types) if cnc_types else ""
                if fee_str:
                    cancel_desc = f"Permitted ({fee_str})" + (f" - {type_desc}" if type_desc else "")
                else:
                    cancel_desc = f"Permitted" + (f" - {type_desc}" if type_desc else "")
                    
    return change_desc, cancel_desc


def parse_flight_offers(raw_response: dict, legs: Optional[list] = None) -> list[dict]:
    """
    Parse Travelport v11 catalog search response into simplified flight offer objects.

    Args:
        raw_response (dict): Raw Travelport catalog search response.
        legs (list|None): The search legs originally requested. When exactly 2 legs are
            given and the second is the exact reverse of the first (a round trip), outbound
            and return offers are paired by CombinabilityCode into merged itinerary objects
            (see pair_round_trip_offers). Otherwise (one-way, or 3+ legs / multi-city) the
            flat per-direction offer list is returned unchanged.
    """
    offers = []
    try:
        catalog = raw_response.get("CatalogProductOfferingsResponse", {})

        # Build a flight lookup dict from ReferenceList
        flight_map = {}  # id -> FlightDetail dict
        for ref_entry in catalog.get("ReferenceList", []):
            if ref_entry.get("@type") == "ReferenceListFlight":
                for flight in ref_entry.get("Flight", []):
                    flight_map[flight.get("id")] = flight

        # Build airline name + logo lookup from ReferenceList
        airline_map = {}       # code -> name
        airline_logo_map = {}  # code -> imageURL (if Travelport provides it)
        for ref_entry in catalog.get("ReferenceList", []):
            if ref_entry.get("@type") == "ReferenceListAirline":
                for airline in ref_entry.get("Airline", []):
                    code = airline.get("iataCode", "")
                    if code:
                        airline_map[code] = airline.get("name", code)
                        logo = (
                            airline.get("imageURL") or
                            airline.get("logoURL") or
                            airline.get("logoUrl") or
                            airline.get("image") or
                            ""
                        )
                        airline_logo_map[code] = logo

        # Merge: Travelport data takes priority; static fallback fills the gaps
        for code, name in IATA_AIRLINE_NAMES.items():
            if code not in airline_map:
                airline_map[code] = name

        # Process each CatalogProductOffering
        offerings_block = catalog.get("CatalogProductOfferings", {})
        catalog_offerings_id = offerings_block.get("Identifier", {}).get("value")
        product_offerings = offerings_block.get("CatalogProductOffering", [])

        # IATA equipment code → human-readable aircraft name mapping
        AIRCRAFT_CODE_MAP = {
            "77W": "Boeing 777-300ER", "773": "Boeing 777-300", "772": "Boeing 777-200",
            "77L": "Boeing 777F",      "744": "Boeing 747-400", "74F": "Boeing 747-400F",
            "789": "Boeing 787-9",     "788": "Boeing 787-8",   "781": "Boeing 787-10",
            "333": "Airbus A330-300",  "332": "Airbus A330-200","338": "Airbus A330-800neo",
            "339": "Airbus A330-900neo","351": "Airbus A350-1000","359": "Airbus A350-900",
            "388": "Airbus A380-800",  "321": "Airbus A321",    "32N": "Airbus A321neo",
            "320": "Airbus A320",      "32A": "Airbus A320neo", "319": "Airbus A319",
            "73H": "Boeing 737-800",   "738": "Boeing 737-800", "737": "Boeing 737-700",
            "739": "Boeing 737-900",   "7M8": "Boeing 737 MAX 8","7M9": "Boeing 737 MAX 9",
            "E90": "Embraer E190",     "E95": "Embraer E195",   "E75": "Embraer E175",
            "AT7": "ATR 72",           "AT4": "ATR 42",         "DH8": "Bombardier Dash 8",
            "CR9": "Bombardier CRJ900","CR7": "Bombardier CRJ700","100": "Fokker 100",
        }

        # Build a brand lookup map and brand attributes map from ReferenceList
        brand_map = {}
        brand_attrs_map = {}
        for ref_entry in catalog.get("ReferenceList", []):
            if ref_entry.get("@type") == "ReferenceListBrand":
                for b in ref_entry.get("Brand", []):
                    brand_id = b.get("id")
                    brand_map[brand_id] = b.get("name", "")
                    attrs = []
                    for attr in b.get("BrandAttribute", []):
                        classif = attr.get("classification")
                        incl = attr.get("inclusion")
                        if classif and incl:
                            attrs.append({
                                "classification": classif,
                                "inclusion": incl
                            })
                    brand_attrs_map[brand_id] = attrs

        # Build product brand reference, cabin, fare basis, and class of service maps
        product_brand_map = {}
        product_cabin_map = {}
        product_fare_basis_map = {}
        product_cos_map = {}
        for ref_entry in catalog.get("ReferenceList", []):
            if ref_entry.get("@type") == "ReferenceListProduct":
                for product in ref_entry.get("Product", []):
                    prod_id = product.get("id")
                    brand_ref = None
                    cabin_val = "Economy"
                    fare_basis_list = []
                    cos_list = []
                    try:
                        for pf in product.get("PassengerFlight", []):
                            for fp in pf.get("FlightProduct", []):
                                br = fp.get("Brand", {}).get("BrandRef")
                                if br:
                                    brand_ref = br
                                c = fp.get("cabin")
                                if c:
                                    cabin_val = c
                                fbc = fp.get("fareBasisCode")
                                if fbc and fbc not in fare_basis_list:
                                    fare_basis_list.append(fbc)
                                cos = fp.get("classOfService")
                                if cos and cos not in cos_list:
                                    cos_list.append(cos)
                    except Exception:
                        pass
                    if brand_ref:
                        product_brand_map[prod_id] = brand_ref
                    product_cabin_map[prod_id] = cabin_val
                    product_fare_basis_map[prod_id] = fare_basis_list
                    product_cos_map[prod_id] = cos_list

        # Build terms lookup map from ReferenceList
        terms_map = {}
        for ref_entry in catalog.get("ReferenceList", []):
            if ref_entry.get("@type") == "ReferenceListTermsAndConditions":
                for t in ref_entry.get("TermsAndConditions", []):
                    terms_map[t.get("id")] = t

        # We will group by segment key:
        grouped_offers = {} # seg_key -> offer dict

        for offering in product_offerings:
            offer_id = offering.get("id", "")
            departure_airport = offering.get("Departure", "")
            arrival_airport = offering.get("Arrival", "")

            for brand_options in offering.get("ProductBrandOptions", []):
                flight_refs = brand_options.get("flightRefs", [])
                if not flight_refs:
                    continue

                # Parse segments for this brand option
                all_segments = []
                for ref in flight_refs:
                    f_seg = flight_map.get(ref)
                    if f_seg:
                        all_segments.append(f_seg)
                if not all_segments:
                    continue

                first_flight = all_segments[0]
                last_flight = all_segments[-1]

                carrier = first_flight.get("carrier", "")
                number = first_flight.get("number", "")
                dep_info = first_flight.get("Departure", {})
                arr_info = last_flight.get("Arrival", {})

                # Map segments for the frontend
                segments_list = []
                for f_seg in all_segments:
                    seg_carrier = f_seg.get("carrier", "")
                    seg_number = f_seg.get("number", "")
                    seg_dep_info = f_seg.get("Departure", {})
                    seg_arr_info = f_seg.get("Arrival", {})
                    seg_duration = f_seg.get("duration", "")
                    
                    seg_equip = f_seg.get("equipment", {})
                    seg_aircraft_type = ""
                    if isinstance(seg_equip, dict):
                        seg_aircraft_code = seg_equip.get("aircraftCode", seg_equip.get("code", ""))
                        seg_aircraft_type = (
                            AIRCRAFT_CODE_MAP.get(seg_aircraft_code.upper(), "") or
                            seg_equip.get("name", "") or
                            seg_equip.get("aircraftType", "") or
                            seg_aircraft_code
                        )
                    elif isinstance(seg_equip, str) and seg_equip:
                        seg_aircraft_type = AIRCRAFT_CODE_MAP.get(seg_equip.upper(), seg_equip)

                    segments_list.append({
                        "carrier": seg_carrier,
                        "carrier_name": airline_map.get(seg_carrier, seg_carrier),
                        "flight_number": f"{seg_carrier}{seg_number}",
                        "departure_airport": seg_dep_info.get("location", ""),
                        "arrival_airport": seg_arr_info.get("location", ""),
                        "departure_time": seg_dep_info.get("date", "") + "T" + seg_dep_info.get("time", ""),
                        "arrival_time": seg_arr_info.get("date", "") + "T" + seg_arr_info.get("time", ""),
                        "duration": seg_duration,
                        "aircraft_type": seg_aircraft_type,
                    })

                # Calculate layovers
                for i in range(len(segments_list) - 1):
                    arr_time_str = segments_list[i]["arrival_time"]
                    dep_time_str = segments_list[i+1]["departure_time"]
                    arr_dt = parse_naive_datetime(arr_time_str)
                    dep_dt = parse_naive_datetime(dep_time_str)
                    layover_delta = dep_dt - arr_dt
                    layover_minutes = int(layover_delta.total_seconds() / 60)
                    segments_list[i]["layover_minutes"] = max(0, layover_minutes)

                # Calculate total duration
                total_minutes = 0
                for seg in segments_list:
                    total_minutes += parse_iso_duration(seg["duration"])
                    total_minutes += seg.get("layover_minutes", 0)
                duration = minutes_to_iso_duration(total_minutes)

                # Stops
                stops = (len(all_segments) - 1) + sum(len(f.get("IntermediateStop", [])) for f in all_segments)

                # Unique key for segment combinations
                seg_key = "-".join([f"{seg['flight_number']}_{seg['departure_time']}" for seg in segments_list])

                # Parse all ProductBrandOffering for this brand option
                for brand_offering in brand_options.get("ProductBrandOffering", []):
                    best_price = brand_offering.get("BestCombinablePrice", {})
                    if not best_price:
                        continue

                    currency_code = best_price.get("CurrencyCode", {}).get("value", "LKR")
                    total_price = float(best_price.get("TotalPrice", 0))

                    prod_refs = [p.get("productRef") for p in brand_offering.get("Product", []) if p.get("productRef")]
                    brand_id = None
                    brand_name = None
                    cabin = "Economy"
                    fare_basis_codes = []
                    classes_of_service = []
                    if prod_refs:
                        first_prod = prod_refs[0]
                        brand_id = product_brand_map.get(first_prod)
                        if brand_id:
                            brand_name = brand_map.get(brand_id)
                        cabin = product_cabin_map.get(first_prod, "Economy")
                        for pr in prod_refs:
                            fbc_list = product_fare_basis_map.get(pr, [])
                            for fbc in fbc_list:
                                if fbc not in fare_basis_codes:
                                    fare_basis_codes.append(fbc)
                            cos_list = product_cos_map.get(pr, [])
                            for cos in cos_list:
                                if cos not in classes_of_service:
                                    classes_of_service.append(cos)

                    display_brand_name = brand_name or "Standard"

                    # Clean brand name mapping if EK or QR prefixes
                    for prefix in ["EK", "QR", "AI"]:
                        if display_brand_name.startswith(prefix):
                            display_brand_name = display_brand_name.replace(prefix, "").strip()

                    # Extract terms and conditions details
                    tc_ref = brand_offering.get("TermsAndConditions", {}).get("termsAndConditionsRef")
                    matched_terms = terms_map.get(tc_ref) if tc_ref else None
                    baggage_allowance = parse_baggage_allowance(matched_terms, prod_refs)
                    change_policy, cancel_policy = parse_penalties(matched_terms, prod_refs)
                    brand_attributes = brand_attrs_map.get(brand_id, []) if brand_id else []

                    price_breakdown = {}
                    pb_list = best_price.get("PriceBreakdown", [])
                    for pb in pb_list:
                        ptc = pb.get("requestedPassengerType")
                        amount_dict = pb.get("Amount", {})
                        if ptc and amount_dict:
                            base_val = float(amount_dict.get("Base", 0))
                            tax_val = float(amount_dict.get("Taxes", {}).get("TotalTaxes", 0))
                            fees_val = float(amount_dict.get("Fees", {}).get("TotalFees", 0))
                            single_total = float(amount_dict.get("Total", base_val + tax_val + fees_val))
                            qty = int(pb.get("quantity", 1))
                            price_breakdown[ptc] = {
                                "base_price": base_val,
                                "taxes": tax_val,
                                "fees": fees_val,
                                "single_price": single_total,
                                "quantity": qty,
                                "total_price": single_total * qty
                            }

                    selected_raw_offering = {
                        **offering,
                        "ProductBrandOptions": [brand_options]
                    }
                    selected_raw_offering["CatalogProductOfferingsIdentifier"] = catalog_offerings_id

                    fare_opt = {
                        "brand_name": display_brand_name,
                        "has_brand": brand_name is not None,
                        "cabin_class": cabin,
                        "price": total_price,
                        "currency": currency_code,
                        "price_breakdown": price_breakdown,
                        "raw_offering": selected_raw_offering,
                        "fare_basis_codes": fare_basis_codes,
                        "classes_of_service": classes_of_service,
                        "baggage_allowance": baggage_allowance,
                        "change_policy": change_policy,
                        "cancel_policy": cancel_policy,
                        "brand_attributes": brand_attributes,
                        "combinability_codes": brand_offering.get("CombinabilityCode", [])
                    }

                    # Add or update in grouped_offers
                    if seg_key not in grouped_offers:
                        airline_name = airline_map.get(carrier, carrier)
                        airline_logo_url = airline_logo_map.get(carrier, "")
                        aircraft_type = segments_list[0]["aircraft_type"] if segments_list else ""
                        seat_map_heuristic = carrier in TRAVELPORT_SEAT_MAP_SUPPORTED_CARRIERS
                        source_code = brand_offering.get("ContentSource") or offering.get("ContentSource") or "GDS"
                        fare_source = "LCC" if source_code == "APIPAC" else source_code

                        grouped_offers[seg_key] = {
                            "offer_id": offer_id,
                            "flight_number": f"{carrier}{number}",
                            "airline": airline_name,
                            "airline_code": carrier,
                            "airline_logo_url": airline_logo_url,
                            "aircraft_type": aircraft_type,
                            "departure_airport": dep_info.get("location", departure_airport),
                            "arrival_airport": arr_info.get("location", arrival_airport),
                            "departure_time": dep_info.get("date", "") + "T" + dep_info.get("time", ""),
                            "arrival_time": arr_info.get("date", "") + "T" + arr_info.get("time", ""),
                            "duration": duration,
                            "stops": stops,
                            "cabin_class": cabin,
                            "price": total_price,
                            "currency": currency_code,
                            "price_breakdown": price_breakdown,
                            "seats_remaining": None,
                            "seat_map_heuristic": seat_map_heuristic,
                            "fare_source": fare_source,
                            "segments": segments_list,
                            "fare_options": [fare_opt],
                            "raw_offering": selected_raw_offering
                        }
                    else:
                        # Append options (make sure we don't add exact duplicate prices with the same brand name)
                        exists = any(
                            x["brand_name"] == fare_opt["brand_name"] and
                            x["cabin_class"] == fare_opt["cabin_class"] and
                            abs(x["price"] - fare_opt["price"]) < 1.0
                            for x in grouped_offers[seg_key]["fare_options"]
                        )
                        if not exists:
                            grouped_offers[seg_key]["fare_options"].append(fare_opt)

        offers = list(grouped_offers.values())
        for o in offers:
            o["fare_options"].sort(key=lambda x: x["price"])
            cheapest = o["fare_options"][0]
            o["price"] = cheapest["price"]
            o["currency"] = cheapest["currency"]
            o["cabin_class"] = cheapest["cabin_class"]
            o["price_breakdown"] = cheapest["price_breakdown"]
            o["raw_offering"] = cheapest["raw_offering"]

    except Exception as e:
        logger.error(f"Error parsing flight offers: {e}", exc_info=True)
        return offers

    # ── Round-trip pairing ──────────────────────────────────────────────────
    # Only when exactly 2 legs were requested and the second leg is the exact
    # reverse of the first (a true round trip, not multi-city).
    if legs and len(legs) == 2:
        leg0_origin = (legs[0].get("origin") or "").upper()
        leg0_dest = (legs[0].get("destination") or "").upper()
        leg1_origin = (legs[1].get("origin") or "").upper()
        leg1_dest = (legs[1].get("destination") or "").upper()
        if leg0_origin and leg0_dest and leg0_origin == leg1_dest and leg0_dest == leg1_origin:
            try:
                paired = pair_round_trip_offers(offers, legs[0], legs[1])
                if paired:
                    return paired
            except Exception as e:
                logger.error(f"Error pairing round-trip offers: {e}", exc_info=True)

    return offers


def pair_round_trip_offers(offers: list[dict], outbound_leg: dict, inbound_leg: dict) -> list[dict]:
    """
    Pair outbound and return offers into merged round-trip itineraries using
    Travelport's own CombinabilityCode signal (offers sharing a code are priced
    together at BestCombinablePrice). Returns a list of merged itinerary dicts
    with a `legs` array of [outbound_offer, inbound_offer] and one combined price.
    """
    outbound_origin = (outbound_leg.get("origin") or "").upper()
    outbound_dest = (outbound_leg.get("destination") or "").upper()
    inbound_origin = (inbound_leg.get("origin") or "").upper()
    inbound_dest = (inbound_leg.get("destination") or "").upper()

    outbound_offers = [
        o for o in offers
        if (o.get("departure_airport") or "").upper() == outbound_origin
        and (o.get("arrival_airport") or "").upper() == outbound_dest
    ]
    inbound_offers = [
        o for o in offers
        if (o.get("departure_airport") or "").upper() == inbound_origin
        and (o.get("arrival_airport") or "").upper() == inbound_dest
    ]

    if not outbound_offers or not inbound_offers:
        return []

    # code -> list of (offer, fare_opt) sharing that CombinabilityCode
    outbound_code_map: dict = {}
    for o_offer in outbound_offers:
        for fo in o_offer.get("fare_options", []):
            for code in fo.get("combinability_codes", []):
                outbound_code_map.setdefault(code, []).append((o_offer, fo))

    inbound_code_map: dict = {}
    for i_offer in inbound_offers:
        for fo in i_offer.get("fare_options", []):
            for code in fo.get("combinability_codes", []):
                inbound_code_map.setdefault(code, []).append((i_offer, fo))

    shared_codes = set(outbound_code_map.keys()) & set(inbound_code_map.keys())
    MAX_PAIRS_PER_CODE = 10  # safety cap against pathological combinatorial fan-out

    # Group every valid (outbound offer, inbound offer) itinerary pairing together
    # first — same flight+segment combo pairing may be reachable via more than one
    # CombinabilityCode (different fare brands) and must land on ONE card with
    # multiple fare_options, exactly like the one-way grouped_offers pattern.
    itineraries: dict = {}  # (outbound offer_id, inbound offer_id) -> merged offer dict

    for code in shared_codes:
        outbound_candidates = outbound_code_map[code][:MAX_PAIRS_PER_CODE]
        inbound_candidates = inbound_code_map[code][:MAX_PAIRS_PER_CODE]

        for o_offer, o_fo in outbound_candidates:
            for i_offer, i_fo in inbound_candidates:
                itin_key = (o_offer.get("offer_id"), i_offer.get("offer_id"))

                price = o_fo.get("price", 0)
                currency = o_fo.get("currency", "LKR")
                merged_fare_opt = {
                    **o_fo,
                    "price": price,
                    "currency": currency,
                    "raw_offering": {
                        "outbound": o_fo.get("raw_offering"),
                        "inbound": i_fo.get("raw_offering")
                    }
                }

                if itin_key not in itineraries:
                    itineraries[itin_key] = {
                        **o_offer,
                        "is_round_trip": True,
                        "legs": [o_offer, i_offer],
                        "offer_id": f"{o_offer.get('offer_id', '')}_{i_offer.get('offer_id', '')}",
                        "price": price,
                        "currency": currency,
                        "fare_options": [merged_fare_opt],
                        "raw_offering": merged_fare_opt["raw_offering"]
                    }
                else:
                    existing_opts = itineraries[itin_key]["fare_options"]
                    exists = any(
                        x["brand_name"] == merged_fare_opt["brand_name"] and
                        x["cabin_class"] == merged_fare_opt["cabin_class"] and
                        abs(x["price"] - merged_fare_opt["price"]) < 1.0
                        for x in existing_opts
                    )
                    if not exists:
                        existing_opts.append(merged_fare_opt)

    merged = list(itineraries.values())
    for m in merged:
        m["fare_options"].sort(key=lambda x: x["price"])
        cheapest = m["fare_options"][0]
        m["price"] = cheapest["price"]
        m["currency"] = cheapest["currency"]
        m["raw_offering"] = cheapest["raw_offering"]

    merged.sort(key=lambda x: x["price"])
    return merged

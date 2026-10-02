"""
flight_search_service.py
=========================
Wraps Amadeus Web Services Fare_MasterPricerTravelBoardSearch (SOAP/XML),
confirmed against the real example in Amadeus's Examples Repository
("Fare_MasterPricerTravelBoardSearch with Multi-City option").

Builds a one-way or round-trip search request and parses the reply into a
flat list of priced flight offers.
"""

from datetime import date
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from services.amadeus_soap_client import call, AmadeusSoapError

FMPTBQ_NS = "http://xml.amadeus.com/FMPTBQ_24_6_1A"
FMPTBR_NS = "http://xml.amadeus.com/FMPTBR_24_6_1A"


def _itinerary_xml(seg_ref: int, origin: str, destination: str, dep_date: date) -> str:
    return f"""<itinerary>
        <requestedSegmentRef>
          <segRef>{seg_ref}</segRef>
        </requestedSegmentRef>
        <departureLocalization>
          <depMultiCity>
            <locationId>{escape(origin.upper())}</locationId>
          </depMultiCity>
        </departureLocalization>
        <arrivalLocalization>
          <arrivalMultiCity>
            <locationId>{escape(destination.upper())}</locationId>
          </arrivalMultiCity>
        </arrivalLocalization>
        <timeDetails>
          <firstDateTimeDetail>
            <date>{dep_date.strftime('%d%m%y')}</date>
          </firstDateTimeDetail>
        </timeDetails>
      </itinerary>"""


def _pax_xml(adults: int, children: int, infants: int) -> tuple[str, int]:
    # Infants are deliberately excluded from the search paxReference: Amadeus
    # Fare_MasterPricerTravelBoardSearch rejects "INF" as a requested
    # passenger type (confirmed live -- errorCode 955 "Invalid passenger
    # type code"). Infants aren't priced/seated in Master Pricer search;
    # they're added at the PNR stage instead (see flight_booking_service).
    blocks = []
    ref = 1
    refs_by_type = {}
    for ptc, count in (("ADT", adults), ("CHD", children)):
        if count <= 0:
            continue
        travellers = ""
        refs = []
        for _ in range(count):
            travellers += f"<traveller><ref>{ref}</ref></traveller>"
            refs.append(ref)
            ref += 1
        refs_by_type[ptc] = refs
        blocks.append(f"<paxReference><ptc>{ptc}</ptc>{travellers}</paxReference>")
    return "".join(blocks), ref - 1


async def search_flights(
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date | None = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    max_results: int = 20,
) -> dict:
    pax_xml, total_pax = _pax_xml(adults, children, infants)

    itineraries = _itinerary_xml(1, origin, destination, departure_date)
    if return_date:
        itineraries += _itinerary_xml(2, destination, origin, return_date)

    body = f"""<Fare_MasterPricerTravelBoardSearch xmlns="{FMPTBQ_NS}">
      <numberOfUnit>
        <unitNumberDetail>
          <numberOfUnits>{max_results}</numberOfUnits>
          <typeOfUnit>RC</typeOfUnit>
        </unitNumberDetail>
        <unitNumberDetail>
          <numberOfUnits>{total_pax}</numberOfUnits>
          <typeOfUnit>PX</typeOfUnit>
        </unitNumberDetail>
      </numberOfUnit>
      {pax_xml}
      <fareOptions>
        <pricingTickInfo>
          <pricingTicketing>
            <priceType>ET</priceType>
            <priceType>TAC</priceType>
            <priceType>RP</priceType>
            <priceType>RU</priceType>
            <priceType>XND</priceType>
          </pricingTicketing>
        </pricingTickInfo>
      </fareOptions>
      <travelFlightInfo/>
      {itineraries}
    </Fare_MasterPricerTravelBoardSearch>"""

    body_root = await call(
        operation="Fare_MasterPricerTravelBoardSearch",
        soap_action="http://webservices.amadeus.com/FMPTBQ_24_6_1A",
        body_xml=body,
        log_prefix="02_fare_mpt_search",
    )

    reply = body_root.find(f"{{{FMPTBR_NS}}}Fare_MasterPricerTravelBoardSearchReply")
    if reply is None:
        raise AmadeusSoapError("Unexpected Amadeus search response (no reply element)", status_code=502)

    error_el = reply.find(f"{{{FMPTBR_NS}}}errorSection") or reply.find("errorSection")
    if error_el is not None:
        raise AmadeusSoapError("Amadeus flight search returned an error", status_code=502)

    return _parse_reply(reply)


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find_all_local(parent: ET.Element, name: str):
    return [c for c in parent if _local(c.tag) == name]


def _find_local(parent: ET.Element, name: str):
    for c in parent:
        if _local(c.tag) == name:
            return c
    return None


def _text(parent: ET.Element, name: str, default=None):
    el = _find_local(parent, name) if parent is not None else None
    return el.text if el is not None and el.text else default


def _parse_flight_segment(flight_info: ET.Element) -> dict:
    pdt = _find_local(flight_info, "productDateTime")
    locations = _find_all_local(flight_info, "location")
    company = _find_local(flight_info, "companyId")
    product_detail = _find_local(flight_info, "productDetail")
    return {
        "departureDate": _text(pdt, "dateOfDeparture"),
        "departureTime": _text(pdt, "timeOfDeparture"),
        "arrivalDate": _text(pdt, "dateOfArrival"),
        "arrivalTime": _text(pdt, "timeOfArrival"),
        "from": _text(locations[0], "locationId") if len(locations) > 0 else None,
        "to": _text(locations[1], "locationId") if len(locations) > 1 else None,
        "marketingCarrier": _text(company, "marketingCarrier") if company is not None else None,
        "operatingCarrier": _text(company, "operatingCarrier") if company is not None else None,
        "flightNumber": _text(flight_info, "flightOrtrainNumber"),
        "equipment": _text(product_detail, "equipmentType") if product_detail is not None else None,
    }


def _parse_reply(reply: ET.Element) -> dict:
    currency = None
    conv = _find_local(reply, "conversionRate")
    if conv is not None:
        detail = _find_local(conv, "conversionRateDetail")
        if detail is not None:
            currency = _text(detail, "currency")

    # flightIndex[i] -> list of groupOfFlights (each a candidate set of segments for requestedSegmentRef i+1)
    flight_indexes = []
    for fi in _find_all_local(reply, "flightIndex"):
        groups = []
        for grp in _find_all_local(fi, "groupOfFlights"):
            segments = []
            for fd in _find_all_local(grp, "flightDetails"):
                info = _find_local(fd, "flightInformation")
                if info is not None:
                    segments.append(_parse_flight_segment(info))
            groups.append(segments)
        flight_indexes.append(groups)

    offers = []
    for rec in _find_all_local(reply, "recommendation"):
        price_info = _find_local(rec, "recPriceInfo")
        amounts = [
            m.find("amount") if m.find("amount") is not None else None
            for m in (_find_all_local(price_info, "monetaryDetail") if price_info is not None else [])
        ]
        # fall back to raw text search for amount since namespaces aren't used in the body elements
        total_amount = None
        if price_info is not None:
            monetary = _find_all_local(price_info, "monetaryDetail")
            if monetary:
                amt_el = _find_local(monetary[0], "amount")
                total_amount = amt_el.text if amt_el is not None else None

        # Each segmentFlightRef bundles the S (segment-group) refs for ALL
        # itinerary legs in order, plus trailing refs like B (booking class)
        # or C (currency); a recommendation can carry several segmentFlightRef
        # blocks (e.g. a currency-only one). We pick the block whose S refs
        # best match the number of requested legs, then map S ref i (1-based)
        # -> flight_indexes[i][S_i - 1]. Confirmed against live
        # test-environment responses for both one-way and round-trip search.
        best_s_refs: list[str] = []
        for sfr in _find_all_local(rec, "segmentFlightRef"):
            s_refs = [
                _text(rd, "refNumber")
                for rd in _find_all_local(sfr, "referencingDetail")
                if _text(rd, "refQualifier") == "S"
            ]
            if len(s_refs) > len(best_s_refs):
                best_s_refs = s_refs
            if len(s_refs) == len(flight_indexes):
                break

        # Build the flat segment list while remembering which requested leg
        # (1-based) each physical segment came from -- a leg can itself be
        # multiple physical segments (a connection), confirmed live on a
        # CMB-DOH-LHR Qatar Airways itinerary (2 physical segments, 1 leg).
        segments = []
        seg_leg_numbers = []
        for leg_idx, group_ref in enumerate(best_s_refs):
            if leg_idx >= len(flight_indexes) or group_ref is None:
                continue
            group_idx = int(group_ref) - 1
            groups = flight_indexes[leg_idx]
            if 0 <= group_idx < len(groups):
                for seg in groups[group_idx]:
                    segments.append(seg)
                    seg_leg_numbers.append(leg_idx + 1)

        # Booking class (RBD) per requested leg (NOT per physical segment --
        # a connecting leg's two physical segments share one fare class).
        # Lives in paxFareProduct/fareDetails[segRef]/groupOfFares/
        # productInformation/cabinProduct/rbd. A recommendation can repeat
        # fareDetails per passenger type (e.g. ADT then CHD); last write wins,
        # and in every live response seen so far the rbd is identical across
        # passenger types for the same leg.
        rbd_by_seg_ref = {}
        pax_fare_product = _find_local(rec, "paxFareProduct")
        if pax_fare_product is not None:
            for fd in _find_all_local(pax_fare_product, "fareDetails"):
                seg_ref_el = _find_local(fd, "segmentRef")
                seg_ref = _text(seg_ref_el, "segRef") if seg_ref_el is not None else None
                group_of_fares = _find_local(fd, "groupOfFares")
                product_info = _find_local(group_of_fares, "productInformation") if group_of_fares is not None else None
                cabin_product = _find_local(product_info, "cabinProduct") if product_info is not None else None
                rbd = _text(cabin_product, "rbd") if cabin_product is not None else None
                if seg_ref and rbd:
                    rbd_by_seg_ref[seg_ref] = rbd

        for seg, leg_number in zip(segments, seg_leg_numbers):
            seg["bookingClass"] = rbd_by_seg_ref.get(str(leg_number))

        offers.append({
            "totalPrice": total_amount,
            "currency": currency,
            "segments": segments,
        })

    return {"offers": offers, "currency": currency}

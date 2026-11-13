"""
flight_search_service.py
=========================
Wraps Amadeus Web Services Fare_MasterPricerTravelBoardSearch (SOAP/XML),
confirmed against the real example in Amadeus's Examples Repository
("Fare_MasterPricerTravelBoardSearch with Multi-City option").

Builds a one-way or round-trip search request and parses the reply into a
flat list of priced flight offers.
"""

import logging
from datetime import date
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from services.amadeus_soap_client import call, AmadeusSoapError

logger = logging.getLogger(__name__)

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
    """Passengers exactly as Amadeus's sample search sends them (13/11/2026
    audit): ADT, then CH for children (we sent "CHD", which Amadeus priced as
    an adult fare), then each infant as INF on an adult's ref with
    infantIndicator 1 (infants used to be left out, so their fare was missing
    from the price). The seat count (PX) is adults + children - an infant
    sits on an adult's lap."""
    if infants > adults:
        raise AmadeusSoapError("Each infant must travel with an adult.", status_code=422)
    blocks = []
    ref = 1
    adult_refs = []
    for ptc, count in (("ADT", adults), ("CH", children)):
        if count <= 0:
            continue
        travellers = ""
        for _ in range(count):
            travellers += f"<traveller><ref>{ref}</ref></traveller>"
            if ptc == "ADT":
                adult_refs.append(ref)
            ref += 1
        blocks.append(f"<paxReference><ptc>{ptc}</ptc>{travellers}</paxReference>")
    if infants > 0:
        travellers = "".join(f"<traveller><ref>{adult_refs[i]}</ref><infantIndicator>1</infantIndicator></traveller>"
                             for i in range(infants))
        blocks.append(f"<paxReference><ptc>INF</ptc>{travellers}</paxReference>")
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
    cabin: str | None = None,
) -> dict:
    pax_xml, total_pax = _pax_xml(adults, children, infants)
    # The cabin the customer asked for (Amadeus cabin code, e.g. C = business),
    # sent as the requested cabin - none when the customer chose none.
    cabin_xml = (f"<travelFlightInfo><cabinId><cabinQualifier>RC</cabinQualifier><cabin>{escape(cabin)}</cabin>"
                 f"</cabinId></travelFlightInfo>") if cabin else "<travelFlightInfo/>"

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
      {cabin_xml}
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
    # Each <location> carries its own optional <terminal> sub-element right
    # alongside locationId -- confirmed against a live response (e.g.
    # <location><locationId>DXB</locationId><terminal>1</terminal></location>).
    # Not every airport/flight has one, hence the None default.
    return {
        "departureDate": _text(pdt, "dateOfDeparture"),
        "departureTime": _text(pdt, "timeOfDeparture"),
        "arrivalDate": _text(pdt, "dateOfArrival"),
        "arrivalTime": _text(pdt, "timeOfArrival"),
        "from": _text(locations[0], "locationId") if len(locations) > 0 else None,
        "to": _text(locations[1], "locationId") if len(locations) > 1 else None,
        "departureTerminal": _text(locations[0], "terminal") if len(locations) > 0 else None,
        "arrivalTerminal": _text(locations[1], "terminal") if len(locations) > 1 else None,
        "marketingCarrier": _text(company, "marketingCarrier") if company is not None else None,
        "operatingCarrier": _text(company, "operatingCarrier") if company is not None else None,
        "flightNumber": _text(flight_info, "flightOrtrainNumber"),
        "equipment": _text(product_detail, "equipmentType") if product_detail is not None else None,
    }


def _parse_baggage_by_fare_ref(reply: ET.Element) -> dict[str, list[dict]]:
    """Master Pricer returns baggage allowance in ONE shared top-level
    <serviceFeesGrp> (a sibling of <recommendation>, not nested in it), not
    per-offer. Confirmed live by fully reconstructing the reference chain:
    serviceCoverageInfoGrp[item N] --refInfo(qualifier=F)--> F-ref M, and
    freeBagAllowanceGrp[itemNumberDetails=N] carries that item's actual
    allowance. F-ref M in turn equals the OWNING recommendation's own
    <itemNumber><itemNumberId><number> -- confirmed by cross-checking F=1's
    two baggage items (a weight-based "W" and a count-based "N" entry, i.e.
    exactly one checked + one carry-on allowance) against recommendation 0's
    own itemNumberId=1. Only the first few (cheapest) recommendations get
    this enrichment from the GDS -- most have no entry at all, which is
    normal, not a parsing gap. quantityCode "W"+unitQualifier "K" = checked
    (kg); quantityCode "N" (no unit) = carry-on (pieces) -- same two
    categories Travelport's own parse_baggage_allowance() distinguishes.
    A fare ref can have more than one checked-bag entry (one per leg of a
    multi-segment itinerary where legs have different allowances) -- all
    are kept, joined with " / ", rather than silently dropping one.
    """
    sfg = _find_local(reply, "serviceFeesGrp")
    if sfg is None:
        return {}

    item_to_fref: dict[str, str] = {}
    for scg in _find_all_local(sfg, "serviceCoverageInfoGrp"):
        item_info = _find_local(scg, "itemNumberInfo")
        item_num = _text(_find_local(item_info, "itemNumber"), "number") if item_info is not None else None
        # refInfo is nested one level deeper, inside serviceCovInfoGrp (note:
        # "Cov" not "Coverage") -- NOT a direct child of serviceCoverageInfoGrp.
        cov_info = _find_local(scg, "serviceCovInfoGrp")
        if cov_info is None:
            continue
        for ref_info in _find_all_local(cov_info, "refInfo"):
            for rd in _find_all_local(ref_info, "referencingDetail"):
                if _text(rd, "refQualifier") == "F":
                    f_ref = _text(rd, "refNumber")
                    if item_num and f_ref:
                        item_to_fref[item_num] = f_ref

    # 13/11/2026 audit: freeBagAllowanceGrp is the fare's free baggage;
    # quantityCode N = number of pieces, W = weight (unitQualifier K = kg).
    # Pieces used to be labelled carry-on. Any other code is shown as it came.
    checked_by_fref: dict[str, list[str]] = {}
    for bag in _find_all_local(sfg, "freeBagAllowanceGrp"):
        info = _find_local(bag, "freeBagAllownceInfo")
        details = _find_local(info, "baggageDetails") if info is not None else None
        if details is None:
            continue
        free_allowance = _text(details, "freeAllowance")
        quantity_code = _text(details, "quantityCode")
        unit_qualifier = _text(details, "unitQualifier")
        item_el = _find_local(bag, "itemNumberInfo")
        item_num = _text(_find_local(item_el, "itemNumberDetails"), "number") if item_el is not None else None
        f_ref = item_to_fref.get(item_num)
        if not f_ref or not free_allowance:
            continue
        if quantity_code == "N":
            text = f"{free_allowance} piece" + ("s" if free_allowance != "1" else "")
        elif quantity_code == "W" and unit_qualifier == "K":
            text = f"{free_allowance} kg"
        else:
            text = " ".join(v for v in (free_allowance, quantity_code, unit_qualifier) if v)
        checked_by_fref.setdefault(f_ref, []).append(text)

    result: dict[str, list[dict]] = {}
    for f_ref, texts in checked_by_fref.items():
        result[f_ref] = [{"type": "Checked Baggage", "allowance": " / ".join(texts)}]
    return result


def _parse_reply(reply: ET.Element) -> dict:
    currency = None
    conv = _find_local(reply, "conversionRate")
    if conv is not None:
        detail = _find_local(conv, "conversionRateDetail")
        if detail is not None:
            currency = _text(detail, "currency")

    baggage_by_fref = _parse_baggage_by_fare_ref(reply)

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
        fares_by_leg: dict[str, list[dict]] = {}
        pax_fare_product = _find_local(rec, "paxFareProduct")  # first = adult fare
        # The fare's validating carrier, as Amadeus gives it on the
        # recommendation (paxFareDetail/codeShareDetails, qualifier V).
        validating = None
        pfd = _find_local(pax_fare_product, "paxFareDetail") if pax_fare_product is not None else None
        for csd in _find_all_local(pfd, "codeShareDetails") if pfd is not None else []:
            if _text(csd, "transportStageQualifier") == "V":
                validating = _text(csd, "company")
                break
        if pax_fare_product is not None:
            for fd in _find_all_local(pax_fare_product, "fareDetails"):
                seg_ref_el = _find_local(fd, "segmentRef")
                seg_ref = _text(seg_ref_el, "segRef") if seg_ref_el is not None else None
                fares = []
                for gof in _find_all_local(fd, "groupOfFares"):
                    pi = _find_local(gof, "productInformation")
                    cabin = _find_local(pi, "cabinProduct") if pi is not None else None
                    fpd = _find_local(pi, "fareProductDetail") if pi is not None else None
                    ti = _find_local(gof, "ticketInfos")
                    afd = _find_local(ti, "additionalFareDetails") if ti is not None else None
                    fares.append({
                        "bookingClass": _text(cabin, "rbd") if cabin is not None else None,
                        "fareBasis": _text(fpd, "fareBasis") if fpd is not None else None,
                        "ticketDesignator": _text(afd, "ticketDesignator") if afd is not None else None,
                        # 14/11/2026: cabin code (M economy, W premium economy,
                        # C business, F first) and seats left in this class.
                        "cabin": _text(cabin, "cabin") if cabin is not None else None,
                        "seatsAvailable": _text(cabin, "avlStatus") if cabin is not None else None,
                    })
                if seg_ref and fares:
                    fares_by_leg[seg_ref] = fares

        # One groupOfFares per physical flight of the leg, in order (Amadeus's
        # sample step 1) -- each flight of a connection keeps its OWN booking
        # class and fare basis. (Until 12/11/2026 every flight of a leg got the
        # first flight's class.) The fare basis (+ ticket designator) is what
        # Amadeus asks us to send for seat prices and extras.
        position_in_leg: dict[int, int] = {}
        incomplete = False
        for seg, leg_number in zip(segments, seg_leg_numbers):
            idx = position_in_leg.get(leg_number, 0)
            position_in_leg[leg_number] = idx + 1
            fares = fares_by_leg.get(str(leg_number)) or []
            # 13/11/2026 audit: a flight with no fare line of its own does NOT
            # borrow the first flight's class / fare basis - the fare is dropped.
            fare = fares[idx] if idx < len(fares) else {}
            if not fare.get("bookingClass"):
                incomplete = True
            seg["bookingClass"] = fare.get("bookingClass")
            seg["fareBasis"] = fare.get("fareBasis")
            seg["ticketDesignator"] = fare.get("ticketDesignator")
            # 13/11/2026: which requested leg (1 = outbound, 2 = return) this
            # flight belongs to, so the website can show a round trip as two
            # legs. Booking ignores it.
            seg["leg"] = leg_number
            seg["cabin"] = fare.get("cabin")
            seg["seatsAvailable"] = fare.get("seatsAvailable")
            seg["validatingCarrier"] = validating
        if incomplete:
            logger.warning(f"Search: recommendation skipped - a flight has no booking class of its own "
                           f"({[s.get('marketingCarrier', '') + str(s.get('flightNumber', '')) for s in segments]})")
            continue

        item_number_el = _find_local(rec, "itemNumber")
        rec_item_id = _text(_find_local(item_number_el, "itemNumberId"), "number") if item_number_el is not None else None

        offers.append({
            "totalPrice": total_amount,
            "currency": currency,
            "segments": segments,
            "baggageAllowance": baggage_by_fref.get(rec_item_id, []),
        })

    return {"offers": offers, "currency": currency}

"""
flight_booking_service.py
==========================
Amadeus Web Services booking flow: sell -> PNR -> price -> TST -> ticket issue.

Each request body below is confirmed either against a real sample from
Amadeus's own Examples Repository, or directly against the WSDL pack's XSDs
(see the inline notes). This is a session-based (context-full / stateful)
flow per the SOAP Header 4.0 guide: Air_SellFromRecommendation opens the
session; PNR_AddMultiElements / Fare_PricePNRWithBookingClass /
Ticket_CreateTSTFromPricing continue it; the last call in confirm_booking()
closes it. Ticket issuance re-opens a fresh session via PNR_Retrieve
(DocIssuance_IssueTicket has no PNR-locator field of its own -- it always
acts on whatever PNR is "in context" for the current session).

No mock data: every field here is either supplied by the caller (traveler
details) or comes straight from a prior live Amadeus response (segments,
booking class, TST reference).
"""

import re
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from services.amadeus_soap_client import AmadeusSession, call, AmadeusSoapError

# Matches the e-ticket number inside an FA freetext ticketing notice, e.g.
# "PAX 603-9508508470/ETUL/LKR104414/05OCT26/CMBVS3299/07300020" -- see
# _parse_pnr_reply for why this fallback is needed.
_TICKET_NUMBER_IN_FREETEXT_RE = re.compile(r"(\d{3}-\d{10})/ET")

# Namespaces/soapActions below are the UNVERSIONED (= latest/default) operation
# for each message per this WSAP's WSDL binding, confirmed from the WSDL pack
# -- NOT necessarily the version shown in an Examples Repository sample (those
# can be generated against an older version than this WSAP's current default).
NS = {
    "sell": "http://xml.amadeus.com/ITAREQ_05_2_IA",
    "sellreply": "http://xml.amadeus.com/ITARES_05_2_IA",
    "pnr": "http://xml.amadeus.com/PNRADD_22_1_1A",
    "pnrreply": "http://xml.amadeus.com/PNRACC_22_1_1A",
    "price": "http://xml.amadeus.com/TPCBRQ_24_3_1A",
    "pricereply": "http://xml.amadeus.com/TPCBRR_24_3_1A",
    "tst": "http://xml.amadeus.com/TAUTCQ_04_1_1A",
    "tstreply": "http://xml.amadeus.com/TAUTCR_04_1_1A",
    "retrieve": "http://xml.amadeus.com/PNRRET_21_1_1A",
    "retrievereply": "http://xml.amadeus.com/PNRACC_21_1_1A",
    "issue": "http://xml.amadeus.com/TTKTIQ_15_1_1A",
    "issuereply": "http://xml.amadeus.com/TTKTIR_15_1_1A",
    "informative": "http://xml.amadeus.com/TIPNRQ_23_1_1A",
    "informativereply": "http://xml.amadeus.com/TIPNRR_23_1_1A",
}


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find_all_local(parent, name):
    return [c for c in parent if _local(c.tag) == name] if parent is not None else []


def _find_local(parent, name):
    if parent is None:
        return None
    for c in parent:
        if _local(c.tag) == name:
            return c
    return None


def _text(parent, name, default=None):
    el = _find_local(parent, name)
    return el.text if el is not None and el.text else default


def _to_iso(date_ddmmyy: str | None, time_hhmm: str | None) -> str:
    """Amadeus dates are DDMMYY, times HHMM -- same conversion used for
    search results (amadeus_aggregator.py) so the frontend never needs to
    know which GDS a date string came from."""
    if not date_ddmmyy or len(date_ddmmyy) != 6:
        return ""
    dd, mm, yy = date_ddmmyy[0:2], date_ddmmyy[2:4], date_ddmmyy[4:6]
    t = (time_hhmm or "0000").zfill(4)
    return f"20{yy}-{mm}-{dd}T{t[0:2]}:{t[2:4]}"


def _find_deep(parent, *path):
    cur = parent
    for name in path:
        cur = _find_local(cur, name)
        if cur is None:
            return None
    return cur


# ── Step 1: Air_SellFromRecommendation ──────────────────────────────────

def _sell_body(segments: list[dict], total_pax: int) -> str:
    segs_xml = ""
    for seg in segments:
        segs_xml += f"""<segmentInformation>
        <travelProductInformation>
          <flightDate>
            <departureDate>{escape(seg['departureDate'])}</departureDate>
            <departureTime>{escape(seg['departureTime'])}</departureTime>
            <arrivalDate>{escape(seg['arrivalDate'])}</arrivalDate>
            <arrivalTime>{escape(seg['arrivalTime'])}</arrivalTime>
          </flightDate>
          <boardPointDetails><trueLocationId>{escape(seg['from'])}</trueLocationId></boardPointDetails>
          <offpointDetails><trueLocationId>{escape(seg['to'])}</trueLocationId></offpointDetails>
          <companyDetails><marketingCompany>{escape(seg['marketingCarrier'])}</marketingCompany></companyDetails>
          <flightIdentification>
            <flightNumber>{escape(seg['flightNumber'])}</flightNumber>
            <bookingClass>{escape(seg['bookingClass'])}</bookingClass>
          </flightIdentification>
        </travelProductInformation>
        <relatedproductInformation>
          <quantity>{total_pax}</quantity>
          <statusCode>NN</statusCode>
        </relatedproductInformation>
      </segmentInformation>"""

    return f"""<Air_SellFromRecommendation xmlns="{NS['sell']}">
      <itineraryDetails>
        <originDestinationDetails>
          <origin>{escape(segments[0]['from'])}</origin>
          <destination>{escape(segments[-1]['to'])}</destination>
        </originDestinationDetails>
        {segs_xml}
      </itineraryDetails>
    </Air_SellFromRecommendation>"""


async def sell_segments(session: AmadeusSession, segments: list[dict], total_pax: int) -> dict:
    body = _sell_body(segments, total_pax)
    body_root = await call(
        operation="Air_SellFromRecommendation",
        soap_action="http://webservices.amadeus.com/ITAREQ_05_2_IA",
        body_xml=body,
        log_prefix="10_air_sell",
        session=session,
        stateful=True,
    )
    reply = _find_local(body_root, "Air_SellFromRecommendationReply")

    # A failed sell does NOT raise a SOAP fault -- Amadeus returns 200 OK with
    # an errorAtMessageLevel block and/or a per-segment actionDetails/statusCode
    # other than "OK" (confirmed live: "UNS" = Unable to Sell, e.g. inventory
    # sold out between search and sell). The old check here only looked for
    # errorAtApplicationLevel, which never appears in this reply shape, so a
    # failed sell was silently treated as success and only surfaced later as a
    # confusing "NEED ITINERARY" error (8111) at End Transact. Check both the
    # message-level error and every segment's own statusCode instead.
    itinerary = _find_local(reply, "itineraryDetails") if reply is not None else None
    failed_segments = []
    if itinerary is not None:
        for seg_info in _find_all_local(itinerary, "segmentInformation"):
            action = _find_local(seg_info, "actionDetails")
            status = _text(action, "statusCode") if action is not None else None
            if status and status != "OK":
                flight_details = _find_local(seg_info, "flightDetails")
                company = _find_local(flight_details, "companyDetails") if flight_details is not None else None
                flight_id = _find_local(flight_details, "flightIdentification") if flight_details is not None else None
                carrier = _text(company, "marketingCompany", "?") if company is not None else "?"
                number = _text(flight_id, "flightNumber", "?") if flight_id is not None else "?"
                failed_segments.append(f"{carrier}{number} ({status})")

    if failed_segments:
        error = _find_deep(reply, "errorAtMessageLevel", "errorSegment", "errorDetails") if reply is not None else None
        err_code = _text(error, "errorCode") if error is not None else None
        detail = f" (errorCode {err_code})" if err_code else ""
        raise AmadeusSoapError(
            f"Amadeus could not sell {', '.join(failed_segments)}{detail} -- this flight/class is no longer available, please search again",
            status_code=409,
        )

    error = _find_deep(reply, "errorAtApplicationLevel") if reply is not None else None
    if error is not None:
        err_code = _find_deep(error, "applicationErrorDetail", "errorCode")
        raise AmadeusSoapError(f"Amadeus could not sell this flight (errorCode {err_code.text if err_code is not None else '?'})", status_code=409)
    return {"ok": True}


# ── Step 1.5: Fare_InformativePricingWithoutPNR ─────────────────────────
# Requested by Amadeus's certification team: a priced, bindable quote on the
# just-sold segments BEFORE committing to a PNR (traveler names / End
# Transact) -- distinct from Fare_PricePNRWithBookingClass, which only runs
# once a PNR exists. Runs in the same stateful session right after the sell.
#
# Element structure (passengersGroup/segmentGroup/pricingOptionGroup, no
# messageActionDetails -- that was a guess from a different/older version's
# docs) confirmed directly against this WSAP's own WSDL pack
# (Fare_InformativePricingWithoutPNR_23_1_1A.xsd, office 1ASIWGSTMLR) --
# version 23_1_1A is this office's default/unversioned binding for the
# operation (confirmed in the master WSDL's <wsdl:operation
# name="Fare_InformativePricingWithoutPNR"> with no version suffix). Two
# earlier blind guesses (v12_4, v13_1) both failed with a generic WSAP
# routing fault; reading the real XSD instead of guessing resolved it.
#
# segmentInformation reuses TravelProductInformationTypeI -- the exact same
# type _sell_body() already builds (flightDate/boardPointDetails/
# offpointDetails/companyDetails/flightIdentification), confirmed by name in
# the XSD, so that XML is shared via _segment_product_xml() rather than
# duplicated. segmentRepetitionControl's own sub-fields are explicitly
# annotated "NOT USED AT TVL LEVEL!" in the XSD, so it's sent empty -- the
# passenger type goes on the sibling discountPtc/valueQualifier instead, one
# passengersGroup per individual traveler (maxOccurs=198, matching a
# per-passenger not per-type cardinality).

def _segment_product_xml(seg: dict) -> str:
    """Shared TravelProductInformationTypeI body -- used by both
    Air_SellFromRecommendation's segmentInformation and
    Fare_InformativePricingWithoutPNR's segmentInformation (confirmed same
    type name in both XSDs)."""
    return f"""<flightDate>
            <departureDate>{escape(seg['departureDate'])}</departureDate>
            <departureTime>{escape(seg['departureTime'])}</departureTime>
            <arrivalDate>{escape(seg['arrivalDate'])}</arrivalDate>
            <arrivalTime>{escape(seg['arrivalTime'])}</arrivalTime>
          </flightDate>
          <boardPointDetails><trueLocationId>{escape(seg['from'])}</trueLocationId></boardPointDetails>
          <offpointDetails><trueLocationId>{escape(seg['to'])}</trueLocationId></offpointDetails>
          <companyDetails><marketingCompany>{escape(seg['marketingCarrier'])}</marketingCompany></companyDetails>
          <flightIdentification>
            <flightNumber>{escape(seg['flightNumber'])}</flightNumber>
            <bookingClass>{escape(seg['bookingClass'])}</bookingClass>
          </flightIdentification>"""


def _informative_pricing_body(segments: list[dict], travelers: list[dict]) -> str:
    pax_xml = ""
    for t in travelers:
        ptc = _PTC_MAP.get((t.get("type") or "adult").lower(), "ADT")
        pax_xml += f"""<passengersGroup>
        <segmentRepetitionControl/>
        <discountPtc><valueQualifier>{escape(ptc)}</valueQualifier></discountPtc>
      </passengersGroup>"""

    seg_xml = ""
    for seg in segments:
        seg_xml += f"""<segmentGroup>
        <segmentInformation>
          {_segment_product_xml(seg)}
        </segmentInformation>
      </segmentGroup>"""

    return f"""<Fare_InformativePricingWithoutPNR xmlns="{NS['informative']}">
      {pax_xml}
      {seg_xml}
      <pricingOptionGroup>
        <pricingOptionKey><pricingOptionKey>RP</pricingOptionKey></pricingOptionKey>
      </pricingOptionGroup>
    </Fare_InformativePricingWithoutPNR>"""


async def informative_pricing(session: AmadeusSession, segments: list[dict], travelers: list[dict], end_session: bool = True) -> dict:
    body = _informative_pricing_body(segments, travelers)
    body_root = await call(
        operation="Fare_InformativePricingWithoutPNR",
        soap_action="http://webservices.amadeus.com/TIPNRQ_23_1_1A",
        body_xml=body,
        log_prefix="10b_fare_informative_pricing",
        session=session,
        stateful=True,
        end_session=end_session,
    )
    reply = _find_local(body_root, "Fare_InformativePricingWithoutPNRReply")
    return {"raw": reply is not None}


# ── Step 2: PNR_AddMultiElements -- travellers + contact ───────────────

_PTC_MAP = {"adult": "ADT", "child": "CHD", "infant": "INF"}


def _traveller_blocks(travelers: list[dict]) -> str:
    """Build one NM travellerInfo block per traveler, adult/child/infant alike.

    A nested infantInformation block (infant declared inside the carrying
    adult's own passengerData, not as its own travellerInfo) was tried first
    -- Amadeus rejected it outright as a schema sequence error ("Unknown item
    found or found at the wrong position", see logs/amadeus
    20261005_112939_368618.11_pnr_add_travellers_RS.xml). A plain, independent
    NM block per traveler (same shape already proven for ADT/CHD) is accepted
    instead; the infant still doesn't occupy a sold seat -- that's handled by
    excluding infants from confirm_booking's segment-sell quantity, not by
    anything structural here.
    """
    blocks = ""
    for t in travelers:
        ptc = _PTC_MAP.get((t.get("type") or "adult").lower(), "ADT")
        # dateOfBirth was tried as a child of <passenger> (same position as
        # <type>) to fix errorCode 11228 "ENTRY NOT ALLOWED IN NHP
        # CONDITIONS" on infant pricing -- rejected outright, same class of
        # schema sequence error as the infantInformation attempt above
        # ("Unknown item found or found at the wrong position" for Composite
        # "passenger"). The correct element/position for DOB on this WSAP is
        # still unconfirmed; left out rather than guessing a third time.
        blocks += f"""<travellerInfo>
        <elementManagementPassenger>
          <reference><qualifier>PR</qualifier><number>0</number></reference>
          <segmentName>NM</segmentName>
        </elementManagementPassenger>
        <passengerData>
          <travellerInformation>
            <traveller><surname>{escape(t['lastName'].upper())}</surname><quantity>1</quantity></traveller>
            <passenger><firstName>{escape(t['firstName'].upper())}</firstName><type>{ptc}</type></passenger>
          </travellerInformation>
        </passengerData>
      </travellerInfo>"""
    return blocks


# NOTE: a Form of Payment (FP) element was tested here (both cash "CA" and
# credit card "CC") back when ticket issuance was failing with errorCode
# 2011 "ENTRY NOT AUTHORISED" (an Office-level authority gate, now granted
# by Amadeus -- see confirm_booking's End Transact reordering). On easyJet/
# LCC content it came back "INVALID FORM OF PAYMENT" (errorCode 2213) --
# LCC "Light Ticketing" content needs the dedicated FOP_CreateFormOfPayment
# message instead -- so FP was reverted pending that separate flow.
#
# Now that 2011 is gone, ticket issuance on a traditional GDS carrier (e.g.
# UL) instead failed with errorCode 315 "NEED FORM OF PAYMENT" -- i.e. an FP
# element genuinely is required, just not via this inline PNR element for
# LCC content. Re-added below (cash "CA", matching the Travelport side's
# FormOfPaymentCash -- PayCorp charges the customer, the GDS itself is only
# ever told "cash/invoice", see workbench_service.py's FormOfPaymentCash).
# If a future LCC (Amadeus-content) booking hits 2213 again, that booking
# needs FOP_CreateFormOfPayment instead of this element -- do not "fix" that
# by reverting this for everyone.


def _contact_elements(email: str, phone: str) -> str:
    elements = ""
    if phone:
        elements += f"""<dataElementsIndiv>
        <elementManagementData><reference><qualifier>OT</qualifier><number>1</number></reference><segmentName>AP</segmentName></elementManagementData>
        <freetextData>
          <freetextDetail><subjectQualifier>3</subjectQualifier><type>4</type></freetextDetail>
          <longFreetext>{escape(phone)}</longFreetext>
        </freetextData>
      </dataElementsIndiv>"""
    if email:
        elements += f"""<dataElementsIndiv>
        <elementManagementData><reference><qualifier>OT</qualifier><number>2</number></reference><segmentName>AP</segmentName></elementManagementData>
        <freetextData>
          <freetextDetail><subjectQualifier>3</subjectQualifier><type>P02</type></freetextDetail>
          <longFreetext>{escape(email)}</longFreetext>
        </freetextData>
      </dataElementsIndiv>"""
    return elements


_FOP_CASH_ELEMENT = """<dataElementsIndiv>
        <elementManagementData><reference><qualifier>OT</qualifier><number>1</number></reference><segmentName>FP</segmentName></elementManagementData>
        <formOfPayment><fop><identification>CA</identification></fop></formOfPayment>
      </dataElementsIndiv>"""

# A blanket 0%-commission (FM) element -- classic Amadeus terminal entry
# "FM0.00". Some carriers inhibit ticketing entirely without ANY commission
# element present on the PNR (confirmed live: errorCode 374 "CMC RJT - NEED
# COMMISSION" on Air China, LOT Polish and Jetstar, all three fixed by adding
# this). Confirmed safe on an already-working carrier too (SriLankan) before
# making it unconditional -- 0% doesn't change what the customer pays, it
# just satisfies carriers that require the element to exist at all.
_COMMISSION_ELEMENT = """<dataElementsIndiv>
        <elementManagementData><reference><qualifier>OT</qualifier><number>1</number></reference><segmentName>FM</segmentName></elementManagementData>
        <commission><indicator>P</indicator><commissionInfo><percentage>0</percentage></commissionInfo></commission>
      </dataElementsIndiv>"""


async def add_travellers_and_contact(session: AmadeusSession, travelers: list[dict], email: str, phone: str):
    body = f"""<PNR_AddMultiElements xmlns="{NS['pnr']}">
      <reservationInfo><reservation/></reservationInfo>
      <pnrActions><optionCode>0</optionCode></pnrActions>
      {_traveller_blocks(travelers)}
      <dataElementsMaster>
        <marker1/>
        {_contact_elements(email, phone)}
        {_FOP_CASH_ELEMENT}
        {_COMMISSION_ELEMENT}
        <dataElementsIndiv>
          <elementManagementData><segmentName>TK</segmentName></elementManagementData>
          <ticketElement><ticket><indicator>OK</indicator></ticket></ticketElement>
        </dataElementsIndiv>
      </dataElementsMaster>
    </PNR_AddMultiElements>"""

    body_root = await call(
        operation="PNR_AddMultiElements",
        soap_action="http://webservices.amadeus.com/PNRADD_22_1_1A",
        body_xml=body,
        log_prefix="11_pnr_add_travellers",
        session=session,
        stateful=True,
    )

    # A rejected name element doesn't raise a SOAP fault -- Amadeus returns
    # 200 OK with that travellerInfo's elementManagementPassenger/status=ERR
    # and a nameError block, and the PNR otherwise looks fine until a much
    # later, confusing failure at End Transact ("NEED NAME" / errorCode
    # 8111) -- confirmed live with a digit in a surname (errorCode 1892
    # "INVALID FORMAT/NOT ENTERED"). Surface it here instead.
    reply = _find_local(body_root, "PNR_Reply")
    for ti in _find_all_local(reply, "travellerInfo"):
        emp = _find_local(ti, "elementManagementPassenger")
        if emp is not None and _text(emp, "status") == "ERR":
            name_error = _find_local(ti, "nameError")
            err_code = _find_deep(name_error, "errorOrWarningCodeDetails", "errorDetails", "errorCode")
            err_text = _find_deep(name_error, "errorWarningDescription", "freeTextDetails")
            desc = _text(name_error, "freeText") if name_error is not None else None
            # freeText lives as a sibling of freeTextDetails under errorWarningDescription
            ewd = _find_local(name_error, "errorWarningDescription") if name_error is not None else None
            desc = _text(ewd, "freeText") if ewd is not None else desc
            raise AmadeusSoapError(
                f"Amadeus rejected a traveler name (errorCode {err_code.text if err_code is not None else '?'}"
                f"{': ' + desc if desc else ''})",
                status_code=400,
            )

    # The request's own travellerInfo/elementManagementPassenger/reference is
    # always a placeholder ("PR"/0, see _traveller_blocks) -- Amadeus assigns
    # the real PT reference numbers only once it processes the add, returned
    # here in the SAME order the NM blocks were sent (confirmed live: a
    # single traveler came back reference qualifier "PT" number "2", matching
    # the PA reference Fare_PricePNRWithBookingClass's paxSegReference uses
    # for that same passenger). A later call (e.g. the SSR DOCS element) that
    # needs to target one specific passenger needs this real number -- it
    # can't be known before this reply exists.
    pax_refs = []
    for ti in _find_all_local(reply, "travellerInfo"):
        emp = _find_local(ti, "elementManagementPassenger")
        ref = _find_local(emp, "reference") if emp is not None else None
        number = _text(ref, "number") if ref is not None else None
        if number:
            pax_refs.append(number)
    return pax_refs


# IATA/Amadeus standard SSR DOCS free text: P/<issuing country>/<document
# number>/<nationality>/<DOB DDMMMYY>/<gender M|F>/<expiry DDMMMYY>/
# <surname>/<given name> -- the same APIS data format travel agents send via
# the classic terminal entry "SR DOCS <carrier> HK1-P/...", confirmed against
# IATA PADIS/Amadeus SSR convention (not guessed from this project's own
# prior live traffic -- no booking before this needed passport data). Tested
# live against the real Amadeus sandbox below before being wired into
# confirm_booking.
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _ddmmmyy(iso_date: str) -> str:
    """'1990-01-15' -> '15JAN90'."""
    y, m, d = iso_date.split("-")
    return f"{d}{_MONTHS[int(m) - 1]}{y[2:]}"


def _iso_from_ddmmmyy(s: str) -> str | None:
    """'15JAN90' -> '1990-01-15' (reverse of _ddmmmyy, for reading an SSR
    DOCS freetext back out of a retrieved PNR). Assumes a 2-digit year
    before 70 is 20xx, matching this project's own near-future test dates."""
    if not s or len(s) != 7:
        return None
    d, mon, yy = s[0:2], s[2:5].upper(), s[5:7]
    if mon not in _MONTHS:
        return None
    century = "20" if int(yy) < 70 else "19"
    return f"{century}{yy}-{_MONTHS.index(mon) + 1:02d}-{d}"


def _docs_ssr_xml(traveler: dict, pax_ref: str, marketing_carrier: str) -> str:
    gender = "M" if (traveler.get("gender") or "Male").upper().startswith("M") else "F"
    freetext = (
        f"P/{traveler['passportIssueCountry']}/{traveler['passportNumber']}/"
        f"{traveler['nationality']}/{_ddmmmyy(traveler['dateOfBirth'])}/{gender}/"
        f"{_ddmmmyy(traveler['passportExpiry'])}/{traveler['lastName'].upper()}/{traveler['firstName'].upper()}"
    )
    return f"""<dataElementsIndiv>
        <elementManagementData>
          <reference><qualifier>PT</qualifier><number>{escape(pax_ref)}</number></reference>
          <segmentName>SSR</segmentName>
        </elementManagementData>
        <serviceRequest>
          <ssr>
            <type>DOCS</type>
            <status>HK</status>
            <quantity>1</quantity>
            <companyId>{escape(marketing_carrier)}</companyId>
            <freetext>{escape(freetext)}</freetext>
          </ssr>
        </serviceRequest>
      </dataElementsIndiv>"""


def _foid_ssr_xml(traveler: dict, pax_ref: str, marketing_carrier: str) -> str:
    """SSR FOID (Form of Identification) -- a different element from SSR DOCS,
    required by some carriers alongside or instead of it (confirmed live:
    errorCode 10609 "MANDATORY SSRFOID MISSING FOR CARRIER" on Fits Air,
    fixed by this; also confirmed harmless on an already-working carrier,
    SriLankan, before making it unconditional). Free text format "PP<number>"
    (PP = passport) is the standard IATA FOID type prefix -- reuses the same
    passport number already collected for SSR DOCS, no new data needed."""
    freetext = f"PP{traveler['passportNumber']}"
    return f"""<dataElementsIndiv>
        <elementManagementData>
          <reference><qualifier>PT</qualifier><number>{escape(pax_ref)}</number></reference>
          <segmentName>SSR</segmentName>
        </elementManagementData>
        <serviceRequest>
          <ssr>
            <type>FOID</type>
            <status>HK</status>
            <quantity>1</quantity>
            <companyId>{escape(marketing_carrier)}</companyId>
            <freetext>{escape(freetext)}</freetext>
          </ssr>
        </serviceRequest>
      </dataElementsIndiv>"""


async def add_document_elements(session: AmadeusSession, travelers: list[dict], pax_refs: list[str], marketing_carrier: str):
    """One PNR_AddMultiElements call, one SSR DOCS + one SSR FOID per
    traveler who actually supplied passport data -- travelers without it are
    silently skipped, same as every other optional field in this flow, so
    routes that don't need document data (the majority so far) are
    completely unaffected."""
    eligible = [
        (t, ref) for t, ref in zip(travelers, pax_refs)
        if t.get("passportNumber") and t.get("passportExpiry") and t.get("passportIssueCountry") and t.get("nationality") and t.get("dateOfBirth")
    ]
    blocks = "".join(_docs_ssr_xml(t, ref, marketing_carrier) for t, ref in eligible)
    blocks += "".join(_foid_ssr_xml(t, ref, marketing_carrier) for t, ref in eligible)
    if not blocks:
        return

    body = f"""<PNR_AddMultiElements xmlns="{NS['pnr']}">
      <reservationInfo><reservation/></reservationInfo>
      <pnrActions><optionCode>0</optionCode></pnrActions>
      <dataElementsMaster>
        <marker1/>
        {blocks}
      </dataElementsMaster>
    </PNR_AddMultiElements>"""

    await call(
        operation="PNR_AddMultiElements",
        soap_action="http://webservices.amadeus.com/PNRADD_22_1_1A",
        body_xml=body,
        log_prefix="11b_pnr_add_documents",
        session=session,
        stateful=True,
    )


def _extract_locator(body_root) -> str | None:
    reply = _find_local(body_root, "PNR_Reply")
    header = _find_deep(reply, "pnrHeader", "reservationInfo", "reservation") if reply is not None else None
    locator = _text(header, "controlNumber") if header is not None else None
    if locator:
        return locator
    # Fallback: scan every reservation block (some replies repeat it under
    # securityInformation/sbr* sections) for a controlNumber.
    if reply is not None:
        for el in reply.iter():
            if _local(el.tag) == "controlNumber" and el.text:
                return el.text
    return None


async def end_transact(session: AmadeusSession, received_from: str, end_session: bool = False) -> str:
    """Finalizes (End Transact, optionCode 11) and returns the PNR locator."""
    body = f"""<PNR_AddMultiElements xmlns="{NS['pnr']}">
      <reservationInfo><reservation/></reservationInfo>
      <pnrActions><optionCode>11</optionCode></pnrActions>
      <dataElementsMaster>
        <marker1/>
        <dataElementsIndiv>
          <elementManagementData><reference><qualifier>OT</qualifier><number>3</number></reference><segmentName>RF</segmentName></elementManagementData>
          <freetextData>
            <freetextDetail><subjectQualifier>3</subjectQualifier><type>P23</type></freetextDetail>
            <longFreetext>{escape(received_from)}</longFreetext>
          </freetextData>
        </dataElementsIndiv>
      </dataElementsMaster>
    </PNR_AddMultiElements>"""

    body_root = await call(
        operation="PNR_AddMultiElements",
        soap_action="http://webservices.amadeus.com/PNRADD_22_1_1A",
        body_xml=body,
        log_prefix="12_pnr_end_transact",
        session=session,
        stateful=True,
        end_session=end_session,
    )
    locator = _extract_locator(body_root)
    if not locator:
        raise AmadeusSoapError("PNR was not confirmed -- no record locator in Amadeus response", status_code=502)
    return locator


# ── Step 3: Fare_PricePNRWithBookingClass ───────────────────────────────

async def price_pnr(session: AmadeusSession) -> dict:
    # "NHP" as a pricingOptionKey override was tried first for errorCode
    # 11228 "ENTRY NOT ALLOWED IN NHP CONDITIONS" (infant on the PNR) --
    # Amadeus rejected it outright as errorCode 572 "INVALID OPTION: NHP",
    # confirming NHP isn't a valid override key here at all. The real fix is
    # a date of birth on the infant/child traveler (see _traveller_blocks) --
    # RP auto-pricing can't validate an age-based discount fare without one.
    body = f"""<Fare_PricePNRWithBookingClass xmlns="{NS['price']}">
      <pricingOptionGroup>
        <pricingOptionKey><pricingOptionKey>RP</pricingOptionKey></pricingOptionKey>
      </pricingOptionGroup>
    </Fare_PricePNRWithBookingClass>"""

    body_root = await call(
        operation="Fare_PricePNRWithBookingClass",
        soap_action="http://webservices.amadeus.com/TPCBRQ_24_3_1A",
        body_xml=body,
        log_prefix="13_fare_price_pnr",
        session=session,
        stateful=True,
    )
    reply = _find_local(body_root, "Fare_PricePNRWithBookingClassReply")
    # A PNR with more than one passenger TYPE (e.g. ADT+CHD) gets back one
    # fareList PER type, each with its own fareReference/uniqueReference --
    # NOT one combined fareList. Confirmed live: a 1 ADT + 1 CHD price came
    # back as two fareList siblings (tstReference 1 = CHD, 2 = ADT), and
    # reading only the first left the ADT passenger with no TST, so ticket
    # issuance failed with errorCode 2102 "NEED TST" despite confirm_booking
    # itself reporting success (see logs/amadeus
    # 20261005_113722_907630.13_fare_price_pnr_RS.xml). Every fareList must
    # get its own TST created (see create_tst) and its amount included in
    # the grand total.
    fare_lists = _find_all_local(reply, "fareList") if reply is not None else []
    if not fare_lists:
        raise AmadeusSoapError("Amadeus pricing returned no fare", status_code=502)

    tst_refs: list[str] = []
    seen_pax_refs: set[str] = set()
    validating_carrier_text = None
    total_amount = 0.0
    currency = None
    for idx, fare_list in enumerate(fare_lists):
        # Some carriers (confirmed live for FZ/flydubai) return several
        # fareList siblings for the SAME passenger -- alternate fare families
        # (e.g. "Lite" vs "Flex"), not separate passenger types -- all sharing
        # the same paxSegReference refNumber. Creating a TST per fareList in
        # that case sends more TSTs than the PNR has passengers, and
        # Ticket_CreateTSTFromPricing rejects it with applicationErrorCode
        # 1908 "CHECK PASSENGER NUMBER" (PNR confirms fine via End Transact,
        # which doesn't need a TST, but DocIssuance_IssueTicket then fails
        # with errorCode 2102 "NEED TST" -- seen live on PNR 9OALWM, logs
        # 20261006_071330_158553.14_ticket_create_tst_RS.xml). So only the
        # first fareList per unique paxSegReference refNumber is kept; this
        # still keeps one fareList per DISTINCT passenger type (ADT+CHD etc,
        # which get different refNumbers -- see the note above this loop).
        pax_ref_num_el = _find_deep(fare_list, "paxSegReference", "refDetails", "refNumber")
        pax_ref_num = pax_ref_num_el.text if pax_ref_num_el is not None else None
        if pax_ref_num is not None:
            if pax_ref_num in seen_pax_refs:
                continue
            seen_pax_refs.add(pax_ref_num)

        fare_ref_el = _find_local(fare_list, "fareReference")
        tst_refs.append(_text(fare_ref_el, "uniqueReference", default=str(idx + 1)))

        if validating_carrier_text is None:
            vc = _find_deep(fare_list, "validatingCarrier", "carrierInformation", "carrierCode")
            validating_carrier_text = vc.text if vc is not None else None

        fare_data = _find_local(fare_list, "fareDataInformation")
        amount, amount_currency = None, None
        for sup in _find_all_local(fare_data, "fareDataSupInformation"):
            if _text(sup, "fareDataQualifier") == "712":  # total fare incl. tax
                amount = _text(sup, "fareAmount")
                amount_currency = _text(sup, "fareCurrency")
                break
        if amount is None and fare_data is not None:
            # fall back to base fare ("B") if no total ("712") qualifier present
            for sup in _find_all_local(fare_data, "fareDataSupInformation"):
                if _text(sup, "fareDataQualifier") == "B":
                    amount = _text(sup, "fareAmount")
                    amount_currency = _text(sup, "fareCurrency")
                    break
        if amount is not None:
            total_amount += float(amount)
            currency = currency or amount_currency

    return {
        "tstReferences": tst_refs,
        "tstReference": tst_refs[0],  # kept for any caller still expecting a single ref
        "validatingCarrier": validating_carrier_text,
        "totalAmount": str(total_amount) if total_amount else None,
        "currency": currency,
    }


# ── Step 4: Ticket_CreateTSTFromPricing ─────────────────────────────────

async def create_tst(session: AmadeusSession, tst_references: list[str], end_session: bool = False):
    """One call, one psaList per TST reference -- psaList is repeatable, so
    every passenger type's TST (see price_pnr) is created together rather
    than needing a separate round trip per type."""
    psa_blocks = "".join(f"""
      <psaList>
        <itemReference>
          <referenceType>TST</referenceType>
          <uniqueReference>{escape(ref)}</uniqueReference>
        </itemReference>
      </psaList>""" for ref in tst_references)

    body = f"""<Ticket_CreateTSTFromPricing xmlns="{NS['tst']}">{psa_blocks}
    </Ticket_CreateTSTFromPricing>"""

    await call(
        operation="Ticket_CreateTSTFromPricing",
        soap_action="http://webservices.amadeus.com/TAUTCQ_04_1_1A",
        body_xml=body,
        log_prefix="14_ticket_create_tst",
        session=session,
        stateful=True,
        end_session=end_session,
    )


# ── Full confirm flow: sell -> PNR -> price -> TST ──────────────────────

async def confirm_booking(segments: list[dict], travelers: list[dict], email: str, phone: str) -> dict:
    """sell -> add travellers/contact -> price -> store TST -> End Transact.

    End Transact (PNR_AddMultiElements, optionCode 11) must be the LAST call,
    not the first after adding travellers: it's the one operation that
    actually commits whatever is in the active PNR workspace to Amadeus's
    host -- pricing and TST creation only modify that in-progress workspace.
    An earlier version of this flow called End Transact right after adding
    travellers, then priced + created the TST afterward and just closed the
    session -- which saved a PNR with no TST attached, so ticket issuance
    later failed with errorCode 2102 "NEED TST" even though the TST had been
    created successfully (just never committed). See logs/amadeus
    20261005_102848_237775.12_pnr_end_transact_RS.xml (early End Transact,
    no TST yet) vs .14_ticket_create_tst_RS.xml (TST created afterward, in a
    session that then just closed) for the live reproduction of this bug.
    """
    # Infants don't occupy a physical seat, but Amadeus's own pricing
    # validation still wants segment-sell quantity to equal the PNR's total
    # NM name-element count (ADT+CHD+INF) -- excluding the infant here
    # triggered errorCode 11228 "ENTRY NOT ALLOWED IN NHP CONDITIONS" at
    # Fare_PricePNRWithBookingClass (confirmed against Amadeus's own Service
    # Hub guidance: NHP = "number of seats booked does not equal the number
    # of passengers booked", fixed by making them match -- not by an XML
    # structure change). So every traveler, infant included, counts here.
    total_pax = len(travelers) or 1

    # Fare_InformativePricingWithoutPNR in its own fresh session, standalone,
    # BEFORE selling -- its own name says "without PNR" and its purpose is a
    # non-committal quote; chaining it onto the sell session instead (i.e.
    # pricing already-sold/held inventory) returned errorCode 146 "CHECK -
    # CALL SUPERVISOR" on every attempt. Matches the realistic real-world
    # order too: quote first, only hold inventory (sell) if the customer
    # proceeds.
    informative_session = AmadeusSession()
    await informative_pricing(informative_session, segments, travelers)

    session = AmadeusSession()
    await sell_segments(session, segments, total_pax)
    pax_refs = await add_travellers_and_contact(session, travelers, email, phone)
    await add_document_elements(session, travelers, pax_refs, segments[0]["marketingCarrier"])
    pricing = await price_pnr(session)
    await create_tst(session, pricing["tstReferences"])
    locator = await end_transact(session, received_from=travelers[0]["lastName"].upper(), end_session=True)

    return {
        "locator": locator,
        "pricing": pricing,
    }


# ── Ticket issuance: PNR_Retrieve -> DocIssuance_IssueTicket ───────────

async def retrieve_pnr(session: AmadeusSession, locator: str, end_session: bool = False):
    body = f"""<PNR_Retrieve xmlns="{NS['retrieve']}">
      <retrievalFacts>
        <retrieve><type>2</type></retrieve>
        <reservationOrProfileIdentifier>
          <reservation><controlNumber>{escape(locator)}</controlNumber></reservation>
        </reservationOrProfileIdentifier>
      </retrievalFacts>
    </PNR_Retrieve>"""

    return await call(
        operation="PNR_Retrieve",
        soap_action="http://webservices.amadeus.com/PNRRET_21_1_1A",
        body_xml=body,
        log_prefix="20_pnr_retrieve",
        session=session,
        stateful=True,
        end_session=end_session,
    )


# ── Check My Ticket Status: standalone live PNR lookup ──────────────────

def _parse_pnr_reply(body_root) -> dict:
    """Compact view of a PNR_Retrieve reply for the ticket-status lookup --
    locator, travelers, flight segments, and ticket number(s) if any were
    actually issued. Confirmed against a real retrieve response (see
    logs/amadeus/*.20_pnr_retrieve_RS.xml); a PNR with ticketing still
    pending (our account's errorCode 2011 situation) has a TK element with
    no document number yet -- ticketNumbers comes back empty in that case,
    not an error, same as the rest of this project's handling of that gate.
    """
    reply = _find_local(body_root, "PNR_Reply")
    if reply is None:
        return {}

    locator = _extract_locator(body_root)

    travelers = []
    pax_ref_by_traveler: dict[str, dict] = {}
    for ti in _find_all_local(reply, "travellerInfo"):
        pd = _find_local(ti, "passengerData")
        tinfo = _find_local(pd, "travellerInformation") if pd is not None else None
        traveller = _find_local(tinfo, "traveller") if tinfo is not None else None
        passenger = _find_local(tinfo, "passenger") if tinfo is not None else None
        if traveller is None and passenger is None:
            continue
        t = {
            "lastName": _text(traveller, "surname") if traveller is not None else None,
            "firstName": _text(passenger, "firstName") if passenger is not None else None,
            "type": _text(passenger, "type") if passenger is not None else None,
        }
        travelers.append(t)
        emp = _find_local(ti, "elementManagementPassenger")
        pax_ref = _text(_find_local(emp, "reference"), "number") if emp is not None else None
        if pax_ref:
            pax_ref_by_traveler[pax_ref] = t

    # Email/phone (AP) and passport/document data (SSR DOCS) aren't on the
    # NM travellerInfo blocks at all -- they're separate dataElementsIndiv
    # entries (see confirm_booking's _contact_elements/add_document_elements)
    # -- so the receipt needs this pass to show anything beyond name/type for
    # a PNR retrieved fresh (e.g. after a PayCorp page reload, where no
    # client-side state survives). SSR DOCS free text round-trips exactly as
    # sent: "P/<country>/<number>/<nationality>/<DOBDDMMMYY>/<M|F>/
    # <expiryDDMMMYY>/<surname>/<given name>", confirmed live (PNR 9OYBAI).
    email, phone = None, None
    dem_for_contact = _find_local(reply, "dataElementsMaster")
    for dei in _find_all_local(dem_for_contact, "dataElementsIndiv"):
        emd = _find_local(dei, "elementManagementData")
        seg_name = _text(emd, "segmentName") if emd is not None else None
        if seg_name == "AP":
            freetext_el = _find_local(dei, "otherDataFreetext")
            detail = _find_local(freetext_el, "freetextDetail") if freetext_el is not None else None
            ftype = _text(detail, "type") if detail is not None else None
            value = _text(freetext_el, "longFreetext") if freetext_el is not None else None
            if ftype == "P02" and value:
                email = value.lower()
            elif ftype == "4" and value:
                phone = value
        elif seg_name == "SSR":
            ssr = _find_deep(dei, "serviceRequest", "ssr")
            if ssr is not None and _text(ssr, "type") == "DOCS":
                free = _text(ssr, "freetext") or _text(ssr, "freeText")
                ref_for = _find_deep(dei, "referenceForDataElement", "reference")
                pax_ref = _text(ref_for, "number") if ref_for is not None else None
                target = pax_ref_by_traveler.get(pax_ref) if pax_ref else None
                if free and target is not None:
                    parts = free.split("/")
                    if len(parts) >= 8 and parts[0] == "P":
                        target["passport_issue_country"] = parts[1]
                        target["passport_number"] = parts[2]
                        target["nationality"] = parts[3]
                        target["date_of_birth"] = _iso_from_ddmmmyy(parts[4])
                        target["gender"] = "Male" if parts[5] == "M" else "Female"
                        target["passport_expiry"] = _iso_from_ddmmmyy(parts[6])

    segments = []
    odd = _find_local(reply, "originDestinationDetails")
    for it in _find_all_local(odd, "itineraryInfo"):
        tp = _find_local(it, "travelProduct")
        product = _find_local(tp, "product") if tp is not None else None
        board = _find_local(tp, "boardpointDetail") if tp is not None else None
        off = _find_local(tp, "offpointDetail") if tp is not None else None
        company = _find_local(tp, "companyDetail") if tp is not None else None
        prod_details = _find_local(tp, "productDetails") if tp is not None else None
        related = _find_local(it, "relatedProduct")
        airline_locator = _find_deep(it, "itineraryReservationInfo", "reservation", "controlNumber")
        segments.append({
            "departure_time": _to_iso(_text(product, "depDate"), _text(product, "depTime")),
            "arrival_time": _to_iso(_text(product, "arrDate"), _text(product, "arrTime")),
            "departure_airport": _text(board, "cityCode"),
            "arrival_airport": _text(off, "cityCode"),
            "carrier_name": _text(company, "identification"),
            "flight_number": f"{_text(company, 'identification', '')}{_text(prod_details, 'identification', '')}",
            "bookingClass": _text(prod_details, "classOfService"),
            "status": _text(related, "status"),
            "airlineConfirmationNumber": airline_locator.text if airline_locator is not None else None,
        })

    ticket_numbers: list[str] = []
    ticketing_status = None
    dem = _find_local(reply, "dataElementsMaster")
    for dei in _find_all_local(dem, "dataElementsIndiv"):
        emd = _find_local(dei, "elementManagementData")
        seg_name = _text(emd, "segmentName") if emd is not None else None
        if seg_name == "TK":
            ticket = _find_deep(dei, "ticketElement", "ticket")
            if ticket is not None:
                ticketing_status = _text(ticket, "indicator")
        if seg_name in ("FA", "FO"):
            for el in dei.iter():
                if _local(el.tag) in ("documentNumber", "ticketNumber") and el.text:
                    ticket_numbers.append(el.text)
                # Some offices (missing a printer/document device -- see
                # confirm_booking's FP note) never get a structured
                # documentNumber element even though the ticket was issued;
                # Amadeus instead writes it into the FA freetext notice, e.g.
                # "PAX 603-9508508470/ETUL/LKR104414/05OCT26/CMBVS3299/...".
                # 603-9508508470 there IS the real e-ticket number (airline
                # numeric code + 10-digit serial) -- confirmed against a live
                # PNR_Retrieve (locator 9JI65M) where this was the only place
                # the number existed in the response at all.
                if _local(el.tag) == "longFreetext" and el.text:
                    m = _TICKET_NUMBER_IN_FREETEXT_RE.search(el.text)
                    if m:
                        ticket_numbers.append(m.group(1))
    ticket_numbers = list(dict.fromkeys(ticket_numbers))

    # Total fare + currency, straight from the TST stored on the saved PNR
    # (same tstData block end_transact's reply showed when confirming the
    # reorder fix -- qualifier "T" is the total-incl-tax amount, matching
    # price_pnr()'s own "712"/total qualifier on the pricing reply). Lets the
    # final ticket receipt show fare/payment details without needing the
    # client to still have confirm_booking's pricing result in memory.
    total_fare, fare_currency = None, None
    tst_data = _find_local(reply, "tstData")
    fare_data = _find_local(tst_data, "fareData") if tst_data is not None else None
    for mon in _find_all_local(fare_data, "monetaryInfo"):
        if _text(mon, "qualifier") == "T":
            total_fare = _text(mon, "amount")
            fare_currency = _text(mon, "currencyCode")
            break

    return {
        "locator": locator,
        "travelers": travelers,
        "segments": segments,
        "ticketingStatus": ticketing_status,
        "ticketNumbers": ticket_numbers,
        "totalFare": total_fare,
        "currency": fare_currency,
        "email": email,
        "phone": phone,
    }


async def get_booking_status(locator: str) -> dict:
    """Live PNR lookup for the "Check My Ticket Status" feature -- a fresh
    Amadeus session just for this one PNR_Retrieve, closed immediately after
    (no follow-on ticketing call, unlike issue_ticket()). Raises
    AmadeusSoapError if the locator doesn't exist / isn't retrievable."""
    session = AmadeusSession()
    body_root = await retrieve_pnr(session, locator, end_session=True)
    return _parse_pnr_reply(body_root)


async def issue_ticket(locator: str) -> dict:
    session = AmadeusSession()
    await retrieve_pnr(session, locator)

    body = f"""<DocIssuance_IssueTicket xmlns="{NS['issue']}">
      <optionGroup>
        <switches><statusDetails><indicator>ET</indicator></statusDetails></switches>
      </optionGroup>
    </DocIssuance_IssueTicket>"""

    body_root = await call(
        operation="DocIssuance_IssueTicket",
        soap_action="http://webservices.amadeus.com/TTKTIQ_15_1_1A",
        body_xml=body,
        log_prefix="21_doc_issuance_issue_ticket",
        session=session,
        stateful=True,
        end_session=False,
    )

    reply = _find_local(body_root, "DocIssuance_IssueTicketReply")
    status = _find_deep(reply, "processingStatus", "statusCode") if reply is not None else None
    ticket_numbers = []
    if reply is not None:
        for el in reply.iter():
            if _local(el.tag) in ("documentNumber", "ticketNumber") and el.text:
                ticket_numbers.append(el.text)

    # The immediate IssueTicketReply only carries a structured document
    # number when the office has a printer/document device assigned. Office
    # CMBVS3299 doesn't -- it returns statusCode "O" / "OK ETICKET" with no
    # number here, but the ticket genuinely is issued and Amadeus writes the
    # real number into the PNR's FA freetext instead (see
    # _TICKET_NUMBER_IN_FREETEXT_RE / _parse_pnr_reply). Re-retrieve the PNR
    # in the same session (closing it here) so the customer sees the real
    # ticket number immediately after payment, instead of only on a later
    # Check-My-Ticket-Status lookup.
    retrieve_root = await retrieve_pnr(session, locator, end_session=True)
    parsed = _parse_pnr_reply(retrieve_root)
    ticket_numbers = list(dict.fromkeys(ticket_numbers + (parsed.get("ticketNumbers") or [])))

    # Return the full parsed PNR alongside the ticketing outcome -- not just
    # ticketNumbers/status -- so the frontend's final e-ticket receipt (the
    # same component the Travelport side renders its confirmation through,
    # per its own "already in the system" format) can be built from this one
    # response: locator, travelers, segments and fare all come straight from
    # the live retrieve, not from client-side state that may not have
    # survived a PayCorp redirect.
    return {
        "status": status.text if status is not None else None,
        "ticketNumbers": ticket_numbers,
        "ticketingStatus": parsed.get("ticketingStatus"),
        "locator": parsed.get("locator") or locator,
        "travelers": parsed.get("travelers") or [],
        "segments": parsed.get("segments") or [],
        "totalFare": parsed.get("totalFare"),
        "currency": parsed.get("currency"),
        "email": parsed.get("email"),
        "phone": parsed.get("phone"),
    }

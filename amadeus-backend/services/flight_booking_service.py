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

from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from services.amadeus_soap_client import AmadeusSession, call, AmadeusSoapError

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


# ── Step 2: PNR_AddMultiElements -- travellers + contact ───────────────

_PTC_MAP = {"adult": "ADT", "child": "CHD", "infant": "INF"}


def _traveller_blocks(travelers: list[dict]) -> str:
    blocks = ""
    for t in travelers:
        ptc = _PTC_MAP.get((t.get("type") or "adult").lower(), "ADT")
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
# credit card "CC") as a candidate fix for ticket-issuance failing with
# errorCode 2011 "ENTRY NOT AUTHORISED". Both FP variants came back
# "INVALID FORM OF PAYMENT" (errorCode 2213) on this easyJet/LCC content --
# a separate, real issue (Amadeus's "Light Ticketing" LCC content needs the
# dedicated FOP_CreateFormOfPayment message, not an inline PNR FP element --
# see the "Light Ticketing implementation guide" on the portal) -- and even
# with FP present, ticket issuance still failed with the *identical*
# errorCode 2011. That confirms 2011 is independent of payment/FOP and is
# an Office-level ticketing authority gate. Reverted to no FP until the
# Light Ticketing flow is built properly; keep this note for that work.


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


async def add_travellers_and_contact(session: AmadeusSession, travelers: list[dict], email: str, phone: str):
    body = f"""<PNR_AddMultiElements xmlns="{NS['pnr']}">
      <reservationInfo><reservation/></reservationInfo>
      <pnrActions><optionCode>0</optionCode></pnrActions>
      {_traveller_blocks(travelers)}
      <dataElementsMaster>
        <marker1/>
        {_contact_elements(email, phone)}
        <dataElementsIndiv>
          <elementManagementData><segmentName>TK</segmentName></elementManagementData>
          <ticketElement><ticket><indicator>OK</indicator></ticket></ticketElement>
        </dataElementsIndiv>
      </dataElementsMaster>
    </PNR_AddMultiElements>"""

    await call(
        operation="PNR_AddMultiElements",
        soap_action="http://webservices.amadeus.com/PNRADD_22_1_1A",
        body_xml=body,
        log_prefix="11_pnr_add_travellers",
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


async def end_transact(session: AmadeusSession, received_from: str) -> str:
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
    )
    locator = _extract_locator(body_root)
    if not locator:
        raise AmadeusSoapError("PNR was not confirmed -- no record locator in Amadeus response", status_code=502)
    return locator


# ── Step 3: Fare_PricePNRWithBookingClass ───────────────────────────────

async def price_pnr(session: AmadeusSession) -> dict:
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
    fare_list = _find_local(reply, "fareList") if reply is not None else None
    if fare_list is None:
        raise AmadeusSoapError("Amadeus pricing returned no fare", status_code=502)

    fare_ref_el = _find_local(fare_list, "fareReference")
    tst_ref = _text(fare_ref_el, "uniqueReference", default="1")

    validating_carrier = _find_deep(fare_list, "validatingCarrier", "carrierInformation", "carrierCode")

    total_amount, currency = None, None
    fare_data = _find_local(fare_list, "fareDataInformation")
    for sup in _find_all_local(fare_data, "fareDataSupInformation"):
        if _text(sup, "fareDataQualifier") == "712":  # total fare incl. tax
            total_amount = _text(sup, "fareAmount")
            currency = _text(sup, "fareCurrency")
            break
    if total_amount is None and fare_data is not None:
        # fall back to base fare ("B") if no total ("712") qualifier present
        for sup in _find_all_local(fare_data, "fareDataSupInformation"):
            if _text(sup, "fareDataQualifier") == "B":
                total_amount = _text(sup, "fareAmount")
                currency = _text(sup, "fareCurrency")
                break

    return {
        "tstReference": tst_ref,
        "validatingCarrier": validating_carrier.text if validating_carrier is not None else None,
        "totalAmount": total_amount,
        "currency": currency,
    }


# ── Step 4: Ticket_CreateTSTFromPricing ─────────────────────────────────

async def create_tst(session: AmadeusSession, tst_reference: str, end_session: bool = False):
    body = f"""<Ticket_CreateTSTFromPricing xmlns="{NS['tst']}">
      <psaList>
        <itemReference>
          <referenceType>TST</referenceType>
          <uniqueReference>{escape(tst_reference)}</uniqueReference>
        </itemReference>
      </psaList>
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
    total_pax = len(travelers) or 1
    session = AmadeusSession()

    await sell_segments(session, segments, total_pax)
    await add_travellers_and_contact(session, travelers, email, phone)
    locator = await end_transact(session, received_from=travelers[0]["lastName"].upper())
    pricing = await price_pnr(session)
    await create_tst(session, pricing["tstReference"], end_session=True)

    return {
        "locator": locator,
        "pricing": pricing,
    }


# ── Ticket issuance: PNR_Retrieve -> DocIssuance_IssueTicket ───────────

async def retrieve_pnr(session: AmadeusSession, locator: str):
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
    )


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
        end_session=True,
    )

    reply = _find_local(body_root, "DocIssuance_IssueTicketReply")
    status = _find_deep(reply, "processingStatus", "statusCode") if reply is not None else None
    ticket_numbers = []
    if reply is not None:
        for el in reply.iter():
            if _local(el.tag) in ("documentNumber", "ticketNumber") and el.text:
                ticket_numbers.append(el.text)

    return {
        "status": status.text if status is not None else None,
        "ticketNumbers": ticket_numbers,
    }

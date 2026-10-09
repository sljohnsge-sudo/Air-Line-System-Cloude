"""
ancillary_booking_service.py
=============================
Actually PURCHASING an ancillary (extra baggage, meal, paid seat, etc.) on an
existing PNR and issuing its EMD -- as opposed to ancillary_service.py, which
only ever BROWSES ancillary options (stateless Service_StandaloneCatalogue,
never attached to a real booking).

Built from Amadeus's own worked sample ("Booking-Flow_Ancillaries", shared
2026-10 in response to our support ticket about the meal/ancillary gap). That
sample's flow, condensed to the 3 stateful phases:

  Phase A (already implemented -- flight_booking_service.confirm_booking):
    sell segments -> add travelers -> price -> TST -> End Transact -> PNR.

  Phase B (THIS MODULE, purchase_ancillary_on_pnr): re-open the PNR in a new
    session, create a TSM from pricing, call Service_BookPriceService once
    per traveler to actually attach the chosen ancillary service to the PNR,
    then End Transact (PNR_AddMultiElements optionCode 11) to commit it.
    flight_booking_service.end_transact() is reused as-is for that last step
    -- it's the exact same operation, just called a second time on the same
    PNR.

  Phase C (THIS MODULE, issue_emd_for_ancillary): re-open the PNR again,
    retrieve the list of TSMs now attached to it, pick out the ones for the
    ancillary just added (matched by RFIC description, e.g. "BAGGAGE"), and
    issue them as a real EMD via DocIssuance_IssueMiscellaneousDocuments.
    Flight ticket issuance (flight_booking_service.issue_ticket) is a
    SEPARATE, already-working call -- this module only adds the EMD step,
    called either right after it in the same session or independently.

NS versions below start as exactly what Amadeus's own sample used. Per
flight_booking_service.py's own NS dict comment, a sample's version is not
guaranteed to be this account's actual current default -- these are UNTESTED
against our live office as of writing and may need adjusting the same way
every other operation in this project was (see flight_booking_service.py's
inline notes for several examples of exactly that happening).
"""

import re
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from services.amadeus_soap_client import AmadeusSession, call, AmadeusSoapError
from services.flight_booking_service import (
    NS as FB_NS,
    retrieve_pnr,
    end_transact,
    _local,
    _find_all_local,
    _find_local,
    _find_deep,
    _text,
    _extract_locator,
)

# Some operations in Amadeus's sample declare TransactionFlowLink as an extra
# SOAP header -- present on every call in the sample flow from
# Service_StandaloneCatalogue (step 12) onward, so included on all of this
# module's calls too.
_TRANSACTION_FLOW_LINK = '<link:TransactionFlowLink xmlns:link="http://wsdl.amadeus.com/2010/06/ws/Link_v1" />'

NS = {
    "integrated_pricing": "http://xml.amadeus.com/TPISGQ_15_1_1A",
    "create_tsm": "http://xml.amadeus.com/TAUSCQ_09_1_1A",
    "retrieve_tsm_list": "http://xml.amadeus.com/TTSLRQ_09_1_1A",
    "issue_misc_docs": "http://xml.amadeus.com/TMDSIQ_15_1_1A",
}


# ── PNR_Retrieve -> traveler (PT) and segment (ST) reference numbers ───────

def extract_pax_and_segment_refs(body_root) -> dict:
    """Parse a PNR_Retrieve reply for the PT (traveler) and ST (segment)
    reference numbers Service_BookPriceService needs -- CustomerRefId and
    SegmentRefId are exactly these numbers, per Amadeus's sample (step 17:
    CustomerRefId="2" matching traveler PT ref 2, SegmentRefId="1" matching
    segment ST ref 1).

    Returns {"travelers": [{"pax_ref": "2", "surname": "RONDIN", "firstName": "CELESTI"}, ...],
             "segments": [{"seg_ref": "1", "marketingCarrier": "UK", ...}, ...]}
    """
    reply = _find_local(body_root, "PNR_Reply")
    if reply is None:
        return {"travelers": [], "segments": []}

    travelers = []
    for ti in _find_all_local(reply, "travellerInfo"):
        emp = _find_local(ti, "elementManagementPassenger")
        ref = _find_local(emp, "reference") if emp is not None else None
        pax_ref = _text(ref, "number") if ref is not None else None
        pd = _find_local(ti, "passengerData")
        tinfo = _find_local(pd, "travellerInformation") if pd is not None else None
        traveller = _find_local(tinfo, "traveller") if tinfo is not None else None
        passenger = _find_local(tinfo, "passenger") if tinfo is not None else None
        if pax_ref is None or traveller is None:
            continue
        travelers.append({
            "pax_ref": pax_ref,
            "surname": _text(traveller, "surname"),
            "firstName": _text(passenger, "firstName") if passenger is not None else None,
            "type": _text(passenger, "type") if passenger is not None else None,
        })

    segments = []
    odd = _find_local(reply, "originDestinationDetails")
    for it in _find_all_local(odd, "itineraryInfo") if odd is not None else []:
        emi = _find_local(it, "elementManagementItinerary")
        ref = _find_local(emi, "reference") if emi is not None else None
        seg_ref = _text(ref, "number") if ref is not None else None
        tp = _find_local(it, "travelProduct")
        company = _find_local(tp, "companyDetail") if tp is not None else None
        prod_details = _find_local(tp, "productDetails") if tp is not None else None
        if seg_ref is None:
            continue
        segments.append({
            "seg_ref": seg_ref,
            "marketingCarrier": _text(company, "identification") if company is not None else None,
            "flightNumber": _text(prod_details, "identification") if prod_details is not None else None,
        })

    return {"travelers": travelers, "segments": segments}


# ── Phase B, step 1: Ticket_CreateTSMFromPricing ────────────────────────────

async def create_tsm_from_pricing(session: AmadeusSession) -> None:
    """Creates a provisional TSM record from whatever pricing is currently in
    the session's context -- required before Service_BookPriceService can
    attach a priced ancillary to the PNR. Takes no PNR-specific parameters;
    like DocIssuance_IssueTicket, it acts on whatever PNR is "in context" for
    the session (set by the preceding retrieve_pnr call)."""
    body = """<Ticket_CreateTSMFromPricing xmlns="%s">
      <psaList>
        <itemReference>
          <referenceType>TSM</referenceType>
        </itemReference>
      </psaList>
    </Ticket_CreateTSMFromPricing>""" % NS["create_tsm"]

    await call(
        operation="Ticket_CreateTSMFromPricing",
        soap_action="http://webservices.amadeus.com/TAUSCQ_09_1_1A",
        body_xml=body,
        log_prefix="anc_create_tsm",
        session=session,
        stateful=True,
        extra_header_xml=_TRANSACTION_FLOW_LINK,
    )


# ── Phase B, step 2: AMA_ServiceBookPriceServiceRQ (per traveler) ──────────

def _parse_service_book_price_reply(body_root) -> dict:
    """AMA_ServiceBookPriceServiceRS uses attribute-heavy XML (not the usual
    element-per-value style), e.g.:
      <Success><Services><Service ... Code="XBAG" Chargeable="Yes" .../></Services>
      <Quotations><Quotation Amount="2250" CurrencyCode="INR" PricingRecordType="TSM" .../></Quotations></Success>
    """
    reply = _find_local(body_root, "AMA_ServiceBookPriceServiceRS")
    if reply is None:
        return {"success": False, "error": "No AMA_ServiceBookPriceServiceRS in response"}

    success = _find_local(reply, "Success")
    if success is None:
        # Error shape confirmed from Amadeus's generic fault pattern used
        # elsewhere in this project -- surface whatever text is present
        # rather than silently returning an empty success.
        error_text = ET.tostring(reply, encoding="unicode")[:500]
        return {"success": False, "error": error_text}

    services_el = _find_local(success, "Services")
    quotations_el = _find_local(success, "Quotations")
    service = _find_local(services_el, "Service") if services_el is not None else None
    quotation = _find_local(quotations_el, "Quotation") if quotations_el is not None else None

    return {
        "success": True,
        "service_id": service.get("ID") if service is not None else None,
        "code": service.get("Code") if service is not None else None,
        "chargeable": service.get("Chargeable") if service is not None else None,
        "quotation_id": quotation.get("ID") if quotation is not None else None,
        "amount": quotation.get("Amount") if quotation is not None else None,
        "currency": quotation.get("CurrencyCode") if quotation is not None else None,
        "pricing_record_type": quotation.get("PricingRecordType") if quotation is not None else None,
    }


async def book_price_service(
    session: AmadeusSession,
    ssr_code: str,
    rfic: str,
    rfisc: str,
    airline_code: str,
    customer_ref_id: str,
    segment_ref_id: str,
) -> dict:
    """Attaches one priced ancillary service to one traveler on the PNR
    currently in the session's context. ssr_code/rfic/rfisc come straight
    from ancillary_service.get_ancillary_offers()'s own output for the item
    the customer picked (its "ssr_code", "rfic", "rfisc" fields) -- the exact
    same identifiers Amadeus's Service_StandaloneCatalogue response already
    gave us, now fed into the real booking call instead of just being shown
    to the customer.
    """
    body = f"""<AMA_ServiceBookPriceServiceRQ Version="0" xmlns="http://xml.amadeus.com/2010/06/ServiceBookAndPrice_v1">
      <Product>
        <Service TID="1" customerRefIDs="{escape(customer_ref_id)}">
          <identifier Code="{escape(ssr_code)}" RFIC="{escape(rfic)}" RFISC="{escape(rfisc)}" bookingMethod="1" />
          <serviceProvider code="{escape(airline_code)}" />
        </Service>
      </Product>
    </AMA_ServiceBookPriceServiceRQ>"""

    body_root = await call(
        operation="Service_BookPriceService",
        soap_action="http://webservices.amadeus.com/Service_BookPriceService_1.1",
        body_xml=body,
        log_prefix="anc_book_price_service",
        session=session,
        stateful=True,
        extra_header_xml=_TRANSACTION_FLOW_LINK,
    )
    return _parse_service_book_price_reply(body_root)


# ── Phase C, step 1: Ticket_RetrieveListOfTSM ───────────────────────────────

async def retrieve_list_of_tsm(session: AmadeusSession) -> list[dict]:
    """Lists every TSM/TMT currently attached to the PNR in context, each
    tagged with its RFIC description (e.g. "BAGGAGE", "AIR TRANSPORTATION")
    so the caller can pick out just the ones for the ancillary just
    purchased -- a PNR can have several TSMs (seats, baggage, the base fare
    TST/TSM, etc.) and DocIssuance_IssueMiscellaneousDocuments must only be
    given the ones that are genuinely new miscellaneous-document items.
    """
    body = f"""<Ticket_RetrieveListOfTSM xmlns="{NS['retrieve_tsm_list']}">
    </Ticket_RetrieveListOfTSM>"""

    body_root = await call(
        operation="Ticket_RetrieveListOfTSM",
        soap_action="http://webservices.amadeus.com/TTSLRQ_09_1_1A",
        body_xml=body,
        log_prefix="anc_retrieve_tsm_list",
        session=session,
        stateful=True,
        extra_header_xml=_TRANSACTION_FLOW_LINK,
    )
    reply = _find_local(body_root, "Ticket_RetrieveListOfTSMReply")
    if reply is None:
        return []

    items = []
    for detail in _find_all_local(reply, "detailsOfRetrievedTSMs"):
        tattoo = _find_local(detail, "tattooAndTypeOfTSM")
        tsm_ref = _text(tattoo, "uniqueReference") if tattoo is not None else None
        pax_tattoo = _find_local(detail, "passengerTattoo")
        pax_ref_el = _find_local(pax_tattoo, "passengerReference") if pax_tattoo is not None else None
        pax_ref = _text(pax_ref_el, "value") if pax_ref_el is not None else None

        rfic_description = None
        for rfic in _find_all_local(detail, "rfics"):
            crit = _find_local(rfic, "criteriaDetails")
            desc = _text(crit, "attributeDescription") if crit is not None else None
            if desc:
                rfic_description = desc
                break

        total = _find_local(detail, "totalAmount")
        mon = _find_local(total, "monetaryDetails") if total is not None else None

        if tsm_ref:
            items.append({
                "tsm_ref": tsm_ref,
                "pax_ref": pax_ref,
                "rfic_description": rfic_description,
                "amount": _text(mon, "amount") if mon is not None else None,
                "currency": _text(mon, "currency") if mon is not None else None,
            })
    return items


# ── Phase C, step 2: DocIssuance_IssueMiscellaneousDocuments ───────────────

async def issue_miscellaneous_documents(session: AmadeusSession, tsm_refs: list[str]) -> dict:
    """Issues a real EMD for the given TSM/TMT reference numbers (from
    retrieve_list_of_tsm's "tsm_ref" field). This is the actual financial/
    ticketing action -- irreversible once Amadeus returns success, same as
    flight ticket issuance."""
    selections = "".join(
        f"<referenceDetails><type>TMT</type><value>{escape(ref)}</value></referenceDetails>"
        for ref in tsm_refs
    )
    body = f"""<DocIssuance_IssueMiscellaneousDocuments xmlns="{NS['issue_misc_docs']}">
      <selection>
        {selections}
      </selection>
    </DocIssuance_IssueMiscellaneousDocuments>"""

    body_root = await call(
        operation="DocIssuance_IssueMiscellaneousDocuments",
        soap_action="http://webservices.amadeus.com/TMDSIQ_15_1_1A",
        body_xml=body,
        log_prefix="anc_issue_misc_docs",
        session=session,
        stateful=True,
        extra_header_xml=_TRANSACTION_FLOW_LINK,
    )
    reply = _find_local(body_root, "DocIssuance_IssueMiscellaneousDocumentsReply")
    if reply is None:
        return {"success": False, "error": "No DocIssuance_IssueMiscellaneousDocumentsReply in response"}

    status = _find_local(reply, "processingStatus")
    status_code = _text(status, "statusCode") if status is not None else None
    error_group = _find_local(reply, "errorGroup")
    error_code = None
    free_text = None
    if error_group is not None:
        err_details = _find_deep(error_group, "errorOrWarningCodeDetails", "errorDetails")
        error_code = _text(err_details, "errorCode") if err_details is not None else None
        desc = _find_local(error_group, "errorWarningDescription")
        free_text_el = _find_local(desc, "freeTextDetails") if desc is not None else None
        # freeText is a sibling of freeTextDetails, not inside it (matches
        # Amadeus's sample: <errorWarningDescription><freeTextDetails>...
        # </freeTextDetails><freeText>OK EMD</freeText></errorWarningDescription>)
        free_text = _text(desc, "freeText") if desc is not None else None

    return {
        "success": status_code == "O" or error_code == "OK",
        "status_code": status_code,
        "error_code": error_code,
        "message": free_text,
    }


# ── Orchestration: Phase B -- purchase one ancillary on an existing PNR ────

async def purchase_ancillary_on_pnr(
    locator: str,
    ancillary: dict,
    traveler_surnames: list[str],
    received_from: str,
) -> dict:
    """Re-opens the given PNR and attaches one ancillary (from
    ancillary_service.get_ancillary_offers()'s output -- needs ssr_code,
    rfic, rfisc) to every traveler whose surname is in traveler_surnames
    (pass all travelers' surnames to buy it for everyone on the booking, or
    just one to buy it for a single passenger).

    Does NOT issue the EMD -- that's issue_emd_for_ancillary(), a separate
    step matching Amadeus's own sample (End Transact here, EMD issuance in
    its own later session, same pattern as flight ticket issuance already
    being separate from confirm_booking()).

    Returns {"success": bool, "booked_services": [...], "errors": [...]}.
    """
    session = AmadeusSession()
    retrieve_root = await retrieve_pnr(session, locator)
    refs = extract_pax_and_segment_refs(retrieve_root)

    pax_refs = [
        t["pax_ref"] for t in refs["travelers"]
        if t["surname"] and t["surname"].upper() in [s.upper() for s in traveler_surnames]
    ]
    seg_ref = refs["segments"][0]["seg_ref"] if refs["segments"] else "1"
    airline_code = (
        refs["segments"][0]["marketingCarrier"] if refs["segments"] else None
    ) or ancillary.get("airline_code")

    if not pax_refs:
        await end_transact(session, received_from=received_from, end_session=True)
        return {"success": False, "booked_services": [], "errors": ["No matching traveler found on PNR"]}

    await create_tsm_from_pricing(session)

    booked_services = []
    errors = []
    for pax_ref in pax_refs:
        result = await book_price_service(
            session,
            ssr_code=ancillary.get("ssr_code"),
            rfic=ancillary.get("rfic"),
            rfisc=ancillary.get("rfisc"),
            airline_code=airline_code,
            customer_ref_id=pax_ref,
            segment_ref_id=seg_ref,
        )
        if result.get("success"):
            booked_services.append(result)
        else:
            errors.append(result.get("error"))

    await end_transact(session, received_from=received_from, end_session=True)

    return {"success": len(errors) == 0, "booked_services": booked_services, "errors": errors}


# ── Orchestration: Phase C -- issue the EMD for already-purchased ancillaries ─

async def issue_emd_for_ancillary(locator: str, rfic_description_filter: str) -> dict:
    """Re-opens the PNR, finds every TSM whose RFIC description matches
    rfic_description_filter (e.g. "BAGGAGE" for an XBAG/ASVC baggage
    ancillary), and issues them as a real EMD. Call this AFTER
    purchase_ancillary_on_pnr() has committed the service to the PNR.

    Does not touch flight ticket issuance -- call
    flight_booking_service.issue_ticket() separately (before or after this,
    either order is fine since they're independent documents).
    """
    session = AmadeusSession()
    await retrieve_pnr(session, locator)

    tsm_list = await retrieve_list_of_tsm(session)
    matching_refs = [
        item["tsm_ref"] for item in tsm_list
        if item.get("rfic_description") and rfic_description_filter.upper() in item["rfic_description"].upper()
    ]

    if not matching_refs:
        await retrieve_pnr(session, locator, end_session=True)
        return {"success": False, "emd_numbers": [], "error": f"No TSM found matching '{rfic_description_filter}'"}

    issue_result = await issue_miscellaneous_documents(session, matching_refs)

    final_root = await retrieve_pnr(session, locator, end_session=True)
    emd_numbers = _extract_emd_numbers(final_root)

    return {
        "success": issue_result.get("success", False),
        "emd_numbers": emd_numbers,
        "tsm_refs_issued": matching_refs,
        "message": issue_result.get("message"),
    }


# "PAX 0000000001 TTM/M1-4 OK EMD" -- confirmed from Amadeus's own sample
# (26-Reply_PNR_Retrieve after EMD.txt), same freetext-fallback pattern as
# flight_booking_service._TICKET_NUMBER_IN_FREETEXT_RE for flight tickets.
_EMD_NUMBER_IN_FREETEXT_RE = re.compile(r"PAX (\d+) TTM.*OK EMD")


def _extract_emd_numbers(body_root) -> list[str]:
    reply = _find_local(body_root, "PNR_Reply")
    if reply is None:
        return []
    numbers = []
    for el in reply.iter():
        if _local(el.tag) == "longFreetext" and el.text:
            m = _EMD_NUMBER_IN_FREETEXT_RE.search(el.text)
            if m:
                numbers.append(m.group(1))
    return list(dict.fromkeys(numbers))

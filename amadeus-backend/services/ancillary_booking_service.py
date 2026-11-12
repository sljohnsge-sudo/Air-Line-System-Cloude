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
      <deadIndicator><statusDetails><indicator>DED</indicator><action>0</action></statusDetails></deadIndicator>
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


# ── Paid seats: PNR_AddMultiElements STR + Service_IntegratedPricing ───────

async def add_seat_requests(session: AmadeusSession, seats: list[dict]) -> None:
    """One STR element per seat (sample step 14): the seat, for one traveler
    (PT ref) on one flight (ST ref)."""
    blocks = "".join(f"""<dataElementsIndiv>
        <elementManagementData><segmentName>STR</segmentName></elementManagementData>
        <seatGroup><seatRequest><special><data>{escape(s['seat'])}</data></special></seatRequest></seatGroup>
        <referenceForDataElement>
          <reference><qualifier>PT</qualifier><number>{escape(s['pax_ref'])}</number></reference>
          <reference><qualifier>ST</qualifier><number>{escape(s['seg_ref'])}</number></reference>
        </referenceForDataElement>
      </dataElementsIndiv>""" for s in seats)
    body = f"""<PNR_AddMultiElements xmlns="{FB_NS['pnr']}">
      <pnrActions><optionCode>0</optionCode></pnrActions>
      <dataElementsMaster>
        <marker1/>
        {blocks}
      </dataElementsMaster>
    </PNR_AddMultiElements>"""
    root = await call(operation="PNR_AddMultiElements", soap_action="http://webservices.amadeus.com/PNRADD_22_1_1A",
                      body_xml=body, log_prefix="anc_add_seats", session=session, stateful=True,
                      extra_header_xml=_TRANSACTION_FLOW_LINK)
    reply = _find_local(root, "PNR_Reply")
    errs = [el for el in reply.iter() if _local(el.tag) in ("elementErrorInformation", "generalErrorInfo")] if reply is not None else []
    if reply is None or errs:
        msg = [t.text.strip() for e in errs for t in e.iter() if _local(t.tag) in ("freeText", "freetext", "errorFreeText") and t.text]
        raise AmadeusSoapError(f"Amadeus refused the seat request{': ' + msg[0] if msg else ''}", status_code=409)


def _bag_text(item: dict) -> str | None:
    """Weight text an airline wants with a baggage SSR, e.g. "TTL15KG" for
    "UPTO33LB 15KG BAGGAGE" (Amadeus's sample shows "TTL 5KG 1PC" on a
    booked bag)."""
    m = re.search(r"(\d+)\s*KG", (item.get("name") or "").upper())
    if m:
        return f"TTL{int(m.group(1))}KG"
    ft = (item.get("free_text") or "").strip()
    return f"TTL{int(ft)}KG" if ft.isdigit() else None


async def add_service_ssrs(session: AmadeusSession, rows: list[dict]) -> None:
    """Extras booked as an SSR with text, then priced like seats. Used when
    Service_BookPriceService answers 29025 "TEXT INFORMATION REQUIRED"
    (SriLankan weight bags, 12/11/2026): the airline confirms the SSR (HK)
    and Service_IntegratedPricing prices it to the matching RFISC."""
    blocks = "".join(f"""<dataElementsIndiv>
        <elementManagementData><segmentName>SSR</segmentName></elementManagementData>
        <serviceRequest><ssr><type>{escape(r['ssr_code'])}</type><status>NN</status><quantity>1</quantity>
          <companyId>{escape(r['airline'])}</companyId><freetext>{escape(r['text'])}</freetext></ssr></serviceRequest>
        <referenceForDataElement>
          <reference><qualifier>PT</qualifier><number>{escape(r['pax_ref'])}</number></reference>
          <reference><qualifier>ST</qualifier><number>{escape(r['seg_ref'])}</number></reference>
        </referenceForDataElement>
      </dataElementsIndiv>""" for r in rows)
    body = f"""<PNR_AddMultiElements xmlns="{FB_NS['pnr']}">
      <pnrActions><optionCode>0</optionCode></pnrActions>
      <dataElementsMaster><marker1/>{blocks}</dataElementsMaster>
    </PNR_AddMultiElements>"""
    root = await call(operation="PNR_AddMultiElements", soap_action="http://webservices.amadeus.com/PNRADD_22_1_1A",
                      body_xml=body, log_prefix="anc_add_service_ssrs", session=session, stateful=True,
                      extra_header_xml=_TRANSACTION_FLOW_LINK)
    reply = _find_local(root, "PNR_Reply")
    errs = [el for el in reply.iter() if _local(el.tag) in ("elementErrorInformation", "generalErrorInfo")] if reply is not None else []
    if reply is None or errs:
        msg = [t.text.strip() for e in errs for t in e.iter() if _local(t.tag) in ("freeText", "freetext", "errorFreeText") and t.text]
        raise AmadeusSoapError(f"Amadeus refused the extra{': ' + msg[0] if msg else ''}", status_code=409)


async def integrated_pricing(session: AmadeusSession) -> list[dict]:
    """Service_IntegratedPricing (sample step 15): prices every paid service
    on the PNR in context -- the seats just requested. Returns one entry per
    priced item: traveler (PT ref), amount, currency, description."""
    body = f"""<Service_IntegratedPricing xmlns="{NS['integrated_pricing']}">
      <pricingOption><pricingOptionKey><pricingOptionKey>ALL</pricingOptionKey></pricingOptionKey></pricingOption>
    </Service_IntegratedPricing>"""
    root = await call(operation="Service_IntegratedPricing", soap_action="http://webservices.amadeus.com/TPISGQ_15_1_1A",
                      body_xml=body, log_prefix="anc_integrated_pricing", session=session, stateful=True,
                      extra_header_xml=_TRANSACTION_FLOW_LINK)
    reply = _find_local(root, "Service_IntegratedPricingReply")
    items = []
    for grp in _find_all_local(reply, "allFaresInfoGroup") if reply is not None else []:
        amount = currency = None
        for md in _find_all_local(_find_local(grp, "monetaryInfo"), "monetaryDetails"):
            if _text(md, "typeQualifier") == "T":
                amount, currency = _text(md, "amount"), _text(md, "currency")
        pax = _find_deep(grp, "fareProductInfoGroup", "specificTravellerDetails", "travellerDetails", "referenceNumber")
        desc = None
        for el in grp.iter():
            if _local(el.tag) == "freeText" and el.text:
                desc = el.text.strip()
                break
        items.append({"pax_ref": pax.text if pax is not None else None, "amount": float(amount) if amount else 0.0,
                      "currency": currency, "description": desc})
    return items


# ── Orchestration: add paid extras (baggage, meals) and seats to a PNR ─────

def _match_travelers(refs: dict, who: dict | None) -> list[str]:
    """PT refs for one requested traveler ({"lastName", "firstName"}) -- or
    everyone when no traveler is named. The PNR lists names alphabetically,
    not in booking order, so names are matched, never positions."""
    travelers = refs["travelers"]
    if not who or not who.get("lastName"):
        return [t["pax_ref"] for t in travelers]
    ln = who["lastName"].strip().upper()
    fn = (who.get("firstName") or "").strip().upper()
    hits = [t["pax_ref"] for t in travelers if (t["surname"] or "").upper() == ln
            and (not fn or (t["firstName"] or "").upper().startswith(fn))]
    return hits


def _match_segment(refs: dict, flight: str | None) -> str | None:
    """ST ref for a flight like "UL225" (first flight when none named)."""
    segs = refs["segments"]
    if not segs:
        return None
    if not flight:
        return segs[0]["seg_ref"]
    f = flight.replace(" ", "").upper()
    for s in segs:
        if f"{s['marketingCarrier']}{s['flightNumber']}".upper() == f:
            return s["seg_ref"]
    return None


async def purchase_on_pnr(locator: str, extras: list[dict], seats: list[dict], received_from: str) -> dict:
    """Amadeus's sample steps 11-19 in one session:
      PNR_Retrieve -> (seats: STR + Service_IntegratedPricing +
      Ticket_CreateTSMFromPricing) -> (extras: Service_BookPriceService per
      traveler, which prices it and makes its own TSM) -> End Transact.
    extras: [{ssr_code, rfic, rfisc, airline_code, traveler: {lastName, firstName}?}]
    seats:  [{seat: "9F", flight: "UL225", traveler: {lastName, firstName}}]
    Nothing is charged here; the EMD is issued later (issue_emds), after
    payment. Returns what the airline priced, so the customer is only ever
    charged airline-confirmed amounts."""
    session = AmadeusSession()
    retrieve_root = await retrieve_pnr(session, locator)
    refs = extract_pax_and_segment_refs(retrieve_root)
    booked, problems, needs_text = [], [], []
    try:
        seat_rows = []
        for s in seats or []:
            pax = _match_travelers(refs, s.get("traveler"))
            seg = _match_segment(refs, s.get("flight"))
            if len(pax) != 1 or not seg:
                problems.append(f"Seat {s.get('seat')}: traveler or flight not found on {locator}")
                continue
            seat_rows.append({"seat": s["seat"], "pax_ref": pax[0], "seg_ref": seg, "flight": s.get("flight")})
        if seat_rows:
            await add_seat_requests(session, seat_rows)
            priced = await integrated_pricing(session)
            if any(p["amount"] > 0 for p in priced):
                await create_tsm_from_pricing(session)
            for row in seat_rows:
                price = next((p for p in priced if p["pax_ref"] == row["pax_ref"]), None)
                booked.append({"type": "seat", "seat": row["seat"], "flight": row["flight"], "pax_ref": row["pax_ref"],
                               "amount": price["amount"] if price else 0.0, "currency": price["currency"] if price else None})

        for x in extras or []:
            if not (x.get("ssr_code") and x.get("rfic") and x.get("rfisc")):
                problems.append(f"{x.get('name') or x.get('ssr_code')}: missing booking codes")
                continue
            pax_refs = _match_travelers(refs, x.get("traveler"))
            if not pax_refs:
                problems.append(f"{x.get('name') or x.get('ssr_code')}: traveler not found on {locator}")
                continue
            airline = x.get("airline_code") or (refs["segments"][0]["marketingCarrier"] if refs["segments"] else None)
            for pax_ref in pax_refs:
                r = await book_price_service(session, ssr_code=x["ssr_code"], rfic=x["rfic"], rfisc=x["rfisc"],
                                             airline_code=airline, customer_ref_id=pax_ref,
                                             segment_ref_id=_match_segment(refs, x.get("flight")) or "1")
                if r.get("success"):
                    booked.append({"type": "extra", "ssr_code": x["ssr_code"], "name": x.get("name"), "pax_ref": pax_ref,
                                   "amount": float(r["amount"]) if r.get("amount") else 0.0, "currency": r.get("currency")})
                elif "29025" in str(r.get("error")) or "TEXT INFORMATION REQUIRED" in str(r.get("error")):
                    text = _bag_text(x)
                    if text:
                        needs_text.append({"ssr_code": x["ssr_code"], "airline": airline, "text": text, "pax_ref": pax_ref,
                                           "seg_ref": _match_segment(refs, x.get("flight")) or "1", "name": x.get("name")})
                    else:
                        problems.append(f"{x.get('name') or x['ssr_code']}: the airline needs text we can't build")
                else:
                    problems.append(f"{x.get('name') or x['ssr_code']}: {_book_error(r.get('error'))}")

        if needs_text:
            seats_priced = {(b["pax_ref"], b["amount"]) for b in booked if b["type"] == "seat"}
            await add_service_ssrs(session, needs_text)
            priced = await integrated_pricing(session)
            if any(p["amount"] > 0 for p in priced):
                await create_tsm_from_pricing(session)
            used = set()
            for row in needs_text:
                cands = [i for i, p in enumerate(priced) if i not in used and p["pax_ref"] == row["pax_ref"] and p["amount"] > 0]
                exact = [i for i in cands if (priced[i]["description"] or "").upper() == (row["name"] or "").upper()]
                pick = (exact or [i for i in cands if (priced[i]["pax_ref"], priced[i]["amount"]) not in seats_priced] or [None])[0]
                if pick is not None:
                    used.add(pick)
                    booked.append({"type": "extra", "ssr_code": row["ssr_code"], "name": row["name"], "pax_ref": row["pax_ref"],
                                   "amount": priced[pick]["amount"], "currency": priced[pick]["currency"], "text": row["text"]})
                else:
                    problems.append(f"{row['name'] or row['ssr_code']}: requested ({row['text']}) but the airline gave no price")
    except AmadeusSoapError as e:
        problems.append(e.message)

    if booked:
        await end_transact(session, received_from=received_from, end_session=True)
    else:
        await retrieve_pnr(session, locator, end_session=True)   # nothing to save: just close the session
    total = round(sum(b["amount"] for b in booked), 2)
    currency = next((b["currency"] for b in booked if b.get("currency")), None)
    return {"success": bool(booked) and not problems, "booked": booked, "problems": problems,
            "total": total, "currency": currency}


def _book_error(err) -> str:
    """Short reason from a Service_BookPriceService failure (ShortText attrs)."""
    texts = re.findall(r'ShortText="([^"]+)"', str(err or ""))
    return "; ".join(texts) if texts else str(err)[:200]


# ── Orchestration: issue the EMDs for every unissued service on a PNR ──────

async def issue_emds(locator: str) -> dict:
    """Sample steps 21-26: PNR_Retrieve -> Ticket_RetrieveListOfTSM (unissued
    services only) -> DocIssuance_IssueMiscellaneousDocuments -> PNR_Retrieve
    for the EMD numbers. Run AFTER the flight ticket is issued and payment
    is confirmed -- this is the step that charges for the extras."""
    session = AmadeusSession()
    await retrieve_pnr(session, locator)
    tsm_list = await retrieve_list_of_tsm(session)
    refs = [t["tsm_ref"] for t in tsm_list]
    if not refs:
        await retrieve_pnr(session, locator, end_session=True)
        return {"success": False, "emd_numbers": [], "message": "No unissued extras or seats on this booking."}
    result = await issue_miscellaneous_documents(session, refs)
    final_root = await retrieve_pnr(session, locator, end_session=True)
    return {"success": result.get("success", False), "emd_numbers": _extract_emd_numbers(final_root),
            "tsm_refs_issued": refs, "items": tsm_list, "message": result.get("message")}


# EMD numbers are in the FA lines: "PAX 228-1842902438/DTUK/INR200/08AUG24/..."
# (DT = EMD, ET = flight ticket) -- sample step 26. The FB lines ("PAX
# 0000000001 TTM/M1-4 OK EMD") are only issuance notices, not EMD numbers
# (that was the old, wrong pattern here).
_EMD_NUMBER_IN_FREETEXT_RE = re.compile(r"(\d{3}-\d{10})/DT")


def _extract_emd_numbers(body_root) -> list[str]:
    reply = _find_local(body_root, "PNR_Reply")
    if reply is None:
        return []
    numbers = []
    for el in reply.iter():
        if _local(el.tag) == "longFreetext" and el.text:
            numbers += _EMD_NUMBER_IN_FREETEXT_RE.findall(el.text)
    return list(dict.fromkeys(numbers))

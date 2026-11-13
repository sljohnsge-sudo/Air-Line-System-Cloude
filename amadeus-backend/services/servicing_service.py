"""
servicing_service.py
=====================
Cancel, void and refund for Amadeus bookings (12/11/2026). Staff only: the
website calls these with the internal key, and each action has its own
on/off switch on the website (Admin > Go-live controls).

Flows, confirmed on the Amadeus TEST system (office CMBVS3299, WSAP
1ASIWGSTMLR, messages from the 12/11 WSDL pack):

  documents  PNR_Retrieve. Tickets and EMDs are the PNR's FA lines, e.g.
             "PAX 603-3943119033/ETUL/LKR60133/12NOV26/CMBVS3299/07300020":
             ET = e-ticket, EV = voided e-ticket, DT = EMD, DV = voided EMD;
             12NOV26 is the issue date (a void is only possible that day).
  cancel     PNR_Retrieve -> PNR_Cancel 17.1 (whole itinerary) -> End
             Transact. Refused while a ticket or EMD is still valid: void or
             refund it first, otherwise the money would stay with the airline.
  void       PNR_Retrieve -> Ticket_CancelDocument 11.1 for every valid
             ticket and EMD -> cancel the itinerary when all of them voided.
  refund     PNR_Retrieve -> Ticket_InitRefund 3.0 -> Ticket_UpdateRefund
             (only when staff enter an airline cancellation fee) ->
             Ticket_ProcessRefund -> cancel the itinerary. Without the ATC
             option: this office is not authorised for ATC refunds (25677
             "ATC REFUND NOT AUTHORIZED"), so Amadeus refunds the unused
             coupons and staff enter the airline's fee from the fare rules.
             A quote runs Init -> [Update] -> Ticket_IgnoreRefund, so nothing
             is refunded.
"""

import re
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from services.amadeus_soap_client import AmadeusSession, AmadeusSoapError, call
from services.flight_booking_service import (
    _find_all_local, _find_deep, _find_local, _local, _parse_pnr_reply, _text,
    retrieve_pnr,
)

PNR_CANCEL_NS = "http://xml.amadeus.com/PNRXCL_17_1_1A"
VOID_NS = "http://xml.amadeus.com/TRCANQ_11_1_1A"
REFUND_NS = "http://xml.amadeus.com/2010/06/TicketGTP_v3"
SIGN_OUT_NS = "http://xml.amadeus.com/VLSSOQ_04_1_1A"

# BSP market of the office (Sri Lanka) for Ticket_CancelDocument.
MARKET = "LK"
COLOMBO = timezone(timedelta(hours=5, minutes=30))

# "PAX 603-3943119033/ETUL/LKR60133/12NOV26/CMBVS3299/07300020"
# (an infant's line starts INF; a conjunction ticket adds "-34").
_FA_RE = re.compile(r"^(PAX|INF)\s+(\d{3}-\d{10})(?:-\d{2})?/([A-Z]{2})([A-Z0-9]{2})/([A-Z]{3})(\d+(?:\.\d+)?)/(\d{2}[A-Z]{3}\d{2})")
_DOC_STATUS = {
    "ET": ("ticket", "issued"), "EV": ("ticket", "voided"), "ER": ("ticket", "refunded"),
    "DT": ("emd", "issued"), "DV": ("emd", "voided"), "DR": ("emd", "refunded"),
}


def _plain(doc_number: str) -> str:
    return re.sub(r"\D", "", doc_number or "")


def _issue_date(ddmmmyy: str) -> str | None:
    try:
        return datetime.strptime(ddmmmyy, "%d%b%y").date().isoformat()
    except ValueError:
        return None


def _errors(reply, warnings: bool = False) -> list[str]:
    """Free-text messages of a PNR/ticketing reply: errors only, or with
    warnings=True only the warnings. Amadeus marks a warning with
    errorCategory WA/W (e.g. 24607 "SERVICE DELETED - TSM MAY BE DELETED
    IF ET/ER" when the flights of a booking with seats/extras are cancelled)."""
    if reply is None:
        return []
    out = []
    for el in reply.iter():
        if _local(el.tag) in ("generalErrorInfo", "elementErrorInformation", "errorGroup", "applicationError", "errorInfo"):
            cat = next((c.text for c in el.iter() if _local(c.tag) == "errorCategory" and c.text), "")
            if cat.upper().startswith("W") != warnings:
                continue
            for t in el.iter():
                if _local(t.tag) in ("freeText", "freetext", "errorFreeText", "text") and t.text and t.text.strip():
                    out.append(t.text.strip())
    return list(dict.fromkeys(out))


async def _close(session: AmadeusSession) -> None:
    """Sign out if a session is still open (after an error part-way through)."""
    if not session.is_open:
        return
    try:
        await call(operation="Security_SignOut", soap_action="http://webservices.amadeus.com/VLSSOQ_04_1_1A",
                   body_xml=f'<Security_SignOut xmlns="{SIGN_OUT_NS}"/>', log_prefix="svc_sign_out",
                   session=session, stateful=True, end_session=True)
    except AmadeusSoapError:
        pass


# ── Reading the booking ──────────────────────────────────────────────────

def documents_from_pnr(body_root) -> list[dict]:
    """Every ticket and EMD on the PNR (FA lines), with passenger name and
    whether it is still valid, voided or refunded."""
    reply = _find_local(body_root, "PNR_Reply")
    if reply is None:
        return []
    names = {}
    for ti in _find_all_local(reply, "travellerInfo"):
        ref = _find_deep(ti, "elementManagementPassenger", "reference")
        tinfo = _find_deep(ti, "passengerData", "travellerInformation")
        if ref is None or tinfo is None:
            continue
        surname = _text(_find_local(tinfo, "traveller"), "surname") or ""
        first = _text(_find_local(tinfo, "passenger"), "firstName") or ""
        names[_text(ref, "number")] = f"{first} {surname}".strip()

    docs, today = [], datetime.now(COLOMBO).date().isoformat()
    for dei in _find_all_local(_find_local(reply, "dataElementsMaster"), "dataElementsIndiv"):
        seg = _text(_find_local(dei, "elementManagementData"), "segmentName")
        if seg not in ("FA", "FHE", "FHM"):
            continue
        texts = [el.text.strip() for el in dei.iter() if _local(el.tag) == "longFreetext" and el.text]
        for text in texts:
            m = _FA_RE.match(text)
            if not m:
                continue
            kind, status = _DOC_STATUS.get(m.group(3), ("ticket" if m.group(3).startswith("E") else "emd", m.group(3)))
            pax_ref = None
            for r in dei.iter():
                if _local(r.tag) == "reference" and _text(r, "qualifier") == "PT":
                    pax_ref = _text(r, "number")
                    break
            issued_on = _issue_date(m.group(7))
            docs.append({
                "number": m.group(2),
                "kind": kind,
                "status": status,
                "airline": m.group(4),
                "amount": float(m.group(6)),
                "currency": m.group(5),
                "issued_on": issued_on,
                "infant": m.group(1) == "INF",
                "passenger": names.get(pax_ref),
                "can_void": status == "issued" and issued_on == today,
            })
    # One entry per document (a PNR can repeat an FA line in history).
    seen, out = set(), []
    for d in docs:
        if d["number"] not in seen:
            seen.add(d["number"])
            out.append(d)
    return out


def _view(body_root) -> dict:
    pnr = _parse_pnr_reply(body_root)
    docs = documents_from_pnr(body_root)
    live = [s for s in pnr.get("segments", []) if s.get("status") not in ("XX", "HX", "UN", "UC", "NO")]
    valid = [d for d in docs if d["status"] == "issued"]
    valid_tickets = [d for d in valid if d["kind"] == "ticket"]
    return {
        "locator": pnr.get("locator"),
        "travelers": [{"firstName": t.get("firstName"), "lastName": t.get("lastName"), "type": t.get("type")}
                      for t in pnr.get("travelers", [])],
        "segments": pnr.get("segments", []),
        "documents": docs,
        # The airline's own ticketing deadline / cancellation time (OPW / OPC).
        "airline_deadlines": pnr.get("airlineDeadlines") or [],
        "has_itinerary": bool(live),
        "valid_documents": len(valid),
        "valid_tickets": len(valid_tickets),
        "valid_emds": [d["number"] for d in valid if d["kind"] == "emd"],
        # Flights can be cancelled once no ticket is valid. A still-valid EMD
        # (seat/bag) needs the staff member's OK: Amadeus cannot refund EMDs
        # for this office (10955), so the airline refunds it separately.
        "can_cancel": bool(live) and not valid_tickets,
        "can_void": bool(valid) and all(d["can_void"] for d in valid),
        "can_refund": bool(valid),
        "total_fare": pnr.get("totalFare"),
        "base_fare": pnr.get("baseFare"),
        "currency": pnr.get("currency"),
    }


async def get_servicing_view(locator: str) -> dict:
    session = AmadeusSession()
    root = await retrieve_pnr(session, locator, end_session=True)
    return _view(root)


# ── Cancel the itinerary ─────────────────────────────────────────────────

async def _end_transact(session: AmadeusSession, received_from: str) -> list[str]:
    """End Transact with RF (optionCode 11). When Amadeus answers with only a
    warning the change is not saved yet: a second End Transact saves it (the
    terminal's "ET twice"). Returns the warnings seen."""
    body = f"""<PNR_AddMultiElements xmlns="http://xml.amadeus.com/PNRADD_22_1_1A">
      <pnrActions><optionCode>11</optionCode></pnrActions>
      <dataElementsMaster>
        <marker1/>
        <dataElementsIndiv>
          <elementManagementData><reference><qualifier>OT</qualifier><number>1</number></reference><segmentName>RF</segmentName></elementManagementData>
          <freetextData><freetextDetail><subjectQualifier>3</subjectQualifier><type>P23</type></freetextDetail>
            <longFreetext>{escape(received_from)}</longFreetext></freetextData>
        </dataElementsIndiv>
      </dataElementsMaster>
    </PNR_AddMultiElements>"""
    seen = []
    for attempt in range(2):
        root = await call(operation="PNR_AddMultiElements", soap_action="http://webservices.amadeus.com/PNRADD_22_1_1A",
                          body_xml=body,
                          log_prefix="svc_end_transact", session=session, stateful=True)
        reply = _find_local(root, "PNR_Reply")
        errs, warns = _errors(reply), _errors(reply, warnings=True)
        if errs:
            raise AmadeusSoapError(f"Amadeus did not save the change: {errs[0]}", status_code=409)
        seen += warns
        if not warns:
            break
    return list(dict.fromkeys(seen))


async def _cancel_itinerary(session: AmadeusSession, locator: str, received_from: str) -> dict:
    """PNR_Cancel (whole itinerary) -> End Transact, then a fresh session
    reads the saved booking back (so we report what Amadeus really kept)."""
    body = f"""<PNR_Cancel xmlns="{PNR_CANCEL_NS}">
      <pnrActions><optionCode>0</optionCode></pnrActions>
      <cancelElements><entryType>I</entryType></cancelElements>
    </PNR_Cancel>"""
    root = await call(operation="PNR_Cancel", soap_action="http://webservices.amadeus.com/PNRXCL_17_1_1A",
                      body_xml=body, log_prefix="svc_pnr_cancel", session=session, stateful=True)
    errs = _errors(_find_local(root, "PNR_Reply"))
    if errs:
        raise AmadeusSoapError(f"Amadeus refused to cancel the flights: {errs[0]}", status_code=409)
    warnings = _errors(_find_local(root, "PNR_Reply"), warnings=True)
    warnings += await _end_transact(session, received_from)
    await _close(session)
    view = await get_servicing_view(locator)
    view["warnings"] = list(dict.fromkeys(warnings))
    return view


async def cancel_booking(locator: str, received_from: str, confirm_emds: bool = False) -> dict:
    """Cancel an unticketed booking (or one whose tickets are all voided or
    refunded): the airline releases the seats. A still-valid EMD needs
    confirm_emds (staff then claim it from the airline)."""
    session = AmadeusSession()
    try:
        before = _view(await retrieve_pnr(session, locator))
        if before["valid_tickets"]:
            raise AmadeusSoapError("This booking still has valid tickets. Void or refund them first, "
                                   "so the money comes back before the flights are cancelled.", status_code=409)
        if before["valid_emds"] and not confirm_emds:
            raise AmadeusSoapError(f"EMD {', '.join(before['valid_emds'])} (seat/baggage) is still valid. Void it today, "
                                   "or confirm the cancel and claim the EMD refund from the airline.", status_code=409)
        if not before["has_itinerary"]:
            await _close(session)
            return {"success": True, "already_cancelled": True, "message": "The flights were already cancelled.", "booking": before}
        after = await _cancel_itinerary(session, locator, received_from)
        return {"success": not after.get("has_itinerary"), "message": "Flights cancelled; the airline has released the seats.",
                "emds_still_valid": before["valid_emds"], "booking": after}
    finally:
        await _close(session)


# ── Void (same day as issue) ─────────────────────────────────────────────

async def _void_document(session: AmadeusSession, number: str) -> dict:
    from config.amadeus_ws_config import AMADEUS_WS_OFFICE_ID
    body = f"""<Ticket_CancelDocument xmlns="{VOID_NS}">
      <documentNumberDetails><documentDetails><number>{_plain(number)}</number></documentDetails></documentNumberDetails>
      <stockProviderDetails><officeSettingsDetails><marketIataCode>{MARKET}</marketIataCode></officeSettingsDetails></stockProviderDetails>
      <targetOfficeDetails><originatorDetails><inHouseIdentification2>{escape(AMADEUS_WS_OFFICE_ID)}</inHouseIdentification2></originatorDetails></targetOfficeDetails>
    </Ticket_CancelDocument>"""
    root = await call(operation="Ticket_CancelDocument", soap_action="http://webservices.amadeus.com/TRCANQ_11_1_1A",
                      body_xml=body, log_prefix="svc_void", session=session, stateful=True)
    reply = _find_local(root, "Ticket_CancelDocumentReply")
    status = _text(_find_deep(reply, "transactionResults", "responseDetails"), "statusCode") if reply is not None else None
    errs = _errors(reply)
    return {"number": number, "voided": status == "O" and not errs, "error": errs[0] if errs else (None if status == "O" else f"status {status}")}


async def void_booking(locator: str, received_from: str, cancel_flights: bool = True) -> dict:
    """Void every valid ticket and EMD on the booking (only on the day they
    were issued), then cancel the flights."""
    session = AmadeusSession()
    try:
        before = _view(await retrieve_pnr(session, locator))
        valid = [d for d in before["documents"] if d["status"] == "issued"]
        if not valid:
            raise AmadeusSoapError("There are no valid tickets or EMDs to void on this booking.", status_code=409)
        late = [d["number"] for d in valid if not d["can_void"]]
        if late:
            raise AmadeusSoapError(f"Void is only possible on the day of issue. {', '.join(late)} "
                                   "was issued on an earlier day — use Refund instead.", status_code=409)
        # Tickets first, then EMDs (an EMD belongs to its ticket).
        results = []
        for d in sorted(valid, key=lambda d: d["kind"] != "ticket"):
            results.append({**await _void_document(session, d["number"]), "kind": d["kind"], "passenger": d["passenger"]})
        mid = _view(await retrieve_pnr(session, locator))
        all_voided = all(r["voided"] for r in results) and not mid["valid_documents"]
        after = mid
        if all_voided and cancel_flights and mid["has_itinerary"]:
            after = await _cancel_itinerary(session, locator, received_from)
        return {"success": all_voided, "results": results, "flights_cancelled": all_voided and not after.get("has_itinerary"),
                "booking": after,
                "message": "All tickets and EMDs voided and the flights cancelled." if all_voided
                           else "Some documents could not be voided — see each line."}
    finally:
        await _close(session)


# ── Refund ───────────────────────────────────────────────────────────────

def _money(el) -> float | None:
    if el is None or el.get("Amount") is None:
        return None
    try:
        amount = float(el.get("Amount"))
        dp = int(el.get("DecimalPlaces") or 0)
        return amount / (10 ** dp) if dp and "." not in el.get("Amount") else amount
    except ValueError:
        return None


def _parse_refund_reply(root, rs_name: str) -> dict:
    """Bundles of an Init/Update/Process refund reply: per document the
    refundable total, fare part, taxes, airline fee, or the error."""
    rs = _find_local(root, rs_name)
    if rs is None:
        return {"ok": False, "error": "No refund answer from Amadeus", "bundles": []}
    general_errors = [e.text for e in rs.iter() if _local(e.tag) == "Error" and e.text]
    bundles = []
    for b in _find_all_local(_find_local(rs, "FunctionalData"), "ContractBundle"):
        errs = [e.text for e in b.iter() if _local(e.tag) == "Error" and e.text]
        record = next((e.get("RecordID") for e in b.iter() if _local(e.tag) in ("Error", "Warning") and e.get("RecordID")), None)
        rd = _find_local(b, "RefundDetails")
        contracts = _find_all_local(_find_local(rd, "Contracts"), "Contract") if rd is not None else []
        for c in contracts or [None]:
            doc = _find_deep(c, "DocumentAndCouponInformation", "DocumentNumber") if c is not None else None
            mon = {m.get("Qualifier"): _money(m) for m in _find_all_local(_find_local(c, "MonetaryInformations"), "MonetaryInformation")} if c is not None else {}
            # 13/11/2026 audit: only the penalty amounts Amadeus gives - no 0 filled in.
            fees = [f for f in (_money(p) for p in _find_all_local(_find_local(c, "Penalties"), "Penalty")) if f is not None]                 if c is not None else []
            refundable = _find_local(c, "Refundable") if c is not None else None
            bundles.append({
                "bundle_id": b.get("ID"),
                "contract_id": c.get("ID") if c is not None else None,
                "document": (doc.get("Number") if doc is not None else record),
                "refundable": _money(refundable),
                "fare_refund": mon.get("FRF"),
                "fare_paid": mon.get("B"),
                "fare_used": mon.get("FRU") if mon.get("FRU") is not None else mon.get("RFU"),
                "taxes": mon.get("TXT"),
                "airline_fee": round(sum(fees), 2) if fees else None,
                "currency": (refundable.get("CurrencyCode") if refundable is not None else None),
                "error": errs[0] if errs else None,
            })
    return {"ok": not general_errors and all(not b["error"] for b in bundles) and bool(bundles),
            "error": general_errors[0] if general_errors else None, "bundles": bundles}


def _match(bundles: list[dict], number: str) -> dict | None:
    digits = _plain(number)
    return next((b for b in bundles if b.get("document") and _plain(b["document"]).startswith(digits)), None)


async def _refund_session(session: AmadeusSession, locator: str, numbers: list[str] | None, fees: dict[str, float]):
    """Retrieve -> Init -> (Update with fees). Returns (view, docs, quote)."""
    view = _view(await retrieve_pnr(session, locator))
    valid = [d for d in view["documents"] if d["status"] == "issued"]
    chosen = [d for d in valid if not numbers or d["number"] in numbers or _plain(d["number"]) in {_plain(n) for n in numbers}]
    if not chosen:
        raise AmadeusSoapError("There are no valid tickets or EMDs to refund on this booking.", status_code=409)
    contracts = "".join(f'<Contract Number="{_plain(d["number"])}"/>' for d in chosen)
    root = await call(operation="Ticket_InitRefund", soap_action="http://webservices.amadeus.com/Ticket_InitRefund_3.0",
                      body_xml=f'<AMA_TicketInitRefundRQ xmlns="{REFUND_NS}" Version="3.000"><Contracts>{contracts}</Contracts></AMA_TicketInitRefundRQ>',
                      log_prefix="svc_refund_init", session=session, stateful=True)
    quote = _parse_refund_reply(root, "AMA_TicketInitRefundRS")
    if not quote["bundles"]:
        raise AmadeusSoapError(f"Amadeus could not start the refund: {quote['error'] or 'no refund record'}", status_code=409)

    updates = []
    for d in chosen:
        fee = float(fees.get(d["number"]) or 0)
        b = _match(quote["bundles"], d["number"])
        if fee > 0 and b and b.get("contract_id"):
            cur = b.get("currency")
            if not cur:
                raise AmadeusSoapError("Amadeus gave no refund currency for this document, so the airline fee can't be added.",
                                       status_code=409)
            # The fee exactly as staff entered it (it used to be rounded to whole units).
            cents = round(fee * 100)
            amount, places = (str(cents // 100), 0) if cents % 100 == 0 else (str(cents), 2)
            updates.append(f"""<ContractBundle ID="{b['bundle_id']}"><Contracts><Contract ID="{b['contract_id']}">
              <Penalties><Penalty PenaltyType="CP" PercentOrAmount="A" Amount="{amount}" CurrencyCode="{cur}" DecimalPlaces="{places}"/></Penalties>
            </Contract></Contracts></ContractBundle>""")
    if updates:
        root = await call(operation="Ticket_UpdateRefund", soap_action="http://webservices.amadeus.com/Ticket_UpdateRefund_3.0",
                          body_xml=f'<AMA_TicketUpdateRefundRQ xmlns="{REFUND_NS}" Version="3.000">{"".join(updates)}</AMA_TicketUpdateRefundRQ>',
                          log_prefix="svc_refund_update", session=session, stateful=True)
        updated = _parse_refund_reply(root, "AMA_TicketUpdateRefundRS")
        if updated["bundles"]:
            quote = updated
        elif updated["error"]:
            raise AmadeusSoapError(f"Amadeus refused the airline fee: {updated['error']}", status_code=409)
    lines = []
    for d in chosen:
        b = _match(quote["bundles"], d["number"]) or {}
        lines.append({**d, "refundable": b.get("refundable"), "fare_refund": b.get("fare_refund"),
                      "taxes": b.get("taxes"), "airline_fee": b.get("airline_fee"),
                      "refund_currency": b.get("currency"), "error": b.get("error")})
    return view, lines, quote


async def refund_quote(locator: str, numbers: list[str] | None = None, fees: dict[str, float] | None = None) -> dict:
    """What Amadeus would refund, without refunding (Ignore at the end)."""
    session = AmadeusSession()
    try:
        view, lines, quote = await _refund_session(session, locator, numbers, fees or {})
        try:
            await call(operation="Ticket_IgnoreRefund", soap_action="http://webservices.amadeus.com/Ticket_IgnoreRefund_3.0",
                       body_xml=f'<AMA_TicketIgnoreRefundRQ xmlns="{REFUND_NS}" Version="3.000"/>',
                       log_prefix="svc_refund_ignore", session=session, stateful=True)
        except AmadeusSoapError:
            pass
        total = round(sum(l["refundable"] or 0 for l in lines if not l["error"]), 2)
        return {"locator": locator, "lines": lines, "total_refund": total,
                "currency": next((l["refund_currency"] for l in lines if l.get("refund_currency")), None),
                "ok": all(not l["error"] and l["refundable"] is not None for l in lines),
                "booking": view}
    finally:
        await _close(session)


async def refund_booking(locator: str, received_from: str, numbers: list[str] | None = None,
                         fees: dict[str, float] | None = None, cancel_flights: bool = True) -> dict:
    """Refund the chosen (default: all) valid tickets/EMDs, then cancel the
    flights once nothing valid is left."""
    session = AmadeusSession()
    try:
        view, lines, quote = await _refund_session(session, locator, numbers, fees or {})
        bad = [l for l in lines if l["error"] or l["refundable"] is None]
        if bad:
            raise AmadeusSoapError(f"Amadeus cannot refund {bad[0]['number']}: {bad[0]['error'] or 'no refund amount'}", status_code=409)
        root = await call(operation="Ticket_ProcessRefund", soap_action="http://webservices.amadeus.com/Ticket_ProcessRefund_3.0",
                          body_xml=f'<AMA_TicketProcessRefundRQ xmlns="{REFUND_NS}" Version="3.000"/>',
                          log_prefix="svc_refund_process", session=session, stateful=True)
        done = _parse_refund_reply(root, "AMA_TicketProcessRefundRS")
        rs = _find_local(root, "AMA_TicketProcessRefundRS")
        success = rs is not None and _find_deep(rs, "GeneralReply", "Success") is not None and not any(b["error"] for b in done["bundles"])
        if not success:
            raise AmadeusSoapError(f"Amadeus did not complete the refund: {done['error'] or next((b['error'] for b in done['bundles'] if b['error']), 'unknown reason')}",
                                   status_code=409)
        mid = _view(await retrieve_pnr(session, locator))
        after = mid
        if cancel_flights and not mid["valid_tickets"] and mid["has_itinerary"]:
            after = await _cancel_itinerary(session, locator, received_from)
        total = round(sum(l["refundable"] or 0 for l in lines), 2)
        return {"success": True, "lines": lines, "total_refund": total,
                "currency": next((l["refund_currency"] for l in lines if l.get("refund_currency")), None),
                "refund_records": [b.get("document") for b in done["bundles"]],
                "flights_cancelled": not after.get("has_itinerary"), "booking": after,
                "emds_still_valid": mid["valid_emds"],
                "message": "Refund recorded with the airline" + (" and the flights cancelled." if not after.get("has_itinerary") else ".")}
    finally:
        await _close(session)

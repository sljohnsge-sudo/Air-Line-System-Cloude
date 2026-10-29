"""
services/cancel_service.py
==========================
Cancel a Travelport flight booking — held or ticketed, GDS or NDC — the way
Travelport's Exchange, Refund, and Void Guide and NDC Exchanges and Refunds
guide lay it out:

  GDS held       post-commit workbench -> Cancel Workbench Items -> commit
  GDS ticketed   on the issue date (agency time): Batch Void every ticket and
                 EMD (Single Ticket Void only if Batch Void itself errors),
                 then cancel the itinerary as for a held booking.
                 After the void period: TripServices has no automated GDS
                 refund yet, so it is refused — staff refund in Smartpoint.
  NDC held       post-commit workbench -> Reservation Cancel
  NDC ticketed   post-commit workbench -> Refund Quote -> Reservation Cancel

quote_cancellation() only reads: its Refund Quote workbench is discarded,
never committed. cancel_booking() runs the same checks again and refuses if
the action or amount moved since the quote staff confirmed. Every amount comes
from a live Travelport response; nothing here is estimated.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

import httpx

import database
from config.api_endpoints import TravelportEndpoints
from config.travelport_config import TravelportConfig
from services.auth_service import get_auth_headers, invalidate_token
from services.workbench_service import discard_workbench
from utils import tp_logger

logger = logging.getLogger(__name__)

_NO_BODY = object()
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class CancelError(Exception):
    """A Travelport step failed. `done` lists what Travelport had already
    confirmed before the failure (e.g. tickets voided), so the caller records
    exactly that and nothing more."""

    def __init__(self, message: str, step: str, done: dict | None = None):
        super().__init__(message)
        self.step = step
        self.done = done or {}


class CancelRefused(Exception):
    """The booking can't be cancelled through TripServices (e.g. a GDS ticket
    past its void period). Nothing was sent to Travelport that changes it."""


class QuoteChanged(Exception):
    """The action or amount differs from the quote staff confirmed."""

    def __init__(self, quote: dict):
        super().__init__("The refund quote changed since it was checked.")
        self.quote = quote


# ── Travelport calls ──────────────────────────────────────────────────────────

def _call(method: str, url: str, payload=_NO_BODY) -> dict:
    """One Travelport call. Returns the JSON body even on a 4xx that carries
    Travelport's own Result.Error (callers read it with _errors); raises
    CancelError only when there is no usable body at all.
    No payload = no body and no Content-Type (Post Commit Workbench, Reservation
    Cancel); a str payload is sent as-is (the empty GDS commit body)."""
    has_body = payload is not _NO_BODY

    def send(client):
        headers = get_auth_headers(with_body=has_body)
        if not has_body:
            return client.request(method, url, headers=headers)
        if isinstance(payload, str):
            return client.request(method, url, content=payload, headers=headers)
        return client.request(method, url, json=payload, headers=headers)

    try:
        with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
            response = send(client)
            if response.status_code == 401:
                invalidate_token()
                response = send(client)
    except httpx.HTTPError as e:
        raise CancelError(f"Travelport did not answer: {e}", "network")

    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400 and not _errors(body):
        raise CancelError(f"Travelport HTTP {response.status_code}: {response.text[:300]}", "http")
    return body if isinstance(body, dict) else {}


def _results(body: dict) -> list[dict]:
    """Result objects at the top level or under the one wrapper key
    (ReservationResponse, OfferListResponse, DocumentVoidListResponse...)."""
    found = [body.get("Result")] if isinstance(body.get("Result"), dict) else []
    for value in body.values():
        if isinstance(value, dict) and isinstance(value.get("Result"), dict):
            found.append(value["Result"])
    return found


def _errors(body: dict) -> list[dict]:
    return [e for r in _results(body) for e in (r.get("Error") or [])]


def _warnings(body: dict) -> list[dict]:
    return [w for r in _results(body) for w in (r.get("Warning") or [])]


def _msg(errors: list[dict]) -> str:
    return "; ".join(f"{e.get('Message')} ({e.get('SourceCode')})" if e.get("SourceCode") else str(e.get("Message"))
                     for e in errors)


def _reservation(body: dict) -> dict:
    return (body.get("ReservationResponse") or {}).get("Reservation") or body.get("Reservation") or {}


# ── Reading the booking ───────────────────────────────────────────────────────

def _agency_tz() -> timezone:
    return timezone(timedelta(minutes=TravelportConfig.AGENCY_UTC_OFFSET_MINUTES))


def _agency_today() -> str:
    return datetime.now(_agency_tz()).date().isoformat()


def _agency_date(utc_time: str | None) -> str | None:
    """'2026-10-25T19:03:00Z' -> '2026-10-26' in agency time."""
    try:
        return datetime.fromisoformat(utc_time.replace("Z", "+00:00")).astimezone(_agency_tz()).date().isoformat()
    except (AttributeError, ValueError):
        return None


def _live_booking(locator_code: str) -> dict:
    """Reservation Retrieve, reduced to what cancelling needs: content source,
    tickets, EMDs and whether any flight segment is still on the booking."""
    body = _call("GET", TravelportEndpoints.retrieve_reservation(locator_code))
    reservation = _reservation(body)
    if not reservation:
        raise CancelError(f"Travelport could not retrieve {locator_code}: {_msg(_errors(body)) or 'no reservation returned'}",
                          "retrieve")

    content_source = None
    segments = 0
    for offer in reservation.get("Offer") or []:
        for product in offer.get("Product") or []:
            content_source = content_source or product.get("ContentSource")
            segments += len(product.get("FlightSegment") or [])

    tickets: dict[str, dict] = {}
    emds: dict[str, dict] = {}
    currency = None
    for receipt in reservation.get("Receipt") or []:
        for doc in receipt.get("Document") or []:
            number = doc.get("Number")
            if not number:
                continue
            amount = doc.get("Amount") or {}
            currency = currency or (amount.get("CurrencyCode") or {}).get("value")
            entry = {
                "number": number,
                "passenger_type": (doc.get("TravelerIdentifierRef") or {}).get("passengerTypeCode") or "ADT",
                "amount": amount.get("Total"),
                "currency": (amount.get("CurrencyCode") or {}).get("value"),
                "receipt_time": receipt.get("dateTime"),
            }
            if doc.get("@type") == "DocumentTicket":
                tickets.setdefault(number, entry)
            elif doc.get("@type") == "DocumentEMD":
                entry["description"] = (doc.get("EMDDescription") or {}).get("value")
                emds.setdefault(number, entry)
    for t in reservation.get("Ticket") or []:
        if t.get("number"):
            tickets.setdefault(t["number"], {"number": t["number"], "passenger_type": "ADT", "amount": None, "currency": None})

    return {
        "content_source": content_source,
        "segments": segments,
        "tickets": list(tickets.values()),
        "emds": list(emds.values()),
        "currency": currency,
    }


def _gds_ticket_detail(ticket_number: str) -> dict:
    """GET /air/ticket/tickets/{number}: issue date (AgencyInfo.ticketedDate,
    agency local) and coupon statuses (OpenForUse, Void, Refunded...)."""
    body = _call("GET", TravelportEndpoints.retrieve_ticket_by_number(ticket_number))
    tickets = (body.get("TicketListResponse") or {}).get("TicketID") or []
    if not tickets:
        raise CancelError(f"Travelport Ticket Retrieve failed for {ticket_number}: {_msg(_errors(body)) or 'no ticket returned'}",
                          "ticket_retrieve")
    t = tickets[0]
    statuses = sorted({s.get("Status") for s in t.get("TicketSegment") or [] if s.get("Status")})
    return {
        "ticketed_date": (t.get("AgencyInfo") or {}).get("ticketedDate"),
        "coupon_statuses": statuses,
    }


def _content_source(live: dict, cached: dict | None) -> str:
    # Reservation Retrieve returns no Offer[] for NDC bookings, so the source
    # saved from the original commit response is the fallback.
    return (live.get("content_source") or (cached or {}).get("fare_source") or "GDS").upper()


def _open_workbench(locator_code: str) -> dict:
    body = _call("POST", TravelportEndpoints.create_workbench_from_locator(locator_code))
    reservation = _reservation(body)
    if _errors(body) or not (reservation.get("Identifier") or {}).get("value"):
        raise CancelError(f"Travelport could not open {locator_code} for changes: "
                          f"{_msg(_errors(body)) or 'no workbench returned'}", "post_commit_workbench")
    return reservation


def _air_offer(workbench: dict, cached: dict | None) -> dict:
    """The air offer's id + Identifier as the post-commit workbench returns
    them (authority = carrier for NDC); falls back to the copy saved from the
    original commit when the workbench comes back without Offer[]."""
    for offer in workbench.get("Offer") or []:
        ident = offer.get("Identifier") or {}
        if offer.get("id") and ident.get("value") and not str(offer["id"]).startswith("an_"):
            return {"id": offer["id"], "value": ident["value"], "authority": ident.get("authority") or "Travelport"}
    cached = cached or {}
    if cached.get("offer_local_id") and cached.get("offer_uuid"):
        return {"id": cached["offer_local_id"], "value": cached["offer_uuid"],
                "authority": cached.get("offer_authority") or cached.get("airline_code") or "Travelport"}
    raise CancelError("Travelport returned no offer for this booking, so it can't be quoted or cancelled.", "offer_lookup")


def _refund_quote(workbench_id: str, offer: dict) -> dict:
    """NDC Refund Quote. Returns the amount Travelport says is refundable and
    the offer the Cancel must name."""
    payload = {
        "@type": "OfferQueryCancelOffer",
        "BuildFromOffer": {
            "@type": "BuildFromOffer",
            "OfferIdentifier": {
                "id": offer["id"],
                "Identifier": {"value": offer["value"], "authority": offer["authority"]},
            },
        },
    }
    body = _call("POST", TravelportEndpoints.ndc_refund_quote(workbench_id), payload)
    if _errors(body):
        raise CancelError(f"Travelport Refund Quote failed: {_msg(_errors(body))}", "refund_quote")
    offers = (body.get("OfferListResponse") or {}).get("OfferID") or []
    if not offers:
        raise CancelError("Travelport Refund Quote returned no offer.", "refund_quote")
    # Live (EK, 2026-10-29): an OfferModify whose Price is the ORIGINAL fare
    # and whose ModifyPrice is the change from it — negative TotalPrice is the
    # amount refunded (Price 104975, ModifyPrice -96552 => 96552 back, 8423
    # kept by the airline). Without ModifyPrice there is no refund figure, so
    # the quote fails rather than show the original fare as the refund.
    quoted = offers[0]
    price = quoted.get("Price") or {}
    modify = quoted.get("ModifyPrice") or {}
    if modify.get("TotalPrice") is None:
        raise CancelError("Travelport Refund Quote returned no refund amount (no ModifyPrice).", "refund_quote")
    change = float(modify["TotalPrice"])
    refund = -change if change < 0 else 0.0
    original = float(price["TotalPrice"]) if price.get("TotalPrice") is not None else None
    return {
        "offer_id": quoted.get("id") or offer["id"],
        "offer_identifier": (quoted.get("Identifier") or {}).get("value"),
        "amount": round(refund, 2),
        "original_price": original,
        "airline_keeps": round(original - refund, 2) if original is not None else None,
        "currency": (modify.get("CurrencyCode") or price.get("CurrencyCode") or {}).get("value"),
        "refund_value_ind": quoted.get("refundValueInd"),
        "modify_price": modify,
        "warnings": [w.get("Message") for w in _warnings(body)],
    }


# ── Quote ─────────────────────────────────────────────────────────────────────

def quote_cancellation(locator_code: str) -> dict:
    """What cancelling this booking would do, from live Travelport data:
    action void / refund / cancel / not_supported, the amount, and the
    tickets/EMDs involved. Changes nothing on Travelport."""
    cached = database.get_booking_by_locator(locator_code)
    live = _live_booking(locator_code)
    source = _content_source(live, cached)
    quote = {
        "locator_code": locator_code,
        "content_source": source,
        "tickets": live["tickets"],
        "emds": live["emds"],
        "segments": live["segments"],
        "currency": live["currency"] or (cached or {}).get("currency"),
        "amount": None,
    }

    if source == "NDC":
        workbench = _open_workbench(locator_code)
        workbench_id = workbench["Identifier"]["value"]
        try:
            offer = _air_offer(workbench, cached)
            if not live["tickets"] and not (cached or {}).get("ticket_number"):
                quote.update(action="cancel", can_cancel=True, offer=offer,
                             message="Held NDC booking — nothing was paid to the airline. Reservation Cancel releases it.")
                return quote
            refund = _refund_quote(workbench_id, offer)
        finally:
            discard_workbench(workbench_id)
        quote.update(action="refund", can_cancel=True, amount=refund["amount"],
                     currency=refund["currency"] or quote["currency"], refund=refund,
                     message=(f"Travelport Refund Quote: {refund['currency'] or ''} {refund['amount']:,.2f} refundable"
                              + (f" of {refund['original_price']:,.2f} paid (the airline keeps {refund['airline_keeps']:,.2f})"
                                 if refund["original_price"] is not None else "")
                              + ". Confirming cancels the ticket with the airline and refunds that amount "
                              "to the original form of payment."))
        return quote

    if not live["tickets"]:
        if not live["segments"]:
            raise CancelRefused("Travelport shows no flights left on this booking — it is already cancelled.")
        quote.update(action="cancel", can_cancel=True,
                     message="Held GDS booking (no ticket). Cancelling removes the flights from the PNR.")
        return quote

    today = _agency_today()
    for t in live["tickets"]:
        try:
            t.update(_gds_ticket_detail(t["number"]))
        except CancelError as e:
            # Some carriers' pre-production tickets answer Ticket Retrieve with
            # "TICKET IS NOT VALID"; the payment receipt's issue time on the
            # booking is then the issue date (coupon status unknown).
            receipt_date = _agency_date(t.get("receipt_time"))
            if not receipt_date:
                raise
            t.update(ticketed_date=receipt_date, coupon_statuses=None, ticket_retrieve_error=str(e))
    open_tickets = [t for t in live["tickets"] if t["coupon_statuses"] != ["Void"]]
    if not open_tickets:
        # Already voided (e.g. an earlier void whose itinerary cancel failed).
        if not live["segments"]:
            raise CancelRefused("Travelport shows every ticket void and no flights left — already cancelled.")
        quote.update(action="cancel", can_cancel=True,
                     message="Every ticket is already void on Travelport; cancelling removes the remaining flights.")
        return quote

    if all(t.get("ticketed_date") == today for t in open_tickets):
        documents = live["tickets"] + live["emds"]
        amount = (round(sum(float(d["amount"]) for d in documents), 2)
                  if all(d.get("amount") is not None for d in documents) else None)
        quote.update(action="void", can_cancel=True, amount=amount, void_date=today, void_tickets=open_tickets,
                     message=(f"Ticketed today ({today}, agency time) — inside the void period. Confirming voids "
                              f"{len(live['tickets'])} ticket(s)" + (f" and {len(live['emds'])} EMD(s)" if live["emds"] else "")
                              + " for a full refund and cancels the flights."))
        return quote

    dates = ", ".join(sorted({str(t.get("ticketed_date")) for t in open_tickets}))
    quote.update(action="not_supported", can_cancel=False,
                 message=(f"Ticket issued {dates}; the void period ended at midnight agency time on the issue date. "
                          "Travelport TripServices doesn't support automated GDS refunds yet — refund this ticket in "
                          "Smartpoint, then mark the cancellation request resolved."))
    return quote


# ── Cancel ────────────────────────────────────────────────────────────────────

def _lock_for(locator_code: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(locator_code, threading.Lock())


def _batch_void(locator_code: str, tickets: list[dict], emds: list[dict]) -> list[dict]:
    payload = {
        "locator": {"value": locator_code, "locatorType": "Confirmation Number", "source": "1G"},
        "documentVoid": [{"documentType": "Ticket", "number": [t["number"] for t in tickets]}]
        + ([{"documentType": "EMD", "number": [e["number"] for e in emds]}] if emds else []),
    }
    body = _call("POST", TravelportEndpoints.BATCH_VOID, payload)
    statuses = (body.get("DocumentVoidListResponse") or {}).get("documentStatus") or []
    if _errors(body) and not statuses:
        raise CancelError(f"Travelport Batch Void failed: {_msg(_errors(body))}", "batch_void")
    return statuses


def _single_void(ticket_number: str) -> dict:
    body = _call("PUT", TravelportEndpoints.void_ticket(ticket_number),
                 {"@type": "TicketQueryUpdateTicket", "status": "Void"})
    if _errors(body):
        return {"documentType": "Ticket", "number": ticket_number, "status": "FAILED", "error": _msg(_errors(body))}
    return {"documentType": "Ticket", "number": ticket_number, "status": "VOID"}


def _tickets_void_on_travelport(tickets: list[dict]) -> bool:
    try:
        return all(_gds_ticket_detail(t["number"])["coupon_statuses"] == ["Void"] for t in tickets)
    except CancelError:
        return False


def _void_documents(locator_code: str, tickets: list[dict], emds: list[dict]) -> list[dict]:
    """Void every ticket and EMD. Returns Travelport's per-document status;
    raises CancelError unless every document came back VOID."""
    try:
        statuses = _batch_void(locator_code, tickets, emds)
    except CancelError as batch_err:
        if emds:
            raise
        logger.warning(f"Batch Void failed for {locator_code} ({batch_err}); trying Single Ticket Void")
        statuses = [_single_void(t["number"]) for t in tickets]

    by_number = {s.get("number"): s for s in statuses}
    wanted = [t["number"] for t in tickets] + [e["number"] for e in emds]
    voided = [n for n in wanted if str((by_number.get(n) or {}).get("status", "")).upper() == "VOID"]
    if len(voided) != len(wanted):
        # A timed-out or half-answered void may still have gone through:
        # Ticket Retrieve is the record of what each coupon is now.
        if not emds and _tickets_void_on_travelport(tickets):
            logger.warning(f"Void response for {locator_code} was incomplete, but Ticket Retrieve shows every ticket Void")
            return statuses + [{"documentType": "Ticket", "number": t["number"], "status": "VOID",
                                "source": "Ticket Retrieve"} for t in tickets if t["number"] not in voided]
        failed = [n for n in wanted if n not in voided]
        raise CancelError(f"Travelport did not void {', '.join(failed)}: "
                          + "; ".join(f"{n}={by_number.get(n, {}).get('error') or by_number.get(n, {}).get('status') or 'no status'}"
                                      for n in failed),
                          "void", done={"voided": voided, "void_status": statuses})
    return statuses


def _cancel_gds_itinerary(locator_code: str) -> dict:
    """post-commit workbench -> Cancel Workbench Items -> commit, then a
    retrieve to check no flight segment is left."""
    workbench = _open_workbench(locator_code)
    workbench_id = workbench["Identifier"]["value"]
    committed = False
    try:
        body = _call("POST", TravelportEndpoints.cancel_items(workbench_id), {"cancelAllInd": True})
        if _errors(body):
            raise CancelError(f"Travelport Cancel Workbench Items failed: {_msg(_errors(body))}", "cancel_items")
        body = _call("POST", TravelportEndpoints.commit_workbench(workbench_id), "")
        if _errors(body) or not _reservation(body):
            raise CancelError(f"Travelport commit of the cancellation failed: {_msg(_errors(body)) or 'no reservation returned'}",
                              "commit")
        committed = True
    finally:
        if not committed:
            discard_workbench(workbench_id)
    remaining = _live_booking(locator_code)["segments"]
    if remaining:
        raise CancelError(f"Travelport accepted the cancel but {remaining} flight segment(s) are still on the booking.",
                          "verify")
    return {"workbench_id": workbench_id, "segments_remaining": 0}


def _reservation_cancel(workbench_id: str, offer_identifier: str) -> list[dict]:
    body = _call("POST", TravelportEndpoints.reservation_cancel(workbench_id, offer_identifier))
    receipts = (body.get("ReceiptListResponse") or {}).get("ReceiptID") or []
    if _errors(body) or not receipts:
        raise CancelError(f"Travelport Reservation Cancel failed: {_msg(_errors(body)) or 'no receipt returned'}",
                          "reservation_cancel")
    return receipts


def cancel_booking(locator_code: str, confirmed_action: str, confirmed_amount: float | None) -> dict:
    """Cancel on Travelport, then record what Travelport confirmed.
    Raises CancelRefused, QuoteChanged (nothing sent) or CancelError (a
    Travelport step failed; anything it had already confirmed is recorded)."""
    lock = _lock_for(locator_code)
    if not lock.acquire(blocking=False):
        raise CancelRefused("A cancellation for this booking is already running.")
    try:
        quote = quote_cancellation(locator_code)
        if not quote["can_cancel"]:
            raise CancelRefused(quote["message"])
        amount_moved = (quote["amount"] is not None and confirmed_amount is not None
                        and round(quote["amount"], 2) != round(float(confirmed_amount), 2))
        if quote["action"] != confirmed_action or amount_moved:
            raise QuoteChanged(quote)
        if quote["action"] == "void":
            return _do_gds_void(locator_code, quote)
        if quote["action"] == "refund":
            return _do_ndc_refund(locator_code, quote)
        if quote["content_source"] == "NDC":
            return _do_ndc_held_cancel(locator_code, quote)
        return _do_gds_held_cancel(locator_code, quote)
    finally:
        lock.release()


def _do_gds_void(locator_code: str, quote: dict) -> dict:
    statuses = _void_documents(locator_code, quote["void_tickets"], quote["emds"])
    details = {"action": "void", "void_status": statuses, "tickets": quote["tickets"], "emds": quote["emds"]}
    try:
        details["itinerary"] = _cancel_gds_itinerary(locator_code)
        details["itinerary_cancelled"] = True
    except CancelError as e:
        # The tickets are void on Travelport whatever happens next, so that is
        # recorded; the flights still on the PNR are raised to staff.
        details["itinerary_cancelled"] = False
        details["itinerary_error"] = str(e)
        record = database.record_cancellation(locator_code, "Voided", "gds_void", quote["amount"], quote["currency"], details)
        raise CancelError(f"Tickets voided, but the flights are still on the PNR: {e}", e.step,
                          done={"status": "Voided", "booking": record})
    record = database.record_cancellation(locator_code, "Voided", "gds_void", quote["amount"], quote["currency"], details)
    return {"status": "Voided", "amount": quote["amount"], "currency": quote["currency"], "details": details, "booking": record}


def _do_gds_held_cancel(locator_code: str, quote: dict) -> dict:
    details = {"action": "cancel", "itinerary": _cancel_gds_itinerary(locator_code), "itinerary_cancelled": True}
    record = database.record_cancellation(locator_code, "Cancelled", "gds_cancel", None, None, details)
    return {"status": "Cancelled", "amount": None, "currency": None, "details": details, "booking": record}


def _do_ndc_refund(locator_code: str, quote: dict) -> dict:
    cached = database.get_booking_by_locator(locator_code)
    workbench = _open_workbench(locator_code)
    workbench_id = workbench["Identifier"]["value"]
    cancelled = False
    try:
        offer = _air_offer(workbench, cached)
        refund = _refund_quote(workbench_id, offer)
        if round(refund["amount"], 2) != round(quote["amount"], 2):
            raise QuoteChanged({**quote, "amount": refund["amount"], "currency": refund["currency"], "refund": refund,
                                "message": f"Travelport Refund Quote is now {refund['currency'] or ''} {refund['amount']:,.2f} "
                                           f"(was {quote['amount']:,.2f}). Confirm again to cancel at this amount."})
        receipts = _reservation_cancel(workbench_id, refund["offer_identifier"] or offer["value"])
        cancelled = True
    finally:
        if not cancelled:
            discard_workbench(workbench_id)
    status = "Refunded" if refund["amount"] > 0 else "Cancelled"
    details = {"action": "refund", "refund": refund, "receipts": receipts, "tickets": quote["tickets"]}
    record = database.record_cancellation(locator_code, status, "ndc_refund", refund["amount"], refund["currency"], details)
    return {"status": status, "amount": refund["amount"], "currency": refund["currency"], "details": details, "booking": record}


def _do_ndc_held_cancel(locator_code: str, quote: dict) -> dict:
    cached = database.get_booking_by_locator(locator_code)
    workbench = _open_workbench(locator_code)
    workbench_id = workbench["Identifier"]["value"]
    cancelled = False
    try:
        offer = _air_offer(workbench, cached)
        # The airline's offer Identifier, not the local "offer_1" — live,
        # OfferIdentifier=offer_1 is refused "OFFER IDENTIFIER IS NOT VALID" (8212).
        receipts = _reservation_cancel(workbench_id, offer["value"])
        cancelled = True
    finally:
        if not cancelled:
            discard_workbench(workbench_id)
    details = {"action": "cancel", "receipts": receipts}
    record = database.record_cancellation(locator_code, "Cancelled", "ndc_cancel", None, None, details)
    return {"status": "Cancelled", "amount": None, "currency": None, "details": details, "booking": record}

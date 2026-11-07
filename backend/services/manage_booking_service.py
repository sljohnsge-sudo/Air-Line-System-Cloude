"""
Customer Portal → Booking Status / Manage My Booking.

lookup(): find a booking by PNR or 13-digit ticket number and read it live
from Travelport (live_booking_service) — our booking table is not the
source. Nothing is shown unless the passenger's last name matches a
traveler Travelport holds on that PNR: the audit made the public retrieve
redact personal data, and seats/SSRs/tickets on the locator alone would
undo that.

Changes after booking use Travelport's post-commit workbench
(buildfromlocator → seat map / ancillary shop → add → commit), per the
Seats Guide and the Ancillary and EMD Guide:
- special requests (wheelchair, meals): add_special_requests() — free SSRs;
- seats and extras: workbench_service.add_seats_and_extras_to_pnr() (the
  same code the booking flow uses), prices always Travelport's;
- paid items on a ticketed booking are paid for by issuing EMDs:
  issue_emds() — Cash FOP + Payment on just those offers + commit.

NDC: seat map / ancillary shop / Ancillary Price fail on Travelport for
every NDC carrier we sell (QR, SQ, AI; see the API reference memory), so
seats and extras are refused for NDC; wheelchair SSRs are accepted.
"""
import logging
import re
import time
import uuid

import database
from config.api_endpoints import TravelportEndpoints
from config.travelport_config import TravelportConfig
from services import live_booking_service
from services.cancel_service import _call, _errors, _msg, _reservation, CancelError
from services.special_services import MEAL_SSR_TYPES, WHEELCHAIR_SSR_FLAGS, NDC_MEAL_SSR_CARRIERS
from services import workbench_service

logger = logging.getLogger(__name__)

# Re-tested 2026-11-03: Ancillary Price still answers 500 INTERNAL SERVER
# ERROR for QR NDC (TraceID_0358C2C77479) — a Travelport-side fault.
NDC_ADDONS_REFUSED = ("Seats and extras are not available for your selected flight segment and cannot be added to this booking. "
                      "Please contact the airline directly or call 011 222 2222 for further assistance.")

# 1G 8604 "ANCILLARIES ARE NOT AVAILABLE FOR THE REQUESTED FLIGHT DATA":
# the airline sells no extras through Travelport (every Emirates booking).
EXTRAS_NOT_OFFERED = ("Extra baggage and other extras are not available for your selected flight and cannot be added to "
                      "this booking. Please contact the airline directly or call 011 222 2222 for further assistance.")

WHEELCHAIR_LABELS = {"WCHR": "Wheelchair — can walk short distances and climb stairs",
                     "WCHS": "Wheelchair — can't climb stairs",
                     "WCHC": "Wheelchair — needs carrying to the cabin seat"}
MEAL_LABELS = {"VGML": "Vegan", "AVML": "Vegetarian (Hindu)", "VLML": "Vegetarian (lacto-ovo)",
               "VOML": "Vegetarian (oriental)", "MOML": "Muslim", "HNML": "Hindu", "KSML": "Kosher",
               "DBML": "Diabetic", "LFML": "Low fat", "LSML": "Low salt", "GFML": "Gluten free",
               "CHML": "Child", "BBML": "Baby", "SFML": "Seafood", "FPML": "Fruit platter", "VJML": "Jain",
               "LCML": "Low calorie", "NLML": "Lactose free", "RVML": "Vegetarian (raw)"}


class NotFound(Exception):
    """Wrong reference or last name — one message for both, so a guess
    can't tell which part was right."""


class Refused(Exception):
    pass


def _norm_name(text: str) -> str:
    return re.sub(r"[^A-Z]", "", (text or "").upper())


def _pnr_for_ticket(ticket_number: str) -> str | None:
    """Ticket number → PNR from Travelport's Ticket Retrieve. That call is
    GDS only (NDC tickets answer 4239), so an NDC ticket we issued is found
    through our booking index instead — the booking itself is still read
    live from Travelport."""
    try:
        body = _call("GET", TravelportEndpoints.retrieve_ticket_by_number(ticket_number))
        t = ((body.get("TicketListResponse") or {}).get("TicketID") or [{}])[0]
        locator = (t.get("ReservationLocator") or {}).get("value")
        if locator:
            return locator.upper()
    except CancelError:
        pass
    cached = database.get_booking_by_ticket_number(ticket_number)
    return (cached or {}).get("locator_code")


def lookup(query: str, last_name: str) -> dict:
    query = re.sub(r"[\s-]", "", query or "").upper()
    if re.fullmatch(r"\d{13}", query):
        locator = _pnr_for_ticket(query)
    elif re.fullmatch(r"[A-Z0-9]{5,8}", query):
        locator = query
    else:
        raise NotFound("Enter a 6-character booking reference (PNR) or a 13-digit ticket number.")
    if not locator:
        raise NotFound()
    try:
        live = live_booking_service.get_live_booking(locator)
    except live_booking_service.LiveBookingError:
        raise NotFound()
    want = _norm_name(last_name)
    if not want or not any(_norm_name(t.get("surname")) == want for t in live.get("travelers") or []):
        raise NotFound()
    live["ticketed"] = bool(live.get("tickets"))
    live["can_add_seats_extras"] = live.get("content_source") != "NDC" and live.get("booking_status") in ("confirmed", "ticketed")
    live["can_add_special_requests"] = live.get("booking_status") in ("confirmed", "ticketed")
    if live.get("content_source") == "NDC":
        live["seats_extras_note"] = NDC_ADDONS_REFUSED
    return live


# ── Post-commit workbench helpers ─────────────────────────────────────────────

def _open_workbench(locator: str) -> tuple[str, dict]:
    body = _call("POST", TravelportEndpoints.create_workbench_from_locator(locator))
    res = _reservation(body) or body.get("ReservationWorkbench") or {}
    wb = (res.get("Identifier") or {}).get("value")
    if not wb:
        raise Refused(f"Travelport could not open the booking for changes: {_msg(_errors(body)) or 'no workbench'}")
    return wb, res


def _discard(wb: str) -> None:
    try:
        workbench_service.discard_workbench(wb)
    except Exception as e:
        logger.warning(f"Discarding workbench {wb} failed: {e}")


def travelers_of(live: dict) -> list:
    """Live travelers as the dicts the booking-flow helpers match on
    (first_name/last_name against Travelport's Given/Surname)."""
    return [{"first_name": t.get("given") or "", "last_name": t.get("surname") or "", "id": t.get("id"),
             "name": t.get("name"), "type": t.get("type")} for t in live.get("travelers") or []]


def get_options(locator: str, live: dict) -> dict:
    """What can be added: live seat maps and the ancillary shop on a
    post-commit workbench (discarded afterwards), plus the special requests."""
    out = {
        "special_requests": {
            "wheelchair": [{"code": c, "label": WHEELCHAIR_LABELS[c]} for c in WHEELCHAIR_SSR_FLAGS],
            "meals": [{"code": c, "label": MEAL_LABELS.get(c, t)} for c, t in MEAL_SSR_TYPES.items() if c != "NOML"],
            "meals_note": None,
        },
        "seat_maps": [], "extras": [], "seats_extras_note": None,
    }
    carriers = {f["flight"][:2] for f in live.get("flights") or []}
    if live.get("content_source") == "NDC":
        out["seats_extras_note"] = NDC_ADDONS_REFUSED
        if not carriers & NDC_MEAL_SSR_CARRIERS:
            out["special_requests"]["meals"] = []
            out["special_requests"]["meals_note"] = "This airline doesn't take meal requests online for this booking."
        return out

    wb, res = _open_workbench(locator)
    try:
        air = next((o for o in res.get("Offer") or [] if (o.get("id") or "").startswith("offer")), {})
        uuid_ = (air.get("Identifier") or {}).get("value")
        try:
            raw = workbench_service._api_post_with_retry(
                f"{TravelportConfig.base_path()}/air/search/seat/catalogofferingsancillaries/seatavailabilities",
                {"CatalogOfferingsQuerySeatAvailability": {"SeatAvailabilityOfferings": {
                    "@type": "SeatAvailabilityOfferingsBuildFromReservationWorkbench",
                    "BuildFromReservationWorkbench": {
                        "ReservationIdentifier": {"Identifier": {"value": wb, "authority": "Travelport"}},
                        **({"OfferIdentifier": {"Identifier": {"value": uuid_, "authority": "Travelport"}}} if uuid_ else {}),
                    }}}}, session_id=wb)
            out["seat_maps"] = workbench_service.parse_seat_map_response(raw)
            workbench_service.seat_rules.annotate_seat_conditions(out["seat_maps"])
        except Exception as e:
            out["seat_map_error"] = str(e)
        try:
            names = {t["id"]: t["name"] for t in live.get("travelers") or []}
            out["extras"] = [{k: x.get(k) for k in ("name", "airline_name", "description", "segment_numbers", "price",
                                                     "currency", "refundable", "service_type", "category_code",
                                                     "traveler_identifier_ref")}
                             | {"passenger": names.get(x.get("traveler_identifier_ref"))}
                             for x in workbench_service.get_ancillary_offers(wb, uuid_)]
        except Exception as e:
            logger.warning(f"{locator}: extras shop refused: {e}")
            out["extras_error"] = EXTRAS_NOT_OFFERED if "NOT AVAILABLE FOR THE REQUESTED FLIGHT" in str(e).upper() else                 "The airline's extras couldn't be loaded just now. Please try again in a few minutes."
    finally:
        _discard(wb)
    return out


def add_special_requests(locator: str, live: dict, requests: list) -> list:
    """requests: [{traveler_id, code}] — WCHR/WCHS/WCHC or a meal code.
    Free SSRs: Special Service List on a post-commit workbench, then commit."""
    travelers = travelers_of(live)
    by_id = {t["id"]: i for i, t in enumerate(travelers)}
    wanted = [dict(t) for t in travelers]
    rows = []
    for r in requests:
        idx = by_id.get(r.get("traveler_id"))
        code = (r.get("code") or "").upper()
        if idx is None or not (code in WHEELCHAIR_SSR_FLAGS or code in MEAL_SSR_TYPES):
            rows.append({"code": code, "status": "failed", "error": "Unknown passenger or request"})
            continue
        wanted[idx]["wheelchair" if code in WHEELCHAIR_SSR_FLAGS else "meal_code"] = code
    if not any(t.get("wheelchair") or t.get("meal_code") for t in wanted):
        return rows
    wb, _ = _open_workbench(locator)
    committed = False
    try:
        carrier = (live.get("flights") or [{}])[0].get("flight", "")[:2]
        sent = workbench_service.add_special_services(wb, wanted, is_gds=live.get("content_source") != "NDC", carrier=carrier)
        rows += sent
        if any(s["status"] == "sent" for s in sent):
            body = _call("POST", f"{TravelportConfig.base_path()}/air/book/reservation/reservations/{wb}", "")
            if _errors(body):
                for s in sent:
                    if s["status"] == "sent":
                        s.update(status="failed", error=f"Travelport did not save it: {_msg(_errors(body))}")
            else:
                committed = True
    finally:
        if not committed:
            _discard(wb)
    return rows


def issue_emds(locator: str, items: list, expected_supplier_total: float, currency: str) -> dict:
    """Pay Travelport for already-booked seat/extra offers on a ticketed PNR
    so the airline issues their EMDs: post-commit workbench → Cash FOP →
    Payment on just those offers (Travelport's own prices, which must add up
    to what the customer was charged before markup) → commit with Issuance.
    Same steps as ticket_service.issue_ticket, minus the air offer.
    items: the add-on's saved items — matched on Travelport's offer
    Identifier (offer_key) when saved, since offer ids get renumbered."""
    wb, res, supplier_total = _emd_workbench(locator, items, expected_supplier_total, currency)
    # EMDs already on the PNR (earlier paid seats/extras), so a retry can tell
    # whether this issue went through despite an error answer.
    if "Receipt" in res:
        before = {e["number"] for e in _emds_in(res)}
    else:
        try:
            before = {e["number"] for e in _emds_in(_reservation(_call("GET", TravelportEndpoints.retrieve_reservation(locator))))}
        except CancelError:
            before = None
    body = _call("POST", _issue_url(wb), "")
    emds = _emds_in(_reservation(body))
    # 2599 "COMMUNICATION ERROR. RETRY" (seen 2026-11-07 on Air India and
    # SriLankan, after the customer had paid): Travelport asks for a retry.
    # Only when the PNR shows no new EMD — an EMD issued despite the error is
    # never issued twice. The error also ends Travelport's work session (the
    # same session answered 8506 "WORKBENCH ID IS NOT VALID", JJ2FJQ), so each
    # retry builds a fresh one.
    for _ in range(2):
        if emds or before is None or "RETRY" not in (_msg(_errors(body)) or "").upper():
            break
        time.sleep(3)
        try:
            now = _emds_in(_reservation(_call("GET", TravelportEndpoints.retrieve_reservation(locator))))
        except CancelError:
            now = []
        emds = [e for e in now if e["number"] not in before]
        if emds:
            break
        logger.warning(f"{locator}: EMD issue answered '{_msg(_errors(body))}' — retrying")
        try:
            wb, _, _ = _emd_workbench(locator, items, expected_supplier_total, currency)
        except (Refused, CancelError) as e:
            logger.warning(f"{locator}: EMD retry could not start: {e}")
            break
        body = _call("POST", _issue_url(wb), "")
        emds = _emds_in(_reservation(body))
    if not emds:
        raise Refused(f"Travelport did not issue the EMDs: {_msg(_errors(body)) or 'no EMD returned'}")
    return {"emds": emds, "supplier_total": supplier_total}


def _issue_url(wb: str) -> str:
    return f"{TravelportConfig.base_path()}/air/book/reservation/reservations/{wb}?Issuance=Ticket&DocumentValue=Retain"


def _emd_workbench(locator: str, items: list, expected_supplier_total: float, currency: str) -> tuple:
    """A post-commit workbench with a Cash FOP and a Payment on just these
    offers, ready for the Issuance commit. (wb, workbench reservation, supplier total)"""
    wb, res = _open_workbench(locator)
    keys = {i["offer_key"] for i in items if i.get("offer_key")}
    ids = {i["offer_id"] for i in items if not i.get("offer_key") and i.get("offer_id")}
    offers = [o for o in res.get("Offer") or [] if (o.get("Identifier") or {}).get("value")
              and ((o["Identifier"]["value"] in keys) or (o.get("id") in ids))]
    if len(offers) != len(items):
        _discard(wb)
        raise Refused(f"Travelport no longer shows every item on the booking ({len(offers)} of {len(items)}).")
    supplier_total = round(sum(float((o.get("Price") or {}).get("TotalPrice") or 0) for o in offers), 2)
    if abs(supplier_total - expected_supplier_total) > 0.01:
        _discard(wb)
        raise Refused(f"The airline's price changed since payment ({currency} {expected_supplier_total} → {supplier_total}).")
    fop_id, fop_uuid = "formOfPayment_1", str(uuid.uuid4()).upper()
    _call("POST", TravelportEndpoints.add_fop_to_workbench(wb), {
        "FormOfPaymentCash": {"id": fop_id, "FormOfPaymentRef": fop_id, "Identifier": {"authority": "Travelport", "value": fop_uuid}}})
    stored = (_reservation(_call("GET", TravelportEndpoints.get_workbench(wb))).get("FormOfPayment") or [{}])[0]
    fop_id = stored.get("id") or fop_id
    fop_uuid = (stored.get("Identifier") or {}).get("value") or fop_uuid
    pay = _call("POST", TravelportEndpoints.add_payment_to_workbench(wb), {"Payment": {
        "id": "payment_1", "Identifier": {"authority": "Travelport", "value": str(uuid.uuid4()).upper()},
        "Amount": {"value": supplier_total, "code": currency, "minorUnit": 2, "currencySource": "Supplier", "approximateInd": True},
        "FormOfPaymentIdentifier": {"id": fop_id, "FormOfPaymentRef": fop_id, "Identifier": {"authority": "Travelport", "value": fop_uuid}},
        "OfferIdentifier": [{"id": o["id"], "offerRef": o["id"], "Identifier": {
            "authority": o["Identifier"].get("authority") or "Travelport", "value": o["Identifier"]["value"]}} for o in offers],
        "TravelerIdentifierRef": [{"passengerTypeCode": t.get("passengerTypeCode"), "id": t.get("id")} for t in res.get("Traveler") or []],
    }})
    if _errors(pay):
        _discard(wb)
        raise Refused(f"Travelport refused the EMD payment: {_msg(_errors(pay))}")
    return wb, res, supplier_total


def _emds_in(res: dict) -> list:
    return [{"number": d["Number"], "description": (d.get("EMDDescription") or {}).get("value")}
            for r in res.get("Receipt") or [] for d in r.get("Document") or []
            if d.get("@type") == "DocumentEMD" and d.get("Number")]

"""
Payment deadlines for seats/extras the airline confirmed on a ticketed
booking, and releasing the ones nobody paid for.

A confirmed (HK) seat or extra with no EMD is still usable at the airport
as far as the airline's PNR goes, and nothing stops it being collected or
billed to the agency later. So every such item on a ticketed booking made
through us gets a deadline: extras_pay_hours after we first see it
confirmed, or extras_pay_before_departure_hours before the flight it covers,
whichever comes first (provider settings, 24/24 by default). Departure times
are the airline's local times read as agency time (Sri Lanka), so for
flights leaving from further east the deadline can be a few hours later
than the exact figure.

When the deadline passes unpaid, the item is taken off the PNR on a
post-commit workbench. What Travelport pre-prod actually does (UL, GDS,
2026-10-30, held HNHLT7 and ticketed HNHN40):
- Ancillary Cancel (…/offers/canceloffer) on a bag: accepted with an empty
  OfferListResponse, but the bag is still on the PNR after commit — with or
  without cancelAtCommitWorkbenchInd, ProductIdentifier, TravelerIdentifier.
  Cancel Workbench Items (CancelSelectedOffers): same, no effect.
- Deleting the bag's standard SSR (XBSM/XBBJ… via specialservices/
  deletemultiple, by the SSR's Identifier value) removes the bag: its ASVC
  goes with it and after commit the passenger no longer has it — held AND
  ticketed. Deleting the ASVC first is refused ("ASVC DELETE NOT ALLOWED -
  DELETE STANDARD SSR FIRST").
- Paid seat on a ticketed booking: Ancillary Cancel answers "INTERNAL SERVER
  ERROR"; deleting the seat's ASVC strips the charge but leaves the seat
  assigned for free — so it is never done. Seats are left for staff.
So: extras with a standard SSR are released by deleting that SSR; seats
(and extras without one) go through Ancillary Cancel and, if Travelport
refuses or nothing changes, a Stuck Bookings alert asks staff to remove it.

Never released: anything in a paid add-on payment, or one the customer has
started paying on PayCorp's page within addon_billing.PAYMENT_WINDOW.

The same check flags EMDs on our PNRs that we didn't issue (emd_ledger), in
case an airline or another agent issues one that may be billed to us.
"""
import json
import logging
import threading
from datetime import datetime, timedelta

import database
from config.api_endpoints import TravelportEndpoints
from config.travelport_config import TravelportConfig
from services import addon_billing, customer_notify, live_booking_service, segment_status
from services.cancel_service import _call, _errors, _msg, _reservation, CancelError

logger = logging.getLogger(__name__)

RETRY_FAILED_RELEASE = timedelta(hours=6)
MIN_PAY_WINDOW = timedelta(hours=1)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def locator_lock(locator: str) -> threading.Lock:
    """One release / payment step per PNR at a time (this process)."""
    with _locks_guard:
        return _locks.setdefault(locator.upper(), threading.Lock())


def _utcnow() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _flights(res: dict) -> dict:
    """flight id -> {"departure_utc", "sequence"} from a retrieve."""
    offset = timedelta(minutes=TravelportConfig.AGENCY_UTC_OFFSET_MINUTES)
    out = {}
    for o in res.get("Offer") or []:
        for p in o.get("Product") or []:
            for seg in p.get("FlightSegment") or []:
                f = seg.get("Flight") or {}
                dep = f.get("Departure") or {}
                try:
                    local = datetime.fromisoformat(f"{dep['date']}T{(dep.get('time') or '00:00:00')[:8]}")
                except (KeyError, ValueError):
                    local = None
                if f.get("id"):
                    out[f["id"]] = {"departure_utc": local - offset if local else None, "sequence": seg.get("sequence")}
    return out


def _is_ticketed(res: dict, booking: dict) -> bool:
    if booking.get("ticket_number"):
        return True
    return any(d.get("@type") != "DocumentEMD" and d.get("Number")
               for r in res.get("Receipt") or [] if r.get("@type") == "ReceiptPayment" for d in r.get("Document") or [])


def _emd_numbers(res: dict) -> list:
    return [d["Number"] for r in res.get("Receipt") or [] for d in r.get("Document") or []
            if d.get("@type") == "DocumentEMD" and d.get("Number")]


def _deadline(first_seen: datetime, departure: datetime | None) -> datetime:
    """The earlier of the two settings — but never less than MIN_PAY_WINDOW
    after we first see the item, so one confirmed close to departure (or
    added then in Manage My Booking) can still be paid before it goes."""
    settings = database.get_provider_settings()
    deadline = first_seen + timedelta(hours=settings["extras_pay_hours"])
    if departure:
        deadline = min(deadline, departure - timedelta(hours=settings["extras_pay_before_departure_hours"]))
    return max(deadline, first_seen + MIN_PAY_WINDOW)


def _paid_with_ticket(booking: dict, states: dict) -> set:
    """Offer keys of seats/extras the customer paid for with the ticket
    (booking-flow items charged in total_fare: status "confirmed" in
    addon_results). Their EMD may still be missing — staff are alerted at
    ticketing then — but they are paid, so never held or released."""
    try:
        raw = json.loads(booking.get("raw_ticket_json") or "{}") or {}
    except ValueError:
        return set()
    keys = set()
    for kind in ("seats", "extras"):
        for item in (raw.get("addon_results") or {}).get(kind) or []:
            if item.get("status") == "confirmed":
                _, st = addon_billing.resolve(states, item)
                if st:
                    keys.add(st["key"])
    return keys


def _hold_label(hold: dict) -> str:
    return f"{hold['description'] or hold['kind']} ({hold['currency'] or ''} {hold['supplier_price']:,.2f} airline price)"


# ── Payment state of a held item ─────────────────────────────────────────────

def _payment_state(locator: str, hold: dict, states: dict) -> str:
    """"paid" (in a completed / EMD-failed add-on payment), "paying" (PayCorp
    page opened within the payment window) or "unpaid". An abandoned
    awaiting_payment add-on for the item is expired here, so a late return
    from PayCorp can't charge for an item that is about to be released."""
    state = "unpaid"
    for row in database.get_addon_payments_for_locator(locator):
        items = row["items"] or [{"offer_id": o} for o in row["offer_ids"]]
        if not any((addon_billing.resolve(states, i)[1] or {}).get("key") == hold["offer_key"] for i in items):
            continue
        if row["status"] in ("completed", "emd_failed"):
            return "paid"
        if addon_billing.payment_in_flight(row):
            state = "paying"
        elif row["status"] == "awaiting_payment":
            database.finish_addon_payment(row["addon_id"], "expired",
                                          {"reason": "payment deadline passed", "offer_key": hold["offer_key"]})
    return state


# ── Releasing an item from the PNR ───────────────────────────────────────────

def _open_workbench(locator: str) -> str:
    body = _call("POST", TravelportEndpoints.create_workbench_from_locator(locator))
    wb = ((_reservation(body) or body.get("ReservationWorkbench") or {}).get("Identifier") or {}).get("value")
    if not wb:
        raise CancelError(f"Travelport could not open the booking: {_msg(_errors(body)) or 'no workbench'}", "workbench")
    return wb


def _discard(wb: str) -> None:
    from services.workbench_service import discard_workbench
    try:
        discard_workbench(wb)
    except Exception as e:
        logger.warning(f"Discarding workbench {wb} failed: {e}")


def _delete_standard_ssr(wb: str, state: dict, flights: dict) -> str:
    """Delete the extra's standard SSR (its ASVC goes with it). Returns what
    was deleted; raises CancelError when it can't be pinned to one SSR."""
    res = _reservation(_call("GET", TravelportEndpoints.get_workbench(wb)))
    sequences = {flights[f]["sequence"] for f in state["flight_refs"] if f in flights}
    matches = [x for x in res.get("SpecialService") or []
               if x.get("SSRCode") == state["ssr_code"]
               and (not state["traveler_refs"] or (x.get("TravelerIdentifier") or {}).get("TravelerRef") in state["traveler_refs"])
               and (not sequences or set((x.get("AppliesTo") or {}).get("SegmentSequenceList") or []) & sequences)]
    if len(matches) != 1:
        raise CancelError(f"found {len(matches)} {state['ssr_code']} SSRs for this passenger and flight, expected one", "ssr_match")
    ssr_id = (matches[0].get("Identifier") or {}).get("value")
    url = TravelportEndpoints.add_special_services(wb).rsplit("/", 1)[0] + "/deletemultiple"
    body = _call("POST", url, {"@type": "SpecialServiceQueryDeleteMultiple", "SpecialServiceID": [ssr_id]})
    if _errors(body):
        raise CancelError(f"Travelport refused to delete SSR {state['ssr_code']}: {_msg(_errors(body))}", "ssr_delete")
    return f"SSR {state['ssr_code']} deleted"


def _ancillary_cancel(wb: str, oid: str, state: dict) -> str:
    body = _call("POST", TravelportEndpoints.ndc_refund_quote(wb), {
        "@type": "OfferQueryCancelOffer", "cancelAtCommitWorkbenchInd": True,
        "BuildFromOffer": {"@type": "BuildFromOffer", "OfferIdentifier": {
            "id": oid, "Identifier": {"authority": "Travelport", "value": state["key"]}}}})
    if _errors(body):
        raise CancelError(f"Travelport refused Ancillary Cancel: {_msg(_errors(body))}", "ancillary_cancel")
    return "Ancillary Cancel sent"


def _snapshot(locator: str, res: dict, skip_key: str) -> dict:
    """What a release must never change: flight segment statuses, ticket
    numbers, ticket coupon statuses, and every other seat/extra that is
    confirmed or has an EMD."""
    tickets = sorted(d["Number"] for r in res.get("Receipt") or [] if r.get("@type") == "ReceiptPayment"
                     for d in r.get("Document") or [] if d.get("@type") != "DocumentEMD" and d.get("Number"))
    ndc = any(o.get("ContentSource") == "NDC" for o in res.get("Offer") or [])
    coupons = {}
    for number in tickets:
        detail = live_booking_service._ticket_coupons(number, locator, ndc)
        coupons[number] = None if detail.get("error") else [(c["flight"], c["status"]) for c in detail["coupons"]]
    # Compared by what the item is, not its Identifier: an unpriced seat's
    # Identifier changes on every retrieve (seen live on HNHN40).
    others = {}
    for st in addon_billing.offer_states(res).values():
        if st["key"] != skip_key and ((st["status"] or "").lower() == "confirmed" or st["emd"]):
            sig = (st["kind"], (st["seat"] or "").lstrip("0") or st["description"], tuple(st["flight_refs"]),
                   tuple(st["traveler_refs"]), st["supplier_price"])
            others[sig] = others.get(sig, ()) + ((st["status"], st["emd"]),)
    return {"segments": {f: c for f, (c, _) in segment_status.segment_statuses(res).items()},
            "tickets": tickets, "coupons": coupons, "others": others}


def _unhealthy(snap: dict) -> str | None:
    """Why a booking isn't safe to touch: a flight not confirmed (HK), or a
    ticket coupon that isn't open for use (checked in, used, exchanged…)."""
    bad = [f"{f} {c}" for f, c in snap["segments"].items() if not segment_status.is_confirmed((c, None))]
    if bad:
        return f"flight segment(s) not confirmed: {', '.join(bad)}"
    closed = [f"{n} {f} {s}" for n, cs in snap["coupons"].items() for f, s in cs or [] if s != "OpenForUse"]
    if closed:
        return f"ticket coupon(s) not open for use: {', '.join(closed)}"
    return None


def _changes(before: dict, after: dict) -> list:
    out = []
    if after["segments"] != before["segments"]:
        out.append(f"flight segments {before['segments']} → {after['segments']}")
    if after["tickets"] != before["tickets"]:
        out.append(f"tickets {before['tickets']} → {after['tickets']}")
    for number, cs in before["coupons"].items():
        if cs is not None and after["coupons"].get(number) != cs:
            out.append(f"ticket {number} coupons {cs} → {after['coupons'].get(number)}")
    lost = [k for k, v in before["others"].items() if after["others"].get(k) != v]
    if lost:
        out.append(f"{len(lost)} other paid/confirmed seat(s)/extra(s) changed")
    return out


def release(locator: str, hold: dict, res: dict) -> dict:
    """Take one unpaid seat/extra off the PNR and check it's gone — and that
    nothing else moved: the ticket, its coupons, the flights and every other
    seat/extra must be exactly as before (_snapshot). Only that one SSR or
    ancillary offer is ever cancelled; the ticket and flights are never
    touched. Returns {"released": bool, "how"/"error", "integrity": …}."""
    res = addon_billing.retrieve(locator)  # fresh, right before the change
    states = addon_billing.offer_states(res)
    oid, state = addon_billing.resolve(states, {"offer_key": hold["offer_key"]})
    if not state:
        return {"released": True, "how": "no longer on the booking"}
    before = _snapshot(locator, res, hold["offer_key"])
    problem = _unhealthy(before)
    if problem:
        return {"released": False, "error": f"not released — {problem}", "before": before}
    wb = _open_workbench(locator)
    committed = False
    try:
        if state["kind"] == "extra" and state["ssr_code"]:
            how = _delete_standard_ssr(wb, state, _flights(res))
        else:
            how = _ancillary_cancel(wb, oid, state)
        body = _call("POST", TravelportEndpoints.commit_workbench(wb), "")
        committed = True
        if _errors(body):
            raise CancelError(f"Travelport commit failed: {_msg(_errors(body))}", "commit")
    except CancelError as e:
        return {"released": False, "error": str(e)}
    finally:
        if not committed:
            _discard(wb)
    res_after = addon_billing.retrieve(locator)
    after = _snapshot(locator, res_after, hold["offer_key"])
    changed = _changes(before, after)
    integrity = {"tickets": after["tickets"], "segments": after["segments"], "coupons": after["coupons"],
                 "other_items_unchanged": len(before["others"])}
    if changed:
        return {"released": False, "integrity_broken": True, "integrity": integrity,
                "error": f"{how} and committed, but something else on the booking changed: {'; '.join(changed)}"}
    _, still = addon_billing.resolve(addon_billing.offer_states(res_after), {"offer_key": hold["offer_key"]})
    if still and (still["status"] or "").lower() == "confirmed":
        return {"released": False, "integrity": integrity,
                "error": f"{how} and committed, but the airline still shows it confirmed on the booking"}
    return {"released": True, "how": how, "integrity": integrity}


# ── The check: run on Booking Status lookup and by the background sweep ─────

def check_booking(locator: str, res: dict | None = None, release_overdue: bool = True) -> dict:
    """Start the clock on newly confirmed unpaid items, close holds that were
    paid or dropped, release overdue ones and flag EMDs we didn't issue.
    Only for bookings made through us. Returns {"holds": open holds,
    "released": [...], "failed": [...], "foreign_emds": [...]}."""
    out = {"holds": [], "released": [], "failed": [], "foreign_emds": []}
    booking = database.get_booking_by_locator(locator)
    if not booking:
        return out
    messages = []  # (event, items, info) — sent after the PNR lock is released
    try:
        _check_locked(locator, booking, res, release_overdue, out, messages)
    finally:
        for event, items, info in messages:
            customer_notify.notify(locator, event, items, **info)
    return out


def _friendly_names(locator: str, booking: dict, states: dict) -> dict:
    """offer key -> the name the customer saw when asking for it."""
    try:
        raw = json.loads(booking.get("raw_ticket_json") or "{}") or {}
    except ValueError:
        raw = {}
    items = [i for kind in ("seats", "extras") for i in (raw.get("addon_results") or {}).get(kind) or []]
    items += [i for row in database.get_addon_payments_for_locator(locator) for i in row["items"]]
    names = {}
    for i in items:
        _, st = addon_billing.resolve(states, i)
        name = i.get("name") or (f"Seat {i['seat_number']}" if i.get("seat_number") else None)
        if st and name:
            names.setdefault(st["key"], name)
    return names


def _msg_item(hold: dict, names: dict) -> dict:
    price = addon_billing._price({"supplier_price": hold["supplier_price"]}, hold["kind"], addon_billing.get_pricing_settings())
    return {"key": hold["offer_key"], "name": names.get(hold["offer_key"]) or hold["description"] or hold["kind"],
            "price": price, "currency": hold["currency"]}


REFUSED_STATUSES = ("rejected", "cancelled", "unable", "notconfirmed")


def _refused_requests(locator: str, booking: dict, states: dict, holds: dict) -> list:
    """Seats/extras the customer asked for that the airline has refused:
    an explicit refusal status, or (tracked by Identifier) gone from the PNR
    without ever being confirmed."""
    try:
        raw = json.loads(booking.get("raw_ticket_json") or "{}") or {}
    except ValueError:
        raw = {}
    requested = [i for kind in ("seats", "extras") for i in (raw.get("addon_results") or {}).get(kind) or []
                 if i.get("status") == "pending"]
    requested += [i for row in database.get_addon_payments_for_locator(locator) if row["status"] == "awaiting_airline"
                  for i in row["items"]]
    out = []
    for i in requested:
        _, st = addon_billing.resolve(states, i)
        key = i.get("offer_key") or i.get("offer_id")
        if not key:
            continue
        refused = (st and (st["status"] or "").lower() in REFUSED_STATUSES) or \
                  (not st and i.get("offer_key") and i["offer_key"] not in holds)
        if refused:
            out.append({"key": key, "name": i.get("name") or (f"Seat {i['seat_number']}" if i.get("seat_number") else "Extra")})
    return out


def _check_locked(locator: str, booking: dict, res: dict | None, release_overdue: bool, out: dict, messages: list) -> None:
    with locator_lock(locator):
        res = res or addon_billing.retrieve(locator)
        out["foreign_emds"] = _flag_foreign_emds(locator, res)
        if not _is_ticketed(res, booking):
            return
        states = addon_billing.offer_states(res)
        flights = _flights(res)
        now = _utcnow()
        holds = {h["offer_key"]: h for h in database.get_addon_holds(locator)}
        paid_with_ticket = _paid_with_ticket(booking, states)
        names = _friendly_names(locator, booking, states)
        new_holds = []
        settings = database.get_provider_settings()
        reminder_before = timedelta(hours=settings["extras_reminder_hours"])
        # Only while every flight is confirmed: if the airline dropped the
        # flight itself, that's the airline-cancelled flow, not "refused".
        flights_ok = not segment_status.booking_problem({f: (c, v) for f, (c, v) in segment_status.segment_statuses(res).items()})
        refused = _refused_requests(locator, booking, states, holds) if flights_ok else []
        if refused:
            messages.append(("refused", refused, {}))
        for key in paid_with_ticket & set(holds):
            database.close_addon_hold(locator, key, "paid", {"note": "charged with the ticket (EMD pending — see Stuck Bookings)"})
        for oid, st in states.items():
            if (st["status"] or "").lower() == "confirmed" and not st["emd"] and st["supplier_price"] \
                    and st["key"] not in holds and st["key"] not in paid_with_ticket:
                departure = min((flights[f]["departure_utc"] for f in st["flight_refs"]
                                 if f in flights and flights[f]["departure_utc"]), default=None)
                holds[st["key"]] = database.start_addon_hold(
                    locator, st["key"], oid, st["kind"], st["description"] or (f"Seat {st['seat']}" if st["seat"] else None),
                    st["ssr_code"], st["supplier_price"], st["currency"], departure, now, _deadline(now, departure))
                logger.info(f"{locator}: unpaid {st['kind']} {st['description']} confirmed — pay by {holds[st['key']]['deadline_utc']} UTC")
                new_holds.append(holds[st["key"]])
        if new_holds:
            messages.append(("payment_due", [_msg_item(h, names) for h in new_holds],
                             {"deadline_utc": min(h["deadline_utc"] for h in new_holds)}))
        reminders = []
        by_key = {st["key"]: st for st in states.values()}
        for key, hold in holds.items():
            if hold["status"] not in ("unpaid", "release_failed") or key in paid_with_ticket:
                continue
            st = by_key.get(key)
            if st and st["emd"]:
                database.close_addon_hold(locator, key, "paid", {"emd": st["emd"]})
                continue
            if not st or (st["status"] or "").lower() != "confirmed":
                if hold["status"] == "release_failed" and "committed" in ((hold.get("result") or {}).get("error") or ""):
                    # Our earlier change went through after all.
                    database.close_addon_hold(locator, key, "released", {**(hold.get("result") or {}), "released": True})
                    out["released"].append(hold)
                    messages.append(("removed", [_msg_item(hold, names)], {}))
                else:
                    database.close_addon_hold(locator, key, "gone", {"airline_status": (st or {}).get("status")})
                continue
            if not release_overdue or now < hold["deadline_utc"]:
                out["holds"].append(hold)
                # Reminder — unless the item was first seen inside the
                # reminder window (the payment-due message had the deadline).
                due_from = hold["deadline_utc"] - reminder_before
                if reminder_before and now >= due_from and hold["first_confirmed_utc"] < due_from and now < hold["deadline_utc"]:
                    reminders.append(hold)
                continue
            payment = _payment_state(locator, hold, states)
            if payment == "paid":
                database.close_addon_hold(locator, key, "paid", {"note": "paid add-on payment"})
                continue
            if payment == "paying":
                out["holds"].append(hold)
                continue
            last = (hold.get("result") or {}).get("attempted_utc")
            if hold["status"] == "release_failed" and last and now - datetime.fromisoformat(last) < RETRY_FAILED_RELEASE:
                out["failed"].append(hold)
                continue
            try:
                result = release(locator, hold, res)
            except CancelError as e:
                result = {"released": False, "error": str(e)}
            result["attempted_utc"] = now.isoformat()
            if result["released"]:
                database.close_addon_hold(locator, key, "released", result)
                out["released"].append({**hold, "result": result})
                logger.info(f"{locator}: released unpaid {_hold_label(hold)} — {result['how']}")
                messages.append(("removed", [_msg_item(hold, names)], {}))
                res = addon_billing.retrieve(locator)  # offer ids move after a change
                states = addon_billing.offer_states(res)
            else:
                first_failure = hold["status"] == "unpaid"
                database.close_addon_hold(locator, key, "release_failed", result)
                out["failed"].append({**hold, "result": result})
                logger.warning(f"{locator}: could not release unpaid {_hold_label(hold)}: {result['error']}")
                if result.get("integrity_broken"):
                    _alert(locator, f"URGENT: removing unpaid {_hold_label(hold)} changed something else on this booking: "
                                    f"{result['error']}. Check the ticket, flights and paid seats/extras in Smartpoint now.")
                elif first_failure:
                    _alert(locator, f"Unpaid {_hold_label(hold)} on this ticketed booking passed its payment deadline "
                                    f"({hold['deadline_utc']} UTC) but could not be removed through Travelport: "
                                    f"{result['error']}. Remove it in Smartpoint (seat: release the seat; extra: delete its SSR) "
                                    f"so it can't be used or billed unpaid. No payment was taken.")
        if reminders:
            messages.append(("reminder", [_msg_item(h, names) for h in reminders],
                             {"deadline_utc": min(h["deadline_utc"] for h in reminders)}))


def _flag_foreign_emds(locator: str, res: dict) -> list:
    """EMDs on the PNR that we didn't issue — alert staff once per EMD."""
    numbers = _emd_numbers(res)
    if not numbers:
        return []
    known = database.get_emd_origins(locator)
    new = [n for n in numbers if n not in known]
    if new:
        database.record_emds(locator, new, "not_ours")
        _alert(locator, f"EMD(s) {', '.join(new)} on this booking were not issued by us (airline or another agent). "
                        f"Check in Smartpoint whether they will be billed to the agency.")
        logger.warning(f"{locator}: EMD(s) not issued by us: {new}")
    return [n for n in numbers if (known.get(n) or "not_ours") == "not_ours"]


def _alert(locator: str, message: str) -> None:
    try:
        database.create_stuck_booking_notification(locator, message, None, False)
    except Exception as e:
        logger.error(f"Could not log stuck-booking alert for {locator}: {e}")


# ── Before taking payment ────────────────────────────────────────────────────

class NotPayable(Exception):
    """An add-on item can't be paid for (released, no longer confirmed,
    already paid, past its deadline, or re-priced). Message is for the customer."""


def check_payable(locator: str, items: list, expected_supplier_total: float, at_completion: bool = False) -> None:
    """Raises NotPayable unless every item is still on the PNR, confirmed,
    without an EMD, at the price the customer is about to pay, and not
    released. Before PayCorp opens it must also be within its deadline; at
    completion it needn't (the sweep leaves an item alone while it's being
    paid). CancelError if Travelport can't be asked."""
    states = addon_billing.offer_states(addon_billing.retrieve(locator))
    holds = {h["offer_key"]: h for h in database.get_addon_holds(locator)}
    total = 0.0
    now = _utcnow()
    for item in items:
        _, st = addon_billing.resolve(states, item)
        name = item.get("name") or item.get("description") or "This item"
        if not st or (st["status"] or "").lower() != "confirmed":
            raise NotPayable(f"{name} is no longer on your booking, so it can't be paid for.")
        if st["emd"]:
            raise NotPayable(f"{name} has already been paid for.")
        hold = holds.get(st["key"])
        if hold and hold["status"] in ("released", "gone"):
            raise NotPayable(f"{name} was removed from your booking because it wasn't paid by its deadline.")
        if hold and hold["status"] == "unpaid" and now >= hold["deadline_utc"] and not at_completion:
            raise NotPayable(f"The payment deadline for {name} has passed, so it is being removed from your booking.")
        total += st["supplier_price"]
    if abs(round(total, 2) - round(expected_supplier_total, 2)) > 0.01:
        raise NotPayable(f"The airline's price has changed since this payment was set up "
                         f"({expected_supplier_total:,.2f} → {total:,.2f}). Please look the booking up again.")


def mark_paid(locator: str, items: list, emds: list) -> None:
    """After EMDs were issued for a paid add-on: our EMDs, holds closed."""
    numbers = [e.get("number") for e in emds]
    database.record_emds(locator, numbers, "ours_addon")
    for item in items:
        if item.get("offer_key"):
            database.close_addon_hold(locator, item["offer_key"], "paid", {"emds": numbers})
    customer_notify.notify(locator, "paid", [{"key": i.get("offer_key") or i.get("offer_id"), "name": i.get("name") or i.get("description")
                                              or (f"Seat {i['seat_number']}" if i.get("seat_number") else "Extra"),
                                              "price": i.get("price"), "currency": i.get("currency")} for i in items],
                           emds=numbers)


# ── Background sweep ─────────────────────────────────────────────────────────

def sweep() -> dict:
    """Every ticketed upcoming booking of ours (and any with an open hold)."""
    summary = {"checked": 0, "released": 0, "failed": 0, "errors": 0}
    for locator in database.get_locators_for_extras_sweep():
        try:
            r = check_booking(locator)
            summary["checked"] += 1
            summary["released"] += len(r["released"])
            summary["failed"] += len(r["failed"])
        except Exception as e:
            summary["errors"] += 1
            logger.warning(f"Seats/extras deadline check for {locator} failed: {e}")
    return summary


def start_sweeper(interval_minutes: int) -> None:
    """Daemon thread running sweep() every interval_minutes (0 = off)."""
    if interval_minutes <= 0:
        logger.info("Seats/extras deadline sweep is off (EXTRAS_SWEEP_MINUTES=0)")
        return

    def loop():
        import time
        time.sleep(60)  # let the app finish starting
        while True:
            try:
                summary = sweep()
                logger.info(f"Seats/extras deadline sweep: {summary}")
            except Exception as e:
                logger.error(f"Seats/extras deadline sweep failed: {e}")
            time.sleep(interval_minutes * 60)

    threading.Thread(target=loop, name="extras-deadline-sweep", daemon=True).start()
    logger.info(f"Seats/extras deadline sweep every {interval_minutes} min")

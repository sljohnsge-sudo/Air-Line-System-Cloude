"""
Seats and extras the airline only "requested" at first and confirmed later.

When a seat or extra is booked, many airlines answer "Pending" (UL bags:
requested, then Confirmed a few minutes later — seen live 2026-10-30 on
HNHLT7). We never charge a pending item, so before this module an item the
airline confirmed afterwards stayed unbilled and its EMD was never issued.

- Held booking (no ticket yet): reconcile_held_booking() runs before the card
  payment starts (/api/payments/init). Items the airline now confirms become
  "confirmed" in the booking's addon_results and are added to its total, so
  the normal pay-and-ticket flow charges them and issue_ticket pays their
  offers (EMDs issue with the ticket). Items the airline refused are marked so.
- Ticketed booking: payable_after_ticketing() lists items the customer asked
  for (booking flow addon_results, or Manage My Booking requests saved as
  addon_payments rows in status "awaiting_airline") that the airline now
  confirms and that have no EMD yet. Booking Status shows them as payment
  due; paying runs the same add-on payment + issue_emds() flow. Any other
  confirmed paid item without an EMD is included too, each with the payment
  deadline services/addon_deadlines.py gives it (unpaid ones are released).

Prices: Travelport's current price for the offer on the PNR, with our
markup — never a stored or browser figure.
"""
import logging
from datetime import datetime, timedelta

import database
from config.api_endpoints import TravelportEndpoints
from services.cancel_service import _call, _reservation
from services.pricing_service import get_settings as get_pricing_settings, apply_markup

logger = logging.getLogger(__name__)


# A card payment the customer has started on PayCorp's page: its items are
# never released or billed again until this long after it started. PayCorp
# takes no money until we call "complete" when the customer comes back, and
# complete-payment refuses a payment whose add-on has expired by then.
PAYMENT_WINDOW = timedelta(minutes=60)


def payment_in_flight(row: dict) -> bool:
    """An awaiting_payment add-on whose PayCorp page was opened recently."""
    started = row.get("payment_started_at")
    return (row.get("status") == "awaiting_payment" and started is not None
            and datetime.utcnow() - started < PAYMENT_WINDOW)


def retrieve(locator: str) -> dict:
    return _reservation(_call("GET", TravelportEndpoints.retrieve_reservation(locator)))


def live_offer_states(locator: str) -> dict:
    """offer id -> state (see offer_states) for every seat/extra offer on the
    PNR, read live from Travelport."""
    return offer_states(retrieve(locator))


def offer_states(res: dict) -> dict:
    """offer id -> {status, supplier_price, currency, emd, kind, description,
    key (Travelport's offer Identifier — offer ids get renumbered), ssr_code,
    flight_refs, traveler_refs} for every seat/extra offer in a retrieve."""
    states = {}
    for o in res.get("Offer") or []:
        for p in o.get("Product") or []:
            if p.get("@type") != "ProductAncillary":
                continue
            anc = p.get("Ancillary") or {}
            price = o.get("Price") or {}
            states[o.get("id")] = {
                "status": None, "emd": None,
                "key": (o.get("Identifier") or {}).get("value") or o.get("id"),
                "supplier_price": float(price.get("TotalPrice") or 0),
                "currency": (price.get("CurrencyCode") or {}).get("value"),
                "kind": "seat" if anc.get("@type") == "AncillaryAirSeat" else "extra",
                "description": next((d.get("value") for d in anc.get("Description") or []
                                     if d.get("value") and not d["value"].startswith("**")), None),
                "seat": (anc.get("SeatAssignment") or {}).get("Seat"),
                "ssr_code": next((d.get("ssrCode") for d in anc.get("Description") or [] if d.get("ssrCode")), None),
                "flight_refs": anc.get("FlightRef") or [],
                "traveler_refs": [(b.get("TravelerIdentifierRef") or {}).get("id") for b in price.get("PriceBreakdown") or []
                                  if (b.get("TravelerIdentifierRef") or {}).get("id")],
            }
    for tp in res.get("TravelerProduct") or []:
        if tp.get("OfferRef") in states and tp.get("ConfirmationStatusEnum"):
            states[tp["OfferRef"]]["status"] = tp["ConfirmationStatusEnum"]
    for r in res.get("Receipt") or []:
        for st in (((r.get("Confirmation") or {}).get("OfferStatus") or {}).get("StatusAncillary") or []):
            for ref in r.get("OfferRef") or []:
                if ref in states and not states[ref]["status"]:
                    states[ref]["status"] = st.get("value")
        if r.get("@type") == "ReceiptPayment":
            for d in r.get("Document") or []:
                if d.get("@type") == "DocumentEMD":
                    for ref in r.get("OfferRef") or []:
                        if ref in states:
                            states[ref]["emd"] = d.get("Number")
    return states


def _price(state: dict, kind: str, settings: dict) -> float:
    supplier = state["supplier_price"]
    return apply_markup(supplier, settings, "seat" if kind == "seat" else "ancillary")[0] if supplier else 0.0


def _raw_booking(locator: str) -> dict | None:
    import json
    cached = database.get_booking_by_locator(locator)
    if not cached:
        return None
    try:
        return json.loads(cached.get("raw_ticket_json") or "{}") or None
    except ValueError:
        return None


def reconcile_held_booking(locator: str) -> dict:
    """Held booking: pending seats/extras the airline now confirms are
    charged from now on (added to the total). Returns
    {"confirmed": [...], "refused": [...], "added": amount}."""
    out = {"confirmed": [], "refused": [], "added": 0.0}
    raw = _raw_booking(locator)
    if not raw or raw.get("ticket_number"):
        return out
    addons = raw.get("addon_results") or {}
    pending = [(kind, i) for kind in ("seats", "extras") for i in addons.get(kind) or []
               if i.get("status") == "pending" and i.get("offer_id")]
    if not pending:
        return out
    res = retrieve(locator)
    states = offer_states(res)
    from services import segment_status
    flights_ok = not segment_status.booking_problem(segment_status.segment_statuses(res))
    settings = get_pricing_settings()
    for kind, item in pending:
        state = states.get(item["offer_id"])
        status = ((state or {}).get("status") or "").lower()
        if not state or status in ("rejected", "cancelled", "unable", "notconfirmed"):
            item.update(status="failed", reason="The airline didn't confirm it — not charged")
            out["refused"].append(item)
        elif status == "confirmed":
            price = _price(state, "seat" if kind == "seats" else "extra", settings)
            item.update(status="confirmed", price=price, supplier_price=state["supplier_price"],
                        currency=state["currency"] or item.get("currency"), reason=None)
            out["confirmed"].append(item)
            out["added"] = round(out["added"] + price, 2)
            if kind == "extras":
                raw["ancillary_charge"] = round(float(raw.get("ancillary_charge") or 0) + price, 2)
                raw.setdefault("ancillaries", []).append(
                    {k: item.get(k) for k in ("passenger_idx", "name", "price", "currency", "service_type", "offer_id", "refundable", "status")})
            raw["seat_charge"] = round(float(raw.get("seat_charge") or 0) + price, 2)
    if out["refused"] and flights_ok:  # a dropped flight isn't "refused extras"
        from services import customer_notify
        customer_notify.notify(locator, "refused", [{"key": i["offer_id"], "name": i.get("name") or
                                                     (f"Seat {i['seat_number']}" if i.get("seat_number") else "Extra")}
                                                    for i in out["refused"]])
    if out["confirmed"] or out["refused"]:
        raw["addon_results"] = addons
        raw["total_fare"] = round(float(raw.get("total_fare") or 0) + out["added"], 2)
        database.save_booking(raw)
        logger.info(f"{locator}: airline confirmed {len(out['confirmed'])} pending item(s) (+{out['added']}), "
                    f"refused {len(out['refused'])}")
    return out


def resolve(states: dict, item: dict) -> tuple[str | None, dict | None]:
    """The live offer for a saved item: by Travelport's offer Identifier when
    we have it (offer ids are renumbered as items come and go), else by id."""
    key = item.get("offer_key")
    if key:
        for oid, state in states.items():
            if state["key"] == key:
                return oid, state
        return None, None
    oid = item.get("offer_id")
    state = states.get(oid)
    if not state:
        return None, None
    # Saved without an Identifier: the id may now be a different item (seen
    # live on HNHN40 — a released seat's "an_o2" became a new bag's), so it
    # must at least be the same seat / the same extra.
    if item.get("seat_number"):
        if state["kind"] != "seat" or (state["seat"] or "").lstrip("0") != str(item["seat_number"]).lstrip("0"):
            return None, None
    elif state["kind"] != "extra" or (item.get("airline_name") and state["description"]
                                      and item["airline_name"].upper() != state["description"].upper()):
        return None, None
    return oid, state


def payable_after_ticketing(locator: str, res: dict | None = None) -> dict:
    """Ticketed booking: seats/extras the airline has confirmed that have no
    EMD yet, priced now — items the customer asked for, plus any other
    confirmed paid item on the PNR (services/addon_deadlines.py tracks
    those). Each carries its payment deadline when one is set.
    {"items": [...], "amount", "supplier_total", "currency"}."""
    candidates = []
    raw = _raw_booking(locator)
    for kind in ("seats", "extras"):
        for i in ((raw or {}).get("addon_results") or {}).get(kind) or []:
            if i.get("status") == "pending" and i.get("offer_id"):
                candidates.append({**i, "kind": "seat" if kind == "seats" else "extra"})
    rows = database.get_addon_payments_for_locator(locator)
    for row in rows:
        # awaiting_airline: requested, waiting for the airline. awaiting_payment
        # that isn't in flight: the customer left PayCorp's page without paying.
        if row["status"] == "awaiting_airline" or (row["status"] == "awaiting_payment" and not payment_in_flight(row)):
            candidates += [{**i, "kind": "seat" if i.get("seat_number") else "extra"} for i in row["items"] if i.get("offer_id")]
    holds = {h["offer_key"]: h for h in database.get_addon_holds(locator)}
    for h in holds.values():
        if h["status"] in ("unpaid", "release_failed"):
            candidates.append({"offer_id": h["offer_id"], "offer_key": h["offer_key"], "kind": h["kind"],
                               "name": h["description"] or ("Seat" if h["kind"] == "seat" else "Extra")})
    if not candidates:
        return {"items": [], "amount": 0.0, "supplier_total": 0.0, "currency": None}
    states = offer_states(res or retrieve(locator))
    # Never bill an offer twice: skip anything in a paid add-on payment or one
    # the customer is paying right now.
    taken = set()
    for row in rows:
        if row["status"] in ("completed", "emd_failed") or payment_in_flight(row):
            for i in row["items"] or [{"offer_id": o} for o in row["offer_ids"]]:
                oid, state = resolve(states, i)
                if state:
                    taken.add(state["key"])
    settings = get_pricing_settings()
    items, seen = [], set()
    for item in candidates:
        oid, state = resolve(states, item)
        if not state or state["key"] in taken or state["key"] in seen:
            continue
        hold = holds.get(state["key"])
        if hold and hold["status"] not in ("unpaid", "release_failed"):
            continue  # released, or no longer on the booking
        if (state["status"] or "").lower() == "confirmed" and not state["emd"] and state["supplier_price"]:
            seen.add(state["key"])
            items.append({**item, "offer_id": oid, "offer_key": state["key"], "status": "confirmed",
                          "price": _price(state, item["kind"], settings),
                          "supplier_price": state["supplier_price"], "currency": state["currency"] or item.get("currency"),
                          "description": state["description"],
                          "deadline_utc": hold["deadline_utc"].isoformat() + "Z" if hold else None})
    return {"items": items, "amount": round(sum(i["price"] for i in items), 2),
            "supplier_total": round(sum(i["supplier_price"] for i in items), 2),
            "currency": items[0]["currency"] if items else None}

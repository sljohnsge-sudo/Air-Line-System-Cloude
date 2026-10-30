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
  due; paying runs the same add-on payment + issue_emds() flow.

Prices: Travelport's current price for the offer on the PNR, with our
markup — never a stored or browser figure.
"""
import logging

import database
from config.api_endpoints import TravelportEndpoints
from services.cancel_service import _call, _reservation
from services.pricing_service import get_settings as get_pricing_settings, apply_markup

logger = logging.getLogger(__name__)


def live_offer_states(locator: str) -> dict:
    """offer id -> {status, supplier_price, currency, emd, kind, description}
    for every seat/extra offer on the PNR, read live from Travelport."""
    res = _reservation(_call("GET", TravelportEndpoints.retrieve_reservation(locator)))
    states = {}
    for o in res.get("Offer") or []:
        for p in o.get("Product") or []:
            if p.get("@type") != "ProductAncillary":
                continue
            anc = p.get("Ancillary") or {}
            price = o.get("Price") or {}
            states[o.get("id")] = {
                "status": None, "emd": None,
                "supplier_price": float(price.get("TotalPrice") or 0),
                "currency": (price.get("CurrencyCode") or {}).get("value"),
                "kind": "seat" if anc.get("@type") == "AncillaryAirSeat" else "extra",
                "description": next((d.get("value") for d in anc.get("Description") or []
                                     if d.get("value") and not d["value"].startswith("**")), None),
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
    states = live_offer_states(locator)
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
    if out["confirmed"] or out["refused"]:
        raw["addon_results"] = addons
        raw["total_fare"] = round(float(raw.get("total_fare") or 0) + out["added"], 2)
        database.save_booking(raw)
        logger.info(f"{locator}: airline confirmed {len(out['confirmed'])} pending item(s) (+{out['added']}), "
                    f"refused {len(out['refused'])}")
    return out


def payable_after_ticketing(locator: str) -> dict:
    """Ticketed booking: items the customer asked for that the airline has
    since confirmed and that have no EMD yet, priced now.
    {"items": [...], "amount", "supplier_total", "currency"}."""
    candidates = {}
    raw = _raw_booking(locator)
    for kind in ("seats", "extras"):
        for i in ((raw or {}).get("addon_results") or {}).get(kind) or []:
            if i.get("status") == "pending" and i.get("offer_id"):
                candidates[i["offer_id"]] = {**i, "kind": "seat" if kind == "seats" else "extra"}
    for row in database.get_addon_payments_for_locator(locator):
        if row["status"] == "awaiting_airline":
            for i in row["items"]:
                if i.get("offer_id"):
                    candidates.setdefault(i["offer_id"], {**i, "kind": "seat" if i.get("seat_number") else "extra"})
    # Never bill an offer twice: skip anything already in an open or paid add-on payment.
    for row in database.get_addon_payments_for_locator(locator):
        if row["status"] in ("awaiting_payment", "completed", "emd_failed"):
            for oid in row["offer_ids"]:
                candidates.pop(oid, None)
    if not candidates:
        return {"items": [], "amount": 0.0, "supplier_total": 0.0, "currency": None}
    states = live_offer_states(locator)
    settings = get_pricing_settings()
    items = []
    for oid, item in candidates.items():
        state = states.get(oid)
        if state and (state["status"] or "").lower() == "confirmed" and not state["emd"] and state["supplier_price"]:
            items.append({**item, "status": "confirmed", "price": _price(state, item["kind"], settings),
                          "supplier_price": state["supplier_price"], "currency": state["currency"] or item.get("currency"),
                          "description": state["description"]})
    return {"items": items, "amount": round(sum(i["price"] for i in items), 2),
            "supplier_total": round(sum(i["supplier_price"] for i in items), 2),
            "currency": items[0]["currency"] if items else None}

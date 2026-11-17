"""
services/hotel_checkout_api.py
==============================
API for hotel payment (services/hotel_checkout.py explains the flow):

  POST /api/hotels/checkout                       start: re-price, order, bank page
  POST /api/hotels/checkout/{order_ref}/complete   after the bank: verify, reserve
  POST /api/hotels/checkout/{order_ref}/cancel     customer left the bank page unpaid
  GET  /api/admin/hotel-orders                    staff: hotel orders (book_failed = act)

Mounted by main.py (app.include_router). The payment checks are main.py's
own (_verify_and_claim_payment, _require_card_enabled), imported when a
request arrives so there is no import loop.
"""
import logging
import sys
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, EmailStr

import auth
import hotel_database
from services import hotel_checkout, hotel_rules_service, hotel_booking_service
from services.hotel_common import HotelApiError

logger = logging.getLogger(__name__)
router = APIRouter()


def _main():
    return sys.modules.get("main") or __import__("main")


class CheckoutGuest(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=20)


class HotelCheckoutRequest(BaseModel):
    offer_id: str = Field(..., min_length=4, max_length=255, description="offer_id from /api/hotels/availability")
    guest: CheckoutGuest
    property_name: Optional[str] = Field(default=None, max_length=200)
    city: Optional[str] = Field(default=None, max_length=100)
    country_code: Optional[str] = Field(default=None, max_length=5)
    return_url: str = Field(..., max_length=500, description="The site's hotel page; the bank returns here")


class HotelCheckoutComplete(BaseModel):
    reqid: str = Field(..., min_length=8, max_length=64)


def _site_hotels_url(return_url: str) -> str:
    """Only the site's own origin is kept; the bank always returns to /hotels."""
    parts = urlsplit(return_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise HTTPException(status_code=422, detail="Invalid return address.")
    return f"{parts.scheme}://{parts.netloc}/hotels"


def _public_order(row: dict) -> dict:
    o = row.get("order") or {}
    return {
        "order_ref": row["order_ref"], "status": row["status"],
        "property_name": row.get("property_name"), "room_description": o.get("room_description"),
        "rate_source": o.get("authority"),
        "check_in_date": str(row.get("check_in_date") or ""), "check_out_date": str(row.get("check_out_date") or ""),
        "hotel_currency": row.get("hotel_currency"), "hotel_total": row.get("hotel_total"),
        "charge_currency": row.get("charge_currency"), "charge_amount": row.get("charge_amount"),
        "conversion_rate": row.get("conversion_rate"),
        "locator_code": row.get("locator_code"), "confirmation_number": row.get("confirmation_number"),
        "guest_name": row.get("guest_name"),
    }


@router.post("/api/hotels/checkout")
def hotel_checkout_start(request: HotelCheckoutRequest, customer_id: Optional[int] = Depends(auth.get_optional_customer_id)):
    """Start paying for a hotel room: the rate is re-priced live (Rules), the
    amount set here in LKR at Travelport's rate, and the bank page returned."""
    m = _main()
    m._require_card_enabled()
    quote = hotel_checkout.get_quote(request.offer_id)
    if not quote:
        raise HTTPException(status_code=409, detail="This room's price has expired. Please open the hotel again to "
                                                    "get the latest rates - nothing has been charged.")
    ctx = quote.get("context") or {}
    try:
        rules = hotel_rules_service.parse_rules(hotel_rules_service.get_rules_for_offer(request.offer_id))
    except HotelApiError as e:
        logger.error(f"Hotel checkout: Rules failed for {request.offer_id[:40]}: {e}")
        raise HTTPException(status_code=502, detail="The hotel could not confirm this room's price just now. "
                                                    "Nothing has been charged - please try again or choose another room.")
    currency = (quote.get("currency") or "").upper()
    hotel_total, base, taxes = float(quote.get("total_price") or 0), quote.get("base_price"), \
        (float(quote.get("total_taxes") or 0) + float(quote.get("total_fees") or 0))
    if rules.get("total_price") and (rules.get("currency") or currency).upper() == currency:
        hotel_total = float(rules["total_price"])
        base = rules.get("base_price") if rules.get("base_price") is not None else base
        taxes = float(rules.get("total_taxes") or 0)
    if hotel_total <= 0:
        raise HTTPException(status_code=409, detail="This room has no price from the hotel. Please choose another room.")
    charge = hotel_checkout.charge_for(hotel_total, quote)
    if not charge:
        raise HTTPException(status_code=409, detail="This room can't be paid online in LKR (no exchange rate from "
                                                    "Travelport). Nothing has been charged - please choose another room.")
    amount, rate = charge
    ref = hotel_checkout.new_order_ref()
    g = request.guest
    order = hotel_checkout.create_order({
        "order_ref": ref, "offer_id": request.offer_id, "customer_id": customer_id,
        "guest_name": f"{g.first_name} {g.last_name}", "guest_email": g.email, "guest": g.model_dump(),
        "property_name": request.property_name or quote.get("property_name") or "",
        "city": request.city or "", "country_code": request.country_code or "",
        "chain_code": quote.get("chain_code") or ctx.get("chain_code"),
        "property_code": quote.get("property_code") or ctx.get("property_code"),
        "booking_code": quote.get("booking_code"), "authority": quote.get("authority"),
        "room_description": quote.get("room_description"),
        "check_in_date": ctx.get("check_in_date"), "check_out_date": ctx.get("check_out_date"),
        "rooms": ctx.get("rooms") or 1, "adults": ctx.get("adults") or 1,
        "hotel_currency": currency, "hotel_total": hotel_total, "base_price": base, "total_taxes": taxes,
        "charge_currency": hotel_checkout.CHARGE_CURRENCY, "charge_amount": amount, "conversion_rate": rate,
        "free_cancellation_until": rules.get("free_cancellation_until"),
    })
    site = _site_hotels_url(request.return_url)
    try:
        pay = m.services.init_payment(
            amount=amount, currency=hotel_checkout.CHARGE_CURRENCY,
            return_url=f"{site}?hotel_order={ref}",
            cancel_url=f"{site}?hotel_order={ref}&payment=cancelled",
            client_ref=ref,
            comment=f"Hotel {order['property_name']} {order['check_in_date']} to {order['check_out_date']}"[:120],
        )
    except Exception as e:
        hotel_checkout.update_order(ref, status="cancelled", error=f"payment start failed: {e}")
        logger.error(f"Hotel checkout {ref}: payment start failed: {e}")
        raise HTTPException(status_code=502, detail="The card payment page could not be opened. Nothing has been charged.")
    hotel_checkout.update_order(ref, reqid=pay.get("reqid"))
    logger.info(f"Hotel checkout {ref}: {currency} {hotel_total} -> LKR {amount} (rate {rate}), {order['property_name']}")
    return {"order_ref": ref, "payment_page_url": pay["payment_page_url"],
            "charge_currency": hotel_checkout.CHARGE_CURRENCY, "charge_amount": amount,
            "hotel_currency": currency, "hotel_total": hotel_total, "conversion_rate": rate}


@router.post("/api/hotels/checkout/{order_ref}/complete")
def hotel_checkout_complete(order_ref: str, body: HotelCheckoutComplete):
    """After the bank: the payment must be this order's (reference, amount,
    LKR) and unused; then the room is reserved with Travelport and saved."""
    m = _main()
    row = hotel_checkout.get_order(order_ref.upper())
    if not row:
        raise HTTPException(status_code=404, detail="Hotel order not found.")
    if row["status"] == "booked":
        if row.get("reqid") != body.reqid:
            raise HTTPException(status_code=409, detail="This payment does not belong to this hotel order.")
        return {"order": _public_order(row), "already_confirmed": True}
    if row["status"] == "book_failed":
        raise HTTPException(status_code=502, detail=_failed_message(row))
    if row["status"] not in ("awaiting_payment", "paid"):
        raise HTTPException(status_code=409, detail="This hotel order is closed. Nothing more has been charged.")

    if row["status"] == "awaiting_payment":
        m._verify_and_claim_payment(body.reqid, row["order_ref"], None, float(row["charge_amount"]),
                                    row["charge_currency"])
        hotel_checkout.update_order(row["order_ref"], status="paid", reqid=body.reqid)
    elif row.get("reqid") != body.reqid:
        raise HTTPException(status_code=409, detail="This payment does not belong to this hotel order.")

    o = row["order"]
    try:
        raw = hotel_booking_service.create_hotel_reservation(
            chain_code=o["chain_code"], property_code=o["property_code"], booking_code=o["booking_code"],
            check_in_date=o["check_in_date"], check_out_date=o["check_out_date"], rooms=int(o.get("rooms") or 1),
            guests=1,
            price={"currency": o["hotel_currency"], "base": o.get("base_price"),
                   "total_taxes": o.get("total_taxes"), "total_price": o["hotel_total"]},
            travelers=[o["guest"]], authority=o.get("authority"),
        )
        parsed = hotel_booking_service.parse_hotel_reservation(raw)
    except HotelApiError as e:
        hotel_checkout.update_order(row["order_ref"], status="book_failed", error=str(e))
        logger.critical(f"HOTEL PAID BUT NOT BOOKED: {row['order_ref']} reqid={body.reqid} "
                        f"LKR {row['charge_amount']} - {e}. Staff must rebook or refund.")
        row["status"] = "book_failed"
        raise HTTPException(status_code=502, detail=_failed_message(row))

    g = o["guest"]
    record = {
        **parsed,
        "customer_id": o.get("customer_id"),
        "guest_name": f"{g['first_name']} {g['last_name']}", "guest_email": g["email"], "guest_phone": g["phone"],
        "property_name": parsed.get("property_name") or o.get("property_name") or "",
        "chain_code": parsed.get("chain_code") or o["chain_code"],
        "property_code": parsed.get("property_code") or o["property_code"],
        "city": o.get("city") or "", "country_code": o.get("country_code") or "",
        "check_in_date": parsed.get("check_in_date") or o["check_in_date"],
        "check_out_date": parsed.get("check_out_date") or o["check_out_date"],
        "rooms": o.get("rooms") or 1,
        "room_description": parsed.get("room_description") or o.get("room_description") or "",
        "total_price": parsed.get("total_price") or o["hotel_total"],
        "currency": parsed.get("currency") or o["hotel_currency"],
        "payment_method": f"Card (NDB) LKR {row['charge_amount']:.2f} - {row['order_ref']}",
    }
    hotel_database.save_hotel_booking(record)
    hotel_checkout.update_order(row["order_ref"], status="booked", locator_code=record.get("locator_code"),
                                confirmation_number=record.get("confirmation_number"))
    logger.info(f"Hotel checkout {row['order_ref']}: paid and booked, PNR {record.get('locator_code')}")
    done = hotel_checkout.get_order(row["order_ref"])
    return {"order": _public_order(done), "already_confirmed": False}


@router.post("/api/hotels/checkout/{order_ref}/cancel")
def hotel_checkout_cancel(order_ref: str):
    """The customer came back from the bank page without paying."""
    row = hotel_checkout.get_order(order_ref.upper())
    if row and row["status"] == "awaiting_payment":
        hotel_checkout.update_order(row["order_ref"], status="cancelled")
    return {"cancelled": True}


@router.get("/api/admin/hotel-orders")
def hotel_orders(status: Optional[str] = None, _admin: dict = Depends(auth.get_current_admin)):
    """Staff: hotel orders. 'book_failed' = paid but the hotel refused: rebook or refund."""
    return {"orders": hotel_checkout.list_orders(status)}


def _failed_message(row: dict) -> str:
    return (f"Your payment of LKR {float(row['charge_amount']):,.2f} was received (reference {row['order_ref']}), "
            "but the hotel could not confirm the room. You don't need to do anything: our team will contact you "
            "to book another room or refund you in full.")

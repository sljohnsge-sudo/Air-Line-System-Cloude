"""
services/hotel_rate_sources.py
==============================
Which content sources (TVPT / BKNG / ...) have rooms in each hotel of a search
result (17/11/2026).

Travelport's Search by Location only labels hotels TVPT; Booking.com (BKNG)
rates appear only in each hotel's Availability answer. So the customer site
asks for the hotels the customer actually scrolls to, a few at a time:

  POST /api/hotels/rate-sources   {stay..., properties: [{chain_code, property_code}] (max 8)}
    -> {"results": {"CHAIN|PROP": {"sources": {"TVPT": 12, "BKNG": 9}, "rates": 21,
                                   "lowest": {"currency": "LKR", "total": 45210.5}}}}

Availability answers are kept for CACHE_TTL seconds, and /api/hotels/availability
uses the same cache, so opening a hotel that was just checked does not ask
Travelport again (fewer calls toward the look-to-book ratio). Read-only: no
rules, no booking.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import hotel_availability_service, hotel_checkout
from services.hotel_common import HotelApiError

logger = logging.getLogger(__name__)
router = APIRouter()

CACHE_TTL = 600          # seconds; offers stay bookable well beyond this
MAX_PER_REQUEST = 8
_cache: dict = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hotel-src")


def _key(chain_code, property_code, check_in_date, check_out_date, adults, children_ages, rooms, currency):
    return (chain_code.upper(), property_code.upper(), check_in_date, check_out_date, int(adults),
            tuple(children_ages or ()), int(rooms), (currency or "").upper())


def get_availability_cached(chain_code: str, property_code: str, check_in_date: str, check_out_date: str,
                            adults: int = 1, children_ages: Optional[list] = None, rooms: int = 1,
                            currency: Optional[str] = None) -> dict:
    """hotel_availability_service.get_availability, remembered for CACHE_TTL."""
    k = _key(chain_code, property_code, check_in_date, check_out_date, adults, children_ages, rooms, currency)
    now = time.time()
    with _lock:
        hit = _cache.get(k)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    raw = hotel_availability_service.get_availability(
        chain_code=chain_code, property_code=property_code, check_in_date=check_in_date,
        check_out_date=check_out_date, adults=adults, children_ages=children_ages, rooms=rooms, currency=currency)
    with _lock:
        _cache[k] = (time.time(), raw)
        if len(_cache) > 2000:  # drop expired entries now and then
            for old in [x for x, v in _cache.items() if time.time() - v[0] >= CACHE_TTL]:
                _cache.pop(old, None)
    return raw


class RateSourceProperty(BaseModel):
    chain_code: str = Field(..., min_length=2, max_length=3)
    property_code: str = Field(..., min_length=1, max_length=12)


class RateSourcesRequest(BaseModel):
    check_in_date: str = Field(..., description="YYYY-MM-DD")
    check_out_date: str = Field(..., description="YYYY-MM-DD")
    adults: int = Field(default=1, ge=1, le=9)
    children_ages: Optional[List[int]] = None
    rooms: int = Field(default=1, ge=1, le=9)
    currency: Optional[str] = Field(default=None, max_length=3)
    properties: List[RateSourceProperty] = Field(..., min_length=1, max_length=MAX_PER_REQUEST)


def _one(req: RateSourcesRequest, p: RateSourceProperty) -> dict:
    try:
        raw = get_availability_cached(p.chain_code, p.property_code, req.check_in_date, req.check_out_date,
                                      req.adults, req.children_ages, req.rooms, req.currency)
        offers = hotel_availability_service.parse_availability_offers(raw)
    except HotelApiError as e:
        return {"error": str(e)[:200], "sources": {}, "rates": 0}
    except Exception as e:  # one hotel failing must not hide the others
        logger.warning(f"rate sources {p.chain_code}{p.property_code}: {e}")
        return {"error": "Rates could not be checked", "sources": {}, "rates": 0}
    try:  # same as /api/hotels/availability: checkout prices from these
        hotel_checkout.save_quotes(offers, {
            "chain_code": p.chain_code, "property_code": p.property_code,
            "check_in_date": req.check_in_date, "check_out_date": req.check_out_date,
            "adults": req.adults, "children_ages": req.children_ages or [], "rooms": req.rooms,
        })
    except Exception as e:
        logger.error(f"Hotel rates not stored for checkout: {e}")
    sources: dict = {}
    lowest = None
    for o in offers:
        a = o.get("authority") or "?"
        sources[a] = sources.get(a, 0) + 1
        conv = o.get("converted") or {}
        total = conv.get("total") if conv.get("total") else o.get("total_price")
        cur = conv.get("currency") if conv.get("total") else o.get("currency")
        try:
            total = float(total)
        except (TypeError, ValueError):
            continue
        if total > 0 and (lowest is None or total < lowest["total"]):
            lowest = {"currency": cur, "total": round(total, 2)}
    return {"sources": sources, "rates": len(offers), "lowest": lowest}


@router.post("/api/hotels/rate-sources")
def hotel_rate_sources(request: RateSourcesRequest):
    if request.check_out_date <= request.check_in_date:
        raise HTTPException(status_code=422, detail="Check-out must be after check-in")
    props = request.properties[:MAX_PER_REQUEST]
    found = list(_pool.map(lambda p: _one(request, p), props))
    return {"results": {f"{p.chain_code}|{p.property_code}": r for p, r in zip(props, found)}}

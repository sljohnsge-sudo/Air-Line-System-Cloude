"""
services/hotel_common.py
=========================
Shared helpers for the Hotel (Stays) service modules — kept separate from
the Air services. Uses hotel_auth_service's own independent OAuth token
cache (not the Air side's auth_service) and builds Hotel-specific request
headers, which differ from the Air header set.
"""

import logging
from typing import Optional
from services.hotel_auth_service import get_hotel_access_token
from config.hotel_config import HotelConfig

logger = logging.getLogger(__name__)


def get_hotel_headers() -> dict:
    """Headers for every Travelport Hotel (Stays) v11 API call, matching each
    endpoint's documented Headers section exactly (TraceId,
    XAUTH_TRAVELPORT_ACCESSGROUP, TVP-PCC-Core, TVP-Correlation-Id,
    Authorization) — see e.g.
    https://developer.travelport.com/apis/stays/search-and-details/searchbylocation.
    """
    token = get_hotel_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "XAUTH_TRAVELPORT_ACCESSGROUP": HotelConfig.ACCESS_GROUP,
        "TVP-PCC-Core": f"{HotelConfig.PCC}_1G",
        "TraceId": HotelConfig.generate_trace_id(),
        "TVP-Correlation-Id": HotelConfig.generate_correlation_id(),
    }


def build_room_stay_candidates(adults: int, children_ages: Optional[list[int]], rooms: int) -> list[dict]:
    """One RoomStayCandidate per room — shared by Search by Location and
    Availability, both of which want per-room occupancy rather than a
    single top-level guest count. Adults are spread evenly across rooms
    (remainder to the first rooms); any children go in room 1.
    ageQualifyingCode "10"=adult, "8"=child (age required only for children).
    """
    base, extra = divmod(adults, rooms)
    candidates = []
    for i in range(rooms):
        room_adults = base + (1 if i < extra else 0)
        guest_count = []
        if room_adults > 0:
            guest_count.append({"@type": "GuestCount", "count": room_adults, "ageQualifyingCode": "10"})
        if i == 0 and children_ages:
            for age in children_ages:
                guest_count.append({"@type": "GuestCount", "count": 1, "age": age, "ageQualifyingCode": "8"})
        if not guest_count:
            guest_count.append({"@type": "GuestCount", "count": 1, "ageQualifyingCode": "10"})
        candidates.append({
            "@type": "RoomStayCandidate",
            "GuestCounts": {"@type": "GuestCounts", "GuestCount": guest_count},
        })
    return candidates


class HotelApiError(Exception):
    """Raised for any non-2xx response from a Hotel/Stays API call."""

    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

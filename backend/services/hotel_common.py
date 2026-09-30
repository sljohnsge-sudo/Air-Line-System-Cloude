"""
services/hotel_common.py
=========================
Shared helpers for the Hotel (Stays) service modules — kept separate from
the Air services. Reuses the existing OAuth token cache in auth_service
(read-only import; OAuth is account-wide, not product-scoped) but builds
Hotel-specific request headers, which differ from the Air header set.
"""

import logging
from services.auth_service import get_access_token
from config.hotel_config import HotelConfig

logger = logging.getLogger(__name__)


def get_hotel_headers(api_version: int = 11) -> dict:
    """Headers for Travelport Hotel (Stays) v11/v12 API calls, per
    https://developer.travelport.com/docs/stays/general/common-stays-api-headers
    — Accept-Encoding and Cache-Control are both listed there as Required and
    were previously missing from every Hotel/Stays call.

    Per https://developer.travelport.com/docs/stays/general/trace-and-transaction-ids
    the tracking header name is version-specific: v11 APIs use `TraceId`, but
    v12 APIs (SearchComplete and SearchComplete Pagination only) use
    `TVP-Trace-Id` instead. Pass api_version=12 for those calls; every other
    Hotel endpoint in this codebase is v11 and should use the default.
    """
    token = get_access_token()
    trace_header = "TVP-Trace-Id" if api_version == 12 else "TraceId"
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "XAUTH_TRAVELPORT_ACCESSGROUP": HotelConfig.ACCESS_GROUP,
        "TVP-PCC-Core": f"{HotelConfig.PCC}_1G",
        trace_header: HotelConfig.generate_trace_id(),
        "TVP-Correlation-Id": HotelConfig.generate_correlation_id(),
    }


class HotelApiError(Exception):
    """Raised for any non-2xx response from a Hotel/Stays API call."""

    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

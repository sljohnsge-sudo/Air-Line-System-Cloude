"""
seat_map_service.py
=====================
Air_RetrieveSeatMap -- seat selection for an Amadeus offer, feeding the SAME
seat-grid UI/state (seatMap.columns / seatMap.rows[].seats[]) already built
for Travelport in frontend/src/App.jsx (renderSeat() etc).

Context-less (stateless) call, like Fare_MasterPricerTravelBoardSearch: this
is a pre-booking "what seats exist on this flight" lookup, not a seat
assignment against an already-sold PNR (that needs resControlInfo, which this
project doesn't use -- not needed before a PNR exists). travelProductIdent
is TravelProductInformationTypeI, the exact same type
_segment_product_xml() already builds for Air_SellFromRecommendation and
Fare_InformativePricingWithoutPNR (confirmed by name in the XSD), so it's
reused here rather than duplicated.
"""

from services.amadeus_soap_client import call
from services.flight_booking_service import _segment_product_xml

NS = "http://xml.amadeus.com/SMPREQ_17_1_1A"


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _find_local(parent, name):
    if parent is None:
        return None
    for c in parent:
        if _local(c.tag) == name:
            return c
    return None


def _find_all_local(parent, name):
    return [c for c in parent if _local(c.tag) == name] if parent is not None else []


def _text(parent, name, default=None):
    el = _find_local(parent, name)
    return el.text if el is not None and el.text else default


async def get_seat_map(segment: dict) -> dict:
    body = f"""<Air_RetrieveSeatMap xmlns="{NS}">
      <travelProductIdent>
        {_segment_product_xml(segment)}
      </travelProductIdent>
    </Air_RetrieveSeatMap>"""

    body_root = await call(
        operation="Air_RetrieveSeatMap",
        soap_action="http://webservices.amadeus.com/SMPREQ_17_1_1A",
        body_xml=body,
        log_prefix="seatmap",
    )

    reply = _find_local(body_root, "Air_RetrieveSeatMapReply")
    if reply is None:
        return {"available": False, "columns": [], "rows": []}

    error_info = _find_local(reply, "errorInformation")
    if error_info is not None:
        return {"available": False, "columns": [], "rows": []}

    seatmap_info = _find_local(reply, "seatmapInformation")
    if seatmap_info is None:
        return {"available": False, "columns": [], "rows": []}

    columns_seen: list[str] = []
    rows_out = []
    for row in _find_all_local(seatmap_info, "row"):
        row_details = _find_local(row, "rowDetails")
        row_number = _text(row_details, "seatRowNumber")
        if row_number is None:
            continue

        seats = []
        for occ in _find_all_local(row_details, "seatOccupationDetails"):
            col = _text(occ, "seatColumn")
            if not col:
                continue
            if col not in columns_seen:
                columns_seen.append(col)
            occupied = bool(_text(occ, "seatOccupation"))
            seats.append({
                "seat_number": f"{row_number}{col}",
                "status": "occupied" if occupied else "available",
                "type": "Standard",
                "price": 0,
                "currency": None,
            })

        seats.sort(key=lambda s: s["seat_number"][len(row_number):])
        rows_out.append({"row_number": int(row_number), "seats": seats})

    columns_seen.sort()
    return {
        "available": bool(rows_out),
        "columns": [{"value": c} for c in columns_seen],
        "rows": rows_out,
    }

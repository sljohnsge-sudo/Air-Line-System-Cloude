"""
services/special_services.py
=============================
Special service requests (SSRs) a passenger can ask for while booking:
a special meal and wheelchair assistance. Code tables per Travelport's
Remarks & Service Requests Guide, plus merging what was requested at booking
with the status the airline returns on the PNR.

Sending them is workbench_service.add_special_services(); reading them back
from a reservation is ticket_service._parse_special_services().
"""

# IATA meal SSR code -> SpecialMealTypeEnum (the guide's mapping table).
MEAL_SSR_TYPES = {
    "BBML": "Baby", "CHML": "Child", "DBML": "Diabetic", "FPML": "FruitPlatter",
    "HNML": "Hindu", "VJML": "Jain", "KSML": "Kosher", "LCML": "LowCalorie",
    "LFML": "LowFat", "LSML": "LowSalt", "MOML": "Muslim", "NLML": "NonLactos",
    "NOML": "None", "SFML": "Seafood", "VGML": "Vegan", "AVML": "VegetarianHindu",
    "VLML": "VegetarianLactoOvo", "VOML": "VegetarianOriental", "RVML": "VegetarianRaw",
}

# Wheelchair SSR code -> SpecialServiceWheelchairAirlineSupplied flags, per
# Travelport's "SSR Disability Request Payload Options" (AirReservation 20.9.1).
WHEELCHAIR_SSR_FLAGS = {
    "WCHR": {},
    "WCHS": {"cannotAscendStairsInd": True},
    "WCHC": {"cannotAscendStairsInd": True, "travelerImmobileInd": True},
}

# Meal SSRs on NDC content: only these carriers (guide, Meals section).
# Wheelchair (disability) SSRs are supported for GDS and NDC.
NDC_MEAL_SSR_CARRIERS = {"AA", "QF", "UA"}


def ssr_code_of(item: dict) -> str | None:
    """The IATA code of one Reservation.SpecialService entry, whether
    Travelport returned it by code (SpecialService + SSRCode) or typed."""
    if item.get("SSRCode"):
        return item["SSRCode"]
    kind = item.get("@type")
    if kind == "SpecialServiceMeal":
        return next((c for c, t in MEAL_SSR_TYPES.items() if t == item.get("SpecialMealTypeEnum")), None)
    if kind == "SpecialServiceWheelchairAirlineSupplied":
        if item.get("travelerImmobileInd"):
            return "WCHC"
        return "WCHS" if item.get("cannotAscendStairsInd") else "WCHR"
    return None


def ssr_kind(code: str | None) -> str | None:
    if code in MEAL_SSR_TYPES:
        return "meal"
    if code in WHEELCHAIR_SSR_FLAGS:
        return "wheelchair"
    return None


def _name_tokens(name: str | None) -> set:
    return {p for p in (name or "").upper().split() if p not in ("MR", "MRS", "MS", "MSTR", "MISS", "DR")}


def merge_ssr_status(requests: list | None, live: list | None) -> list:
    """One row per meal/wheelchair request: the airline's status from the PNR
    when it carries that SSR ("confirmed", "pending", ...), otherwise what
    happened when it was sent ("requested" = Travelport accepted it but the
    PNR shows no airline reply — confirmed live for QR and SQ NDC, which
    return no SSRs at all; "failed" / "not_supported" with the reason).
    SSRs on the PNR that weren't requested here (added by staff later) are
    listed too."""
    rows, used = [], set()
    for req in requests or []:
        row = {"passenger_name": req.get("passenger_name"), "kind": req.get("kind"),
               "code": req.get("code"), "status": req.get("status"), "error": req.get("error")}
        if req.get("status") == "sent":
            row["status"] = "requested"
            want = _name_tokens(req.get("passenger_name"))
            for i, s in enumerate(live or []):
                if i not in used and s.get("code") == req.get("code") and want <= _name_tokens(s.get("passenger_name")):
                    used.add(i)
                    row["status"] = s.get("status") or "requested"
                    row["passenger_name"] = s.get("passenger_name") or row["passenger_name"]
                    break
        rows.append(row)
    for i, s in enumerate(live or []):
        if i not in used:
            rows.append({"passenger_name": s.get("passenger_name"), "kind": s.get("kind"),
                         "code": s.get("code"), "status": s.get("status"), "error": None})
    return rows

"""
services/fare_rule_service.py
==============================
Standalone fare rules — the change/cancel conditions of a fare, from
Travelport's Fare Rules API (Fare Rules Guide):

    GET /air/farerule/farerules/fromcatalogproductofferings   after search
    GET /air/farerule/farerules/fromreservation               on a booking

Two rule types are requested for each fare: ShortText (one line per ATPCO
category, GDS and NDC) and Structured (penalty amounts). NDC supports
structured Penalties only and no long text, so NDC asks for
fareRuleCategories=Penalties. Nothing here is defaulted or invented — what
Travelport doesn't return is simply not shown.

Confirmed live 2026-10-29 (pre-prod): GDS AI2275 CMB-DXB and NDC EK655
CMB-DXB both return short text and structured penalties after search.
"""

import logging
import httpx
from config.travelport_config import TravelportConfig
from config.api_endpoints import TravelportEndpoints
from services.auth_service import get_auth_headers, invalidate_token
from utils import tp_logger

logger = logging.getLogger(__name__)

_WHEN = {
    "BeforeDeparture": "before departure",
    "AfterDeparture": "after departure",
    "NoShow": "no-show",
    "Anytime": "any time",
}


def _api_get(url: str, params: list) -> dict:
    with httpx.Client(timeout=TravelportConfig.REQUEST_TIMEOUT, event_hooks=tp_logger.HOOKS) as client:
        response = client.get(url, params=params, headers=get_auth_headers(with_body=False))
        if response.status_code == 401:
            invalidate_token()
            response = client.get(url, params=params, headers=get_auth_headers(with_body=False))
        response.raise_for_status()
        return response.json()


def _fetch(url: str, base_params: list, is_gds: bool) -> dict:
    """ShortText + Structured for one fare, parsed. Each type fails on its
    own — a missing structured answer still leaves the short text."""
    out = {"summary": [], "penalties": [], "flights": [], "notes": []}
    for rule_type in ("ShortText", "Structured"):
        params = base_params + [("fareRuleType", rule_type)]
        if rule_type == "Structured" and not is_gds:
            params.append(("fareRuleCategories", "Penalties"))
        try:
            body = _api_get(url, params).get("FareRuleListResponse", {})
        except httpx.HTTPError as e:
            logger.warning(f"Fare rules ({rule_type}) failed: {e}")
            out["notes"].append(f"{rule_type} rules unavailable from Travelport.")
            continue
        result = body.get("Result") or {}
        for err in result.get("Error") or []:
            out["notes"].append(err.get("Message"))
        for warn in result.get("Warning") or []:
            out["notes"].append(warn.get("Message"))
        if not out["flights"]:
            out["flights"] = _flights(body)
        for rule in body.get("FareRule") or []:
            if rule.get("@type") == "FareRuleText":
                out["summary"] += [
                    {"name": t.get("name"), "value": t.get("value").strip(" |")}
                    for t in rule.get("TextFareRule") or [] if (t.get("value") or "").strip(" |")
                ]
            elif rule.get("@type") == "FareRuleStructured":
                out["penalties"] += _penalties(rule)
    out["penalties"] = _dedupe_penalties(out["penalties"])
    out["notes"] = [n for n in dict.fromkeys(out["notes"]) if n]
    return out


def _flights(body: dict) -> list:
    flights = []
    for ref in body.get("ReferenceList") or []:
        for f in ref.get("Flight") or []:
            flights.append(f"{f.get('carrier', '')}{f.get('number', '')} "
                           f"{(f.get('Departure') or {}).get('location', '')}-{(f.get('Arrival') or {}).get('location', '')}")
    return flights


def _penalties(rule: dict) -> list:
    """One raw row per Change/Cancel item in the structured rules."""
    rows = []
    for group in rule.get("StructuredFareRules") or []:
        group_ptc = group.get("passengerTypeCodes") or []
        for pen in group.get("Penalties") or []:
            ptc = pen.get("PassengerTypeCodes") or group_ptc
            for kind in ("Change", "Cancel"):
                for item in pen.get(kind) or []:
                    amount, percent = None, None
                    for p in item.get("Penalty") or []:
                        if p.get("Amount"):
                            amount = (p["Amount"].get("value"), p["Amount"].get("code"))
                        elif p.get("percent") is not None:
                            percent = p["percent"]
                    before = item.get("PeriodBeforeDeparture") or {}
                    rows.append({
                        "kind": kind,
                        "permitted": not (item.get("@type") or "").endswith("NotPermitted"),
                        "when": ", ".join(_WHEN[t] for t in item.get("penaltyTypes") or [] if t in _WHEN) or "any time",
                        "within": f"within {before['value']} {before.get('period', '').lower()} of departure" if before.get("value") else None,
                        "amount": amount,
                        "percent": percent,
                        "passenger_types": tuple(ptc),
                    })
    return rows


def _dedupe_penalties(rows: list) -> list:
    """GDS fares repeat each penalty across ATPCO categories (16 general, 31
    changes, 33 refunds), each with several conditional sequences — live
    AI2275 returned change fees of USD 44 and USD 132 plus a "not permitted"
    row for the same fare. Grouped by change/cancel, timing and passenger
    type: the fees Travelport gives (lowest to highest), or "not allowed"
    when every row says so, or allowed with no fee stated."""
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["kind"], r["when"], r["within"], r["passenger_types"]), []).append(r)
    out = []
    for (kind, when, within, ptc), items in groups.items():
        amounts = sorted({r["amount"] for r in items if r["amount"]}, key=lambda a: (a[1] or "", float(a[0] or 0)))
        percents = sorted({r["percent"] for r in items if r["percent"] is not None})
        if amounts or percents:
            permitted = True
        elif all(not r["permitted"] for r in items):
            permitted = False
        elif all(r["permitted"] for r in items):
            permitted = True
        else:
            permitted = None  # Travelport's own rows disagree — see the short text
        out.append({
            "kind": kind, "when": when, "within": within, "passenger_types": list(ptc), "permitted": permitted,
            "amounts": [{"amount": a, "currency": c} for a, c in amounts], "percents": percents,
        })
    return out


def _leg_offerings(raw_offering: dict) -> list:
    if "outbound" in raw_offering and "inbound" in raw_offering:
        return [raw_offering["outbound"], raw_offering["inbound"]]
    if "legs" in raw_offering:
        return raw_offering["legs"]
    return [raw_offering]


def get_fare_rules_for_offering(raw_offering: dict) -> list:
    """Fare rules for each leg of a searched offer (one-way, round-trip
    {"outbound","inbound"} or multi-city {"legs"}). After search Travelport
    takes ONE product per request, so each leg's product is asked for on
    its own. Returns one entry per leg/product."""
    fares = []
    for leg in _leg_offerings(raw_offering):
        is_gds = leg.get("fare_source") == "GDS"
        products = []
        for pbo in leg.get("ProductBrandOptions") or []:
            for bo in (pbo.get("ProductBrandOffering") or [])[:1]:
                products += [p.get("productRef") for p in bo.get("Product") or [] if p.get("productRef")]
        for product in dict.fromkeys(products):
            params = [
                ("catalogProductOfferingsIdentifier", leg.get("CatalogProductOfferingsIdentifier", "")),
                ("catalogProductOfferingID", leg.get("id", "")),
                ("productIDs", product),
            ]
            fare = _fetch(TravelportEndpoints.FARE_RULES_FROM_SEARCH, params, is_gds)
            fare.update(fare_source=leg.get("fare_source"), brand_name=leg.get("brand_name"))
            fares.append(fare)
    return fares


def get_fare_rules_for_reservation(locator_code: str, is_gds: bool) -> list:
    """Fare rules of an existing booking (made through TripServices, with a
    valid fare — Fare Rules Guide)."""
    fare = _fetch(TravelportEndpoints.FARE_RULES_FROM_RESERVATION, [("reservationIdentifier", locator_code)], is_gds)
    fare["fare_source"] = "GDS" if is_gds else "NDC"
    return [fare]

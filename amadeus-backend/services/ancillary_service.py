"""
ancillary_service.py
=====================
Service_StandaloneCatalogue -- stateless ancillary catalogue (extra baggage,
meal preferences, etc.) for an Amadeus offer. Context-less, like
Air_RetrieveSeatMap in seat_map_service.py: built straight from the search
result's segment dicts, no PNR/session required.

Confirmed live against our own office via Amadeus's own sample flow (Oman Air
DEL-MCT-DXB, 4_SSRAvailability/1_ServiceStandalone_RQ.xml / _RS.xml) -- this
module mirrors that exact request shape:
  - one passengerInfoGroup (ADT, referenceNumber 1)
  - one flightInfo per segment (itemNumber 1, 2, ...)
  - one FBA pricingOption per segment, referencing its own paxSegTstReference
  - one GRP pricingOption explicitly asking for BG (baggage) and ML (meal)
    categories, plus SCD/OIS/MIF with no further detail (all confirmed valid
    no-detail keys)

Response shape (confirmed live): repeated <serviceGroup> elements, each one
ancillary item --
  serviceAttributes/criteriaDetails[attributeType=CNM] -> commercial name
  serviceDetailsGroup/serviceDetails/specialRequirementsInfo
    -> ssrCode, serviceType (BG/ML/...), serviceFreeText
  serviceDetailsGroup/fsfkwDataGroup/fsfkwValues/criteriaDetails
    -> RFIC / RFISC (IATA revenue-forecasting codes)
  pricingGroup/couponInfoGroup/monetaryInfo/otherMonetaryDetails[typeQualifier=MA]
    -> price in USD (falls back to monetaryDetails[typeQualifier=ME], the
    local-currency amount, if MA isn't present)
"""

from services.amadeus_soap_client import call
from services.flight_booking_service import _segment_product_xml

NS = "http://xml.amadeus.com/TPSCGQ_16_1_1A"


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


def _criteria_value(parent, group_name, attribute_type):
    """Find serviceAttributes/criteriaDetails (or fsfkwValues/criteriaDetails)
    whose attributeType matches, return its attributeDescription."""
    for group in _find_all_local(parent, group_name):
        for crit in _find_all_local(group, "criteriaDetails"):
            if _text(crit, "attributeType") == attribute_type:
                return _text(crit, "attributeDescription")
    return None


async def get_ancillary_offers(segments: list[dict]) -> list[dict]:
    """segments: the same per-leg dicts already used for the seat map call
    (departureDate/departureTime/arrivalDate/arrivalTime/from/to/
    marketingCarrier/flightNumber/bookingClass, native Amadeus string
    formats) -- one or two entries (outbound, and return/next-leg if any).

    Confirmed live: asking for BG and ML together in one GRP pricingOption's
    optionDetail (a single <criteriaDetails> list) does NOT return both --
    Amadeus appears to cap the number of serviceGroup items per call (~19-20
    seen live) and the two categories compete for that shared cap, so one
    starves the other (order-dependent: whichever criteriaDetails is listed
    first gets most/all of the budget). Two separate <pricingOption><GRP>...
    blocks in the SAME request fares even worse -- confirmed live to return
    ZERO items, not the union. The only combination confirmed to reliably
    return BOTH categories complete is two separate Service_StandaloneCatalogue
    calls (one GRP/BG-only, one GRP/ML-only), merged here in Python.
    """
    bg_items = await _fetch_ancillary_category(segments, "BG")
    ml_items = await _fetch_ancillary_category(segments, "ML")
    return bg_items + ml_items


async def _fetch_ancillary_category(segments: list[dict], category: str) -> list[dict]:
    """One Service_StandaloneCatalogue call for a single GRP category (BG or
    ML) -- see get_ancillary_offers()'s docstring for why this can't be a
    single combined call."""
    flight_info_xml = ""
    pricing_fba_xml = ""
    for i, seg in enumerate(segments, start=1):
        flight_info_xml += f"""<flightInfo>
          <flightDetails>
            {_segment_product_xml(seg)}
            <itemNumber>{i}</itemNumber>
          </flightDetails>
        </flightInfo>"""
        pricing_fba_xml += f"""<pricingOption>
          <pricingOptionKey><pricingOptionKey>FBA</pricingOptionKey></pricingOptionKey>
          <optionDetail><criteriaDetails><attributeType>R1CFOIA</attributeType></criteriaDetails></optionDetail>
          <paxSegTstReference><referenceDetails><type>S</type><value>{i}</value></referenceDetails></paxSegTstReference>
        </pricingOption>"""

    body = f"""<Service_StandaloneCatalogue xmlns="{NS}">
      <passengerInfoGroup>
        <specificTravellerDetails><travellerDetails><referenceNumber>1</referenceNumber></travellerDetails></specificTravellerDetails>
        <fareInfo><valueQualifier>ADT</valueQualifier></fareInfo>
      </passengerInfoGroup>
      {flight_info_xml}
      {pricing_fba_xml}
      <pricingOption>
        <pricingOptionKey><pricingOptionKey>GRP</pricingOptionKey></pricingOptionKey>
        <optionDetail>
          <criteriaDetails><attributeType>{category}</attributeType></criteriaDetails>
        </optionDetail>
      </pricingOption>
      <pricingOption><pricingOptionKey><pricingOptionKey>SCD</pricingOptionKey></pricingOptionKey></pricingOption>
      <pricingOption><pricingOptionKey><pricingOptionKey>OIS</pricingOptionKey></pricingOptionKey></pricingOption>
      <pricingOption><pricingOptionKey><pricingOptionKey>MIF</pricingOptionKey></pricingOptionKey></pricingOption>
    </Service_StandaloneCatalogue>"""

    body_root = await call(
        operation="Service_StandaloneCatalogue",
        soap_action="http://webservices.amadeus.com/TPSCGQ_16_1_1A",
        body_xml=body,
        log_prefix=f"ancillary_{category.lower()}",
    )

    reply = _find_local(body_root, "Service_StandaloneCatalogueReply")
    if reply is None:
        return []

    error_info = _find_local(reply, "errorInformation") or _find_local(reply, "applicationError")
    if error_info is not None:
        return []

    items = []
    for i, group in enumerate(_find_all_local(reply, "serviceGroup"), start=1):
        name = _criteria_value(group, "serviceAttributes", "CNM")
        # Confirmed live: Amadeus pads the commercial name with a leading
        # space ("EXCESS BAGGAGE..." comes back as " EXCESS BAGGAGE...").
        if name:
            name = name.strip()

        detail_group = _find_local(group, "serviceDetailsGroup")
        service_details = _find_local(detail_group, "serviceDetails")
        ssr_info = _find_local(service_details, "specialRequirementsInfo")
        ssr_code = _text(ssr_info, "ssrCode")
        service_type = _text(ssr_info, "serviceType")
        free_text = _text(ssr_info, "serviceFreeText")

        rfic = _criteria_value(detail_group, "fsfkwDataGroup", "RFIC") if detail_group is not None else None
        rfisc = _criteria_value(detail_group, "fsfkwDataGroup", "RFISC") if detail_group is not None else None
        # fsfkwDataGroup/fsfkwValues nests one level deeper than
        # serviceAttributes does -- _criteria_value already looks inside each
        # <fsfkwDataGroup> for a <criteriaDetails> child, but the real
        # criteriaDetails sits inside <fsfkwValues> one level further in, so
        # fall back to scanning that explicitly if the shortcut found nothing.
        if detail_group is not None and (rfic is None or rfisc is None):
            for fkw_group in _find_all_local(detail_group, "fsfkwDataGroup"):
                for values in _find_all_local(fkw_group, "fsfkwValues"):
                    for crit in _find_all_local(values, "criteriaDetails"):
                        atype = _text(crit, "attributeType")
                        if atype == "RFIC" and rfic is None:
                            rfic = _text(crit, "attributeDescription")
                        elif atype == "RFISC" and rfisc is None:
                            rfisc = _text(crit, "attributeDescription")

        price, currency = None, None
        for pricing_group in _find_all_local(group, "pricingGroup"):
            for coupon_info in _find_all_local(pricing_group, "couponInfoGroup"):
                for monetary_info in _find_all_local(coupon_info, "monetaryInfo"):
                    for other in _find_all_local(monetary_info, "otherMonetaryDetails"):
                        if _text(other, "typeQualifier") == "MA":
                            price = _text(other, "amount")
                            currency = _text(other, "currency")
                    if price is None:
                        main = _find_local(monetary_info, "monetaryDetails")
                        if main is not None:
                            price = _text(main, "amount")
                            currency = _text(main, "currency")
            if price is not None:
                break

        if not name:
            continue
        price_value = float(price) if price else 0.0
        if price_value <= 0:
            # Confirmed live: Amadeus's GRP/BG catalogue includes items like
            # "FREE BAGGAGE ALLOWANCE" and "CARRYON HAND BAGGAGE ALLOWANCE"
            # that describe what's already included in the fare, priced at
            # 0 -- informational, not something to purchase. Skip them so
            # the Extras picker only lists real paid add-ons.
            continue
        # Confirmed live: serviceId/itemNumberDetails/number is empty for
        # this office's responses (unlike Amadeus's own WY sample), and
        # multiple distinct items can share the same ssr_code (e.g. two
        # different "ASVC" baggage tiers) -- an id built from just ssr_code
        # collides and makes the frontend's select/deselect pick both at
        # once. Append the loop index so every item gets a unique id.
        catalog_offering_id = f"{ssr_code or 'SVC'}_{i}"
        items.append({
            "catalog_offering_id": catalog_offering_id,
            "product_id": catalog_offering_id,
            "service_type": service_type,
            "category_code": service_type,
            "ssr_code": ssr_code,
            "rfic": rfic,
            "rfisc": rfisc,
            "name": name,
            # serviceFreeText (e.g. "05") is a raw internal code, not a
            # labelled quantity/unit like Travelport's Measurement block --
            # appending it raw ("EXCESS BAGGAGE PER 5KG (05)") looked like a
            # typo, not a weight, so it's kept on the item for callers that
            # want it but left out of the customer-facing description.
            "service_free_text": free_text,
            "description": name,
            "price": float(price) if price else 0.0,
            "currency": currency or "USD",
        })

    return items

"""
fare_rules_service.py
======================
Change/cancellation policy text for an Amadeus offer, shown on the search
results card the same way Travelport's own (real) change_policy/cancel_policy
fields already are (frontend/src/App.jsx).

Fare_InformativePricingWithoutPNR -- the "quote without a PNR" operation that
would normally back this -- is still blocked by errorCode 146 "CHECK - CALL
SUPERVISOR" for this office (see logs/amadeus/*fare_informative_pricing_RS.xml
and the Fare_InformativePricingWithoutPNR_errorCode146_logs package already
sent to Amadeus). Fare_GetFareRules was considered too, but its request needs
a tariffClassId/ruleSectionId this project has no live source for.

Fare_PricePNRWithBookingClass (already proven working in the real booking
flow) returns real rule text for free: a sell + price with NO travelers and
NO PNR_AddMultiElements/TST/End Transact ever leaves the workspace
uncommitted, so nothing is saved on Amadeus's side when the session closes --
the exact same "shop without committing" behavior Informative Pricing was
meant to provide. Confirmed live: otherPricingInfo/attributeDetails with
attributeType "END" carries real restriction/change-fee free text (e.g.
"VALID ON UL ONLY CHANGE FEE MAY APPLY", "NON-END NO RFND WITHIN 24HRS CTC TA
FOR CHNGS"). Amadeus doesn't split change vs. cancel into two separate
fields here (unlike Travelport) -- the same END text is used for both.
"""

from services.amadeus_soap_client import AmadeusSession, call
from services.flight_booking_service import sell_segments, NS as BOOKING_NS


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


async def get_fare_rules(segments: list[dict], total_pax: int = 1) -> dict:
    session = AmadeusSession()

    # sell_segments() (not a raw call()) so a genuinely unavailable segment
    # (errorCode 288 UNS -- real live inventory depletion, confirmed seen on
    # this exact route earlier this session) raises a clear error here
    # instead of silently falling through to a confusing, unrelated
    # "PNR NOT PRESENT" error at the pricing step below.
    await sell_segments(session, segments, total_pax)

    price_body = f"""<Fare_PricePNRWithBookingClass xmlns="{BOOKING_NS['price']}">
      <pricingOptionGroup>
        <pricingOptionKey><pricingOptionKey>RP</pricingOptionKey></pricingOptionKey>
      </pricingOptionGroup>
    </Fare_PricePNRWithBookingClass>"""

    body_root = await call(
        operation="Fare_PricePNRWithBookingClass",
        soap_action="http://webservices.amadeus.com/TPCBRQ_24_3_1A",
        body_xml=price_body,
        log_prefix="fr2_price",
        session=session,
        stateful=True,
        end_session=True,
    )

    reply = _find_local(body_root, "Fare_PricePNRWithBookingClassReply")
    fare_lists = _find_all_local(reply, "fareList") if reply is not None else []

    rule_texts: list[str] = []
    for fare_list in fare_lists:
        other_info = _find_local(fare_list, "otherPricingInfo")
        for attr in _find_all_local(other_info, "attributeDetails"):
            if _text(attr, "attributeType") == "END":
                desc = _text(attr, "attributeDescription")
                if desc and desc not in rule_texts:
                    rule_texts.append(desc)

    policy_text = " / ".join(rule_texts) if rule_texts else None
    return {"change_policy": policy_text, "cancel_policy": policy_text}

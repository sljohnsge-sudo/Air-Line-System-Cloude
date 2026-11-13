"""
fare_rules_service.py
======================
Change/cancellation rules for an Amadeus offer, shown on the search results
card the same way Travelport's own change_policy/cancel_policy fields are
(frontend/src/App.jsx fetchAmadeusFareRules).

Rebuilt 12/11/2026 on Amadeus's own sample flow (Booking-Flow_Ancillaries
steps 2-3): Fare_InformativePricingWithoutPNR (the quote, no PNR, nothing
held) followed in the SAME session by Fare_CheckRules for the airline's real
rule text -- penalties (PE: change and cancellation fees), rule application
(RU), sales restrictions (SR) and maximum stay (MX).

Until now the quote was blocked by errorCode 146 "CHECK - CALL SUPERVISOR",
which turned out to be our request shape, not the test system (see
flight_booking_service._informative_pricing_body), so this used a
sell + price workaround that only gave the short endorsement line ("VLD ON
UL ONLY/CHANGE FEE MAY APPLY"). That workaround stays as the fallback when
the quote or the rules call fails.
"""

import re

from services.amadeus_soap_client import AmadeusSession, AmadeusSoapError, call
from services.flight_booking_service import sell_segments, informative_pricing, NS as BOOKING_NS

CHECK_RULES_NS = "http://xml.amadeus.com/FARQNQ_07_1_1A"
RULE_SECTIONS = ("RU", "PE", "SR", "MX")


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


async def _sign_out(session: AmadeusSession) -> None:
    """Closes a stateful session that's still open after a failed step."""
    if not session.is_open:
        return
    try:
        await call(operation="Security_SignOut", soap_action="http://webservices.amadeus.com/VLSSOQ_04_1_1A",
                   body_xml='<Security_SignOut xmlns="http://xml.amadeus.com/VLSSOQ_04_1_1A"/>',
                   log_prefix="fr_signout", session=session, stateful=True, end_session=True)
    except AmadeusSoapError:
        pass


async def _check_rules(session: AmadeusSession, fare_component: str, end_session: bool) -> dict:
    """Fare_CheckRules for one fare component of the quote in context.
    Returns {"PE": [lines], "RU": [...], ...} keyed by rule section."""
    sections = "".join(f"<ruleSectionId>{s}</ruleSectionId>" for s in RULE_SECTIONS)
    body = f"""<Fare_CheckRules xmlns="{CHECK_RULES_NS}">
      <msgType><messageFunctionDetails><messageFunction>712</messageFunction></messageFunctionDetails></msgType>
      <itemNumber>
        <itemNumberDetails><number>1</number></itemNumberDetails>
        <itemNumberDetails><number>{fare_component}</number><type>FC</type></itemNumberDetails>
      </itemNumber>
      <fareRule><tarifFareRule>{sections}</tarifFareRule></fareRule>
    </Fare_CheckRules>"""
    root = await call(operation="Fare_CheckRules", soap_action="http://webservices.amadeus.com/FARQNQ_07_1_1A",
                      body_xml=body, log_prefix=f"fr_check_rules_fc{fare_component}",
                      session=session, stateful=True, end_session=end_session)
    reply = _find_local(root, "Fare_CheckRulesReply")
    out: dict[str, list[str]] = {}
    for tariff in _find_all_local(reply, "tariffInfo"):
        lines = []
        key = None
        for frt in _find_all_local(tariff, "fareRuleText"):
            q = _find_local(frt, "freeTextQualification")
            txt = (_text(frt, "freeText") or "").rstrip()
            if q is not None and _text(q, "informationType") == "CAT":
                key = txt.split(".", 1)[0].strip() or key   # "PE.PENALTIES" -> "PE"
                continue
            lines.append(txt)
        if key:
            out.setdefault(key, []).extend(lines)
    return out


_SKIP = re.compile(r"^\s*(-{5,}|NOTE\s*-?)\s*$")


def _section(lines: list[str], start: str, stop: tuple[str, ...]) -> list[str]:
    """Lines between a heading like 'CHANGES' and the next heading in `stop`."""
    out, inside = [], False
    for ln in lines:
        head = ln.strip().upper()
        if head == start:
            inside = True
            continue
        if inside and head in stop:
            break
        if inside and ln.strip() and not _SKIP.match(ln):
            out.append(" ".join(ln.split()))
    return out




async def _rules_via_check_rules(segments: list[dict], travelers: list[dict]) -> dict | None:
    session = AmadeusSession()
    try:
        quote = await informative_pricing(session, segments, travelers, end_session=False)
    except AmadeusSoapError:
        await _sign_out(session)
        return None
    if not quote.get("ok"):
        await _sign_out(session)
        return None
    fcs = quote["groups"][0].get("fare_components") if quote.get("groups") else None
    if not fcs:
        await _sign_out(session)
        return None
    # 13/11/2026 audit: the rules of each fare component are kept apart (a
    # connection or round trip can have different rules per fare), in full,
    # and a policy is only what the airline's CHANGES / CANCELLATIONS section
    # says - never the whole text standing in for it.
    components = []
    try:
        for i, fc in enumerate(fcs):
            part = await _check_rules(session, fc, end_session=(i == len(fcs) - 1))
            pe = part.get("PE") or []
            heads = ("CHANGES", "CANCELLATIONS", "CANCELLATION", "REFUND", "REFUNDS")
            changes = _section(pe, "CHANGES", heads[1:])
            cancels = _section(pe, "CANCELLATIONS", ("CHANGES",)) or _section(pe, "CANCELLATION", ("CHANGES",))
            components.append({
                "fare_component": fc,
                "change_policy": " ".join(changes) or None,
                "cancel_policy": " ".join(cancels) or None,
                "rules": {k: [" ".join(l.split()) for l in v if l.strip() and not _SKIP.match(l)] for k, v in part.items()},
            })
    except AmadeusSoapError:
        await _sign_out(session)
        return None
    if not any(c["rules"].get("PE") for c in components):
        return None

    def joined(key: str) -> str | None:
        texts = [c[key] for c in components if c[key]]
        if not texts:
            return None
        if len(components) == 1:
            return texts[0]
        return " | ".join(f"Fare {n}: {c[key]}" for n, c in enumerate(components, 1) if c[key])

    return {
        "change_policy": joined("change_policy"),
        "cancel_policy": joined("cancel_policy"),
        "components": components,
        "fare_basis": [s.get("fareBasis") for s in quote["groups"][0]["segments"]],
        "quoted_total": quote.get("total"),
        "currency": quote.get("currency"),
        "source": "Fare_CheckRules",
    }


async def _rules_via_endorsement(segments: list[dict], total_pax: int) -> dict:
    """When the quote / rules calls above fail: sell + price without a PNR and
    read the fare's endorsement line (otherPricingInfo END). 13/11/2026 audit:
    it is returned as the endorsement only - never as the change or cancel
    policy (e.g. "NON END/CHANGE FEE MAY APPLY" is not the fare's rules)."""
    session = AmadeusSession()
    await sell_segments(session, segments, total_pax)
    price_body = f"""<Fare_PricePNRWithBookingClass xmlns="{BOOKING_NS['price']}">
      <pricingOptionGroup>
        <pricingOptionKey><pricingOptionKey>RP</pricingOptionKey></pricingOptionKey>
      </pricingOptionGroup>
    </Fare_PricePNRWithBookingClass>"""
    body_root = await call(operation="Fare_PricePNRWithBookingClass", soap_action="http://webservices.amadeus.com/TPCBRQ_24_3_1A",
                           body_xml=price_body, log_prefix="fr2_price", session=session, stateful=True, end_session=True)
    reply = _find_local(body_root, "Fare_PricePNRWithBookingClassReply")
    rule_texts: list[str] = []
    for fare_list in _find_all_local(reply, "fareList") if reply is not None else []:
        for attr in _find_all_local(_find_local(fare_list, "otherPricingInfo"), "attributeDetails"):
            if _text(attr, "attributeType") == "END":
                desc = _text(attr, "attributeDescription")
                if desc and desc not in rule_texts:
                    rule_texts.append(desc)
    return {"change_policy": None, "cancel_policy": None, "endorsement": " / ".join(rule_texts) or None,
            "source": "endorsement"}


async def get_fare_rules(segments: list[dict], total_pax: int = 1) -> dict:
    travelers = [{"type": "adult"}] * max(1, total_pax)
    rules = await _rules_via_check_rules(segments, travelers)
    if rules:
        return rules
    return await _rules_via_endorsement(segments, total_pax)

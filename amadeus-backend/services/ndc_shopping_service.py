"""
ndc_shopping_service.py
=========================
Proof-of-concept for NDC_AirShopping -- a completely separate operation
family from everything else in this backend (confirmed via the WSDL pack,
`1ASIWGSTMLR_PDT_Altea_NDC_18_1_1.2_4.0.wsdl` + its imported
`Altea_NDC_18_1_1.2.wsdl`): it's the standard IATA NDC EDIST 2018.1
AirShoppingRQ/RS XML (namespace `http://www.iata.org/IATA/2015/00/2018.1/
AirShoppingRQ`), not Amadeus's own proprietary message shape the rest of
this backend uses. Its SOAP binding also declares a THIRD header beyond the
usual WS-Security/Session/AMA_SecurityHostedUser set --
`awsl:TransactionFlowLink` -- which amadeus_soap_client.call() now accepts
via `extra_header_xml` just for this.

This exists to answer one question: does NDC-sourced airline content even
come back for this office/sandbox at all. Not wired into the main /api/
flights/search endpoint or normalized into the shared offer shape yet --
see what this call actually returns first.
"""

from datetime import date

from services.amadeus_soap_client import call
from config.amadeus_ws_config import AMADEUS_WS_OFFICE_ID

NS = "http://www.iata.org/IATA/2015/00/2018.1/AirShoppingRQ"

TRANSACTION_FLOW_LINK_HEADER = (
    '<awsl:TransactionFlowLink xmlns:awsl="http://wsdl.amadeus.com/2010/06/ws/Link_v1"/>'
)


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


async def search_ndc(origin: str, destination: str, departure_date: date, adults: int = 1) -> dict:
    body = f"""<AirShoppingRQ xmlns="{NS}">
      <Party>
        <Sender>
          <TravelAgency>
            <AgencyID>{AMADEUS_WS_OFFICE_ID}</AgencyID>
            <PseudoCityID>{AMADEUS_WS_OFFICE_ID}</PseudoCityID>
          </TravelAgency>
        </Sender>
      </Party>
      <Request>
        <FlightRequest>
          <OriginDestRequest>
            <DestArrivalRequest>
              <IATA_LocationCode>{destination.upper()}</IATA_LocationCode>
            </DestArrivalRequest>
            <OriginDepRequest>
              <IATA_LocationCode>{origin.upper()}</IATA_LocationCode>
              <Date>{departure_date.isoformat()}</Date>
            </OriginDepRequest>
          </OriginDestRequest>
        </FlightRequest>
        <Paxs>
          <Pax>
            <PaxID>PAX1</PaxID>
            <PTC>ADT</PTC>
          </Pax>
        </Paxs>
      </Request>
    </AirShoppingRQ>"""

    body_root = await call(
        operation="NDC_AirShopping",
        soap_action="http://webservices.amadeus.com/NDC_AirShopping_18.1",
        body_xml=body,
        log_prefix="ndc_air_shopping",
        extra_header_xml=TRANSACTION_FLOW_LINK_HEADER,
    )

    reply = _find_local(body_root, "AirShoppingRS")
    if reply is None:
        return {"raw_root_tag": body_root.tag if body_root is not None else None, "offers": []}

    # Confirmed live: AirShoppingRS puts a bare <Error> directly under the
    # root (code/DescText/TypeCode), not nested in an <Errors> wrapper the
    # way an IATA schema reading alone would suggest -- see
    # errorCode 911 "Target office id not found" below.
    error_els = _find_all_local(reply, "Error")
    if error_els:
        errors = [
            {"code": _find_local(e, "Code").text if _find_local(e, "Code") is not None else None,
             "text": _find_local(e, "DescText").text if _find_local(e, "DescText") is not None else None}
            for e in error_els
        ]
        return {"errors": errors, "offers": []}

    offers_group = _find_local(reply, "OffersGroup")
    offers = _find_all_local(offers_group, "AirlineOffers")
    return {"offer_group_count": len(offers), "offers": []}

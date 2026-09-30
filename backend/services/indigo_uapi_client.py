"""
services/indigo_uapi_client.py
================================
Shared HTTP + XML/SOAP plumbing for Travelport's legacy Universal API
(uAPI) — IndiGo (6E) ancillary content, provider code ACH. Kept fully
separate from every GDS/NDC (JSON TripServices v11) service module:
different wire format (SOAP-wrapped XML bodies, not JSON), different auth
(HTTP Basic, not OAuth Bearer), different Travelport product entirely.

Nothing in this module is imported by, or touches, search_service.py,
workbench_service.py, ticket_service.py, auth_service.py, or
api_endpoints.py — the existing Air (GDS/NDC) and Hotel integrations are
untouched by this work.

Built strictly to the payload shapes documented in the reference logs under
"Indigo all ancillary service" (also converted to this project's own JSON
convention at backend/reference/indigo_uapi_json/, for readability). The
SOAP envelope wrapping here is CONFIRMED correct via a live reachability
test (2026-09-29): pointing this client at the JSON API's own sandbox OAuth
credentials produced a genuine Apache Axis SOAP fault response
(WWW-Authenticate: Basic realm="AXIS", body a <SOAP-ENV:Fault> with
faultcode 76 "Authentication credentials are invalid") — proving the
endpoint, SOAP envelope, and content type are all correct; only real uAPI
credentials remain unverified.
"""

import logging
import xml.etree.ElementTree as ET
import httpx
from config.indigo_config import IndigoConfig
from utils import tp_logger

logger = logging.getLogger(__name__)

# ── XML namespaces used across the reference logs ──────────────────────────
NS_AIR = "http://www.travelport.com/schema/air_v43_0"
NS_COMMON = "http://www.travelport.com/schema/common_v43_0"
NS_UNIV = "http://www.travelport.com/schema/universal_v43_0"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"

for prefix, uri in (("air", NS_AIR), ("common", NS_COMMON), ("univ", NS_UNIV), ("xsi", NS_XSI), ("soapenv", NS_SOAP)):
    ET.register_namespace(prefix, uri)


def air_tag(name: str) -> str:
    return f"{{{NS_AIR}}}{name}"


def common_tag(name: str) -> str:
    return f"{{{NS_COMMON}}}{name}"


def univ_tag(name: str) -> str:
    return f"{{{NS_UNIV}}}{name}"


class IndigoApiError(Exception):
    """Raised for any non-2xx response, or an embedded uAPI/SOAP <Fault>, from a legacy uAPI call."""

    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def serialize(root: ET.Element) -> bytes:
    """Wrap a uAPI message element in a SOAP 1.1 envelope and render to UTF-8 — see module docstring for why."""
    envelope = ET.Element(f"{{{NS_SOAP}}}Envelope")
    ET.SubElement(envelope, f"{{{NS_SOAP}}}Header")
    body = ET.SubElement(envelope, f"{{{NS_SOAP}}}Body")
    body.append(root)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(envelope, encoding="utf-8")


def post_xml(url: str, root: ET.Element) -> ET.Element:
    """
    POST a uAPI SOAP request body and return the parsed *inner* response
    element (i.e. the caller gets back a plain <air:LowFareSearchRsp> etc.,
    with the SOAP envelope already unwrapped here).

    Auth: HTTP Basic (TP_UAPI_USERNAME / TP_UAPI_PASSWORD) per Travelport's
    legacy uAPI convention — distinct from the OAuth Bearer flow every other
    Travelport call in this codebase uses (see services/auth_service.py).

    Raises:
        IndigoApiError: on a non-2xx HTTP response, a SOAP Fault (even inside
        a 2xx response — some SOAP stacks do this), or an unparsable body.
    """
    if not IndigoConfig.is_configured():
        raise IndigoApiError(
            "Travelport legacy Universal API is not configured — "
            "TP_UAPI_USERNAME/PASSWORD/TARGET_BRANCH/BASE_URL are empty in .env. "
            "None of these have a default — ask your Travelport account rep for "
            "legacy uAPI (IndiGo/ACH) credentials AND your account's uAPI endpoint host."
        )

    body = serialize(root)
    headers = {
        "Content-Type": "text/xml; charset=UTF-8",
        "Accept": "text/xml",
        # Travelport's Axis-based uAPI expects a SOAPAction header; "" is
        # valid per the SOAP 1.1 spec when the operation is identified by the
        # body's root element instead. UNVERIFIED against this account's own
        # WSDL — revisit if a real call rejects it for that specific reason.
        "SOAPAction": "",
    }

    with httpx.Client(
        timeout=IndigoConfig.REQUEST_TIMEOUT,
        auth=(IndigoConfig.USERNAME, IndigoConfig.PASSWORD),
        event_hooks=tp_logger.HOOKS,
    ) as client:
        response = client.post(url, content=body, headers=headers)

    try:
        parsed = ET.fromstring(response.content)
    except ET.ParseError as e:
        raise IndigoApiError(
            f"Could not parse uAPI response as XML (HTTP {response.status_code}): {e}",
            status_code=response.status_code, body=response.text[:2000],
        ) from e

    fault = parsed.find(f".//{{{NS_SOAP}}}Fault")
    if fault is not None:
        fault_string = fault.findtext("faultstring") or fault.findtext(f"{{{NS_SOAP}}}faultstring") or "Unknown SOAP fault"
        raise IndigoApiError(
            f"Travelport uAPI SOAP fault: {fault_string}",
            status_code=response.status_code, body=response.text[:2000],
        )

    if response.status_code >= 400:
        raise IndigoApiError(
            f"Travelport uAPI request failed: HTTP {response.status_code}",
            status_code=response.status_code,
            body=response.text[:2000],
        )

    inner = parsed.find(f"{{{NS_SOAP}}}Body")
    if inner is not None and len(inner):
        return inner[0]
    return parsed


def find_all(root: ET.Element, path: str) -> list[ET.Element]:
    """findall() convenience that accepts 'air:Tag/common:Tag' shorthand."""
    return root.findall(_expand_path(path))


def find_one(root: ET.Element, path: str) -> ET.Element | None:
    return root.find(_expand_path(path))


def _expand_path(path: str) -> str:
    ns = {"air": NS_AIR, "common": NS_COMMON, "univ": NS_UNIV}
    parts = []
    for segment in path.split("/"):
        if ":" in segment:
            prefix, tag = segment.split(":", 1)
            parts.append(f"{{{ns[prefix]}}}{tag}")
        else:
            parts.append(segment)
    return "/".join(parts)

"""
amadeus_soap_client.py
=======================
SOAP client for Amadeus Web Services using SOAP Header 4.0, confirmed against
Amadeus's official "Web Services Implementation Guide: SOAP 4.0" (v1.8, Feb
2019), downloaded directly from the WSAP's own Technical Documentation page.

SOAP header 4.0 has four parts (per the guide):
  1. WS-Addressing header   -- MessageID, Action, To (every request)
  2. Amadeus Session header -- only for stateful (context-full) flows;
                                omitted entirely for context-less calls like
                                flight search (confirmed by the guide's own
                                "Context-less (Stateless)" example)
  3. WS-Security header     -- UsernameToken: Username, Nonce, Password
                                (digest), Created -- sent on the first call
                                of a conversation only
  4. Amadeus Security header -- AMA_SecurityHostedUser/UserID attributes --
                                sent on the first call of a conversation only

Password digest algorithm (guide page 12):
    Base64(SHA1(nonce_bytes + timestamp_str + SHA1(password_bytes)))
(SHA1 here means the raw 20-byte digest, not hex.)

There is no separate "login" operation for header 4.0 -- Fare_MasterPricerTravelBoardSearch
*is* the authenticated call. This replaces an earlier (incorrect) attempt
that tried to call the legacy Security_Authenticate business message, which
only applies to older SOAP header versions and caused a
"12|Presentation|soap message header incorrect" fault.
"""

import os
import base64
import hashlib
import secrets
import logging
import uuid
from datetime import datetime, timezone
from xml.sax.saxutils import escape

import httpx
from xml.etree import ElementTree as ET

from config.amadeus_ws_config import (
    AMADEUS_WS_USERNAME,
    AMADEUS_WS_OFFICE_ID,
    AMADEUS_WS_PASSWORD,
    AMADEUS_WS_ENDPOINT,
)

logger = logging.getLogger(__name__)

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "amadeus")
os.makedirs(LOG_DIR, exist_ok=True)

SOAPENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
WSA_NS = "http://www.w3.org/2005/08/addressing"
SESSION_NS = "http://xml.amadeus.com/2010/06/Session_v3"
WSSE_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
AMA_SEC_NS = "http://xml.amadeus.com/2010/06/Security_v1"


class AmadeusSoapError(Exception):
    def __init__(self, message: str, status_code: int = 502, detail=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail


class AmadeusSession:
    """Session header state for a context-full (stateful) conversation."""

    def __init__(self):
        self.session_id: str | None = None
        self.sequence_number: int = 0
        self.security_token: str | None = None

    @property
    def is_open(self) -> bool:
        return self.session_id is not None

    def update_from_header(self, header: ET.Element | None):
        if header is None:
            return
        session_el = header.find(f"{{{SESSION_NS}}}Session")
        if session_el is None:
            return
        sid = session_el.find(f"{{{SESSION_NS}}}SessionId")
        seq = session_el.find(f"{{{SESSION_NS}}}SequenceNumber")
        tok = session_el.find(f"{{{SESSION_NS}}}SecurityToken")
        status = session_el.get("TransactionStatusCode")
        if sid is not None:
            self.session_id = sid.text
        if seq is not None:
            self.sequence_number = int(seq.text)
        if tok is not None:
            self.security_token = tok.text
        if status == "End":
            self.session_id = None
            self.sequence_number = 0
            self.security_token = None


def _password_digest(nonce_bytes: bytes, timestamp: str, password: str) -> str:
    password_sha1 = hashlib.sha1(password.encode("utf-8")).digest()
    combined = nonce_bytes + timestamp.encode("utf-8") + password_sha1
    digest = hashlib.sha1(combined).digest()
    return base64.b64encode(digest).decode("ascii")


def _ws_security_header() -> str:
    nonce_bytes = secrets.token_bytes(32)
    nonce_b64 = base64.b64encode(nonce_bytes).decode("ascii")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    password_digest = _password_digest(nonce_bytes, created, AMADEUS_WS_PASSWORD)

    return f"""<oas:Security xmlns:oas="{WSSE_NS}" xmlns:oas1="{WSU_NS}">
      <oas:UsernameToken oas1:Id="UsernameToken-1">
        <oas:Username>{escape(AMADEUS_WS_USERNAME)}</oas:Username>
        <oas:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</oas:Nonce>
        <oas:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{password_digest}</oas:Password>
        <oas1:Created>{created}</oas1:Created>
      </oas:UsernameToken>
    </oas:Security>"""


def _ama_security_header() -> str:
    return (
        f'<AMA_SecurityHostedUser xmlns="{AMA_SEC_NS}">'
        f'<UserID AgentDutyCode="SU" RequestorType="U" '
        f'PseudoCityCode="{escape(AMADEUS_WS_OFFICE_ID)}" POS_Type="1"/>'
        f'</AMA_SecurityHostedUser>'
    )


def _log(prefix: str, request_xml: str, response_xml: str | None, error: str | None = None):
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    base = os.path.join(LOG_DIR, f"{ts}.{prefix}")
    try:
        with open(base + "_RQ.xml", "w", encoding="utf-8") as f:
            f.write(request_xml)
        if response_xml:
            with open(base + "_RS.xml", "w", encoding="utf-8") as f:
                f.write(response_xml)
        if error:
            with open(base + "_ERROR.txt", "w", encoding="utf-8") as f:
                f.write(error)
    except OSError:
        logger.exception("Failed writing Amadeus SOAP log files")


async def call(
    operation: str,
    soap_action: str,
    body_xml: str,
    log_prefix: str,
    session: AmadeusSession | None = None,
    stateful: bool = False,
    end_session: bool = False,
) -> ET.Element:
    """
    POST one SOAP operation using header 4.0.

    - Context-less (default): full WS-Security + Amadeus Security headers on
      every call, no Session header. Use for flight search.
    - Context-full (stateful=True): pass a shared AmadeusSession. The first
      call (session.is_open == False) sends WS-Security + Amadeus Security +
      Session(Start); subsequent calls send only Session(InSeries); pass
      end_session=True on the last call to send Session(End).
    """
    if not AMADEUS_WS_ENDPOINT:
        raise AmadeusSoapError("AMADEUS_WS_ENDPOINT is not configured in backend/.env", status_code=500)

    message_id = str(uuid.uuid4())

    header_parts = [
        f'<add:MessageID xmlns:add="{WSA_NS}">{message_id}</add:MessageID>',
        f'<add:Action xmlns:add="{WSA_NS}">{soap_action}</add:Action>',
        f'<add:To xmlns:add="{WSA_NS}">{escape(AMADEUS_WS_ENDPOINT)}</add:To>',
    ]

    if stateful and session is not None and session.is_open:
        status = "End" if end_session else "InSeries"
        header_parts.append(
            f'<awsse:Session xmlns:awsse="{SESSION_NS}" TransactionStatusCode="{status}">'
            f'<awsse:SessionId>{escape(session.session_id)}</awsse:SessionId>'
            f'<awsse:SequenceNumber>{session.sequence_number}</awsse:SequenceNumber>'
            f'<awsse:SecurityToken>{escape(session.security_token)}</awsse:SecurityToken>'
            f'</awsse:Session>'
        )
    else:
        header_parts.append(_ws_security_header())
        header_parts.append(_ama_security_header())
        if stateful:
            header_parts.append(f'<awsse:Session xmlns:awsse="{SESSION_NS}" TransactionStatusCode="Start"/>')

    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="{SOAPENV_NS}">
  <soapenv:Header>
    {''.join(header_parts)}
  </soapenv:Header>
  <soapenv:Body>
{body_xml}
  </soapenv:Body>
</soapenv:Envelope>"""

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": soap_action,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(AMADEUS_WS_ENDPOINT, content=envelope.encode("utf-8"), headers=headers)
    except httpx.HTTPError as exc:
        _log(log_prefix, envelope, None, error=str(exc))
        raise AmadeusSoapError(f"Amadeus SOAP request failed: {exc}", status_code=502)

    _log(log_prefix, envelope, resp.text)

    if resp.status_code >= 400 and resp.status_code != 500:
        raise AmadeusSoapError(f"Amadeus SOAP HTTP error {resp.status_code}", status_code=502, detail=resp.text)

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        raise AmadeusSoapError(f"Could not parse Amadeus SOAP response: {exc}", status_code=502, detail=resp.text)

    header_el = root.find(f"{{{SOAPENV_NS}}}Header")
    if stateful and session is not None:
        session.update_from_header(header_el)

    fault = root.find(f".//{{{SOAPENV_NS}}}Fault")
    if fault is not None:
        fault_string = fault.findtext("faultstring", default="Unknown SOAP fault")
        raise AmadeusSoapError(f"Amadeus SOAP fault: {fault_string}", status_code=502, detail=resp.text)

    body = root.find(f"{{{SOAPENV_NS}}}Body")
    return body

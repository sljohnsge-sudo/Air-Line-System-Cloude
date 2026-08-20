"""
services/paycorp_service.py
============================
PayCorp (Sampath Bank) Payment Gateway — Hosted Page Integration.

Flow (per PayCorp's own REST API, referenced from their public PHP SDK —
no official docs were provided, this mirrors https://github.com/bitixel/paycorp):

  1. init_payment()     -> PAYMENT_INIT.  Returns a paymentPageUrl. The
                            frontend redirects the customer's browser there.
                            Card details are entered on PayCorp's own hosted
                            page — they never pass through this backend.
  2. (customer pays on PayCorp's page, gets redirected back to our return_url
      with ?reqid=... in the query string)
  3. complete_payment() -> PAYMENT_COMPLETE. Looks up the final result for
                            that reqid: responseCode "00" = successful charge.

HMAC: hex(HMAC-SHA256(hmac_secret, raw_json_request_body)), sent in an
'HMAC' header alongside 'AUTHTOKEN'. The hash must be computed over the
EXACT bytes posted, so the request is built as a string once and reused
for both hashing and sending.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime

import httpx

from config.paycorp_config import PayCorpConfig
from utils import tp_logger

logger = logging.getLogger(__name__)

_OP_PAYMENT_INIT = "PAYMENT_INIT"
_OP_PAYMENT_COMPLETE = "PAYMENT_COMPLETE"
_VERSION = "1.04"


class PayCorpError(Exception):
    """Raised when PayCorp returns an {"error": {...}} envelope."""
    pass


def _compute_hmac(secret: str, data: str) -> str:
    return hmac.new(secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()


def _build_envelope(operation: str, request_data: dict, validate_only: bool = False) -> tuple[str, str]:
    """Build the PaycorpRequest envelope and serialize it to the exact JSON
    string that will be both HMAC-signed and posted as the request body."""
    msg_id = str(uuid.uuid4())
    envelope = {
        "version": _VERSION,
        "msgId": msg_id,
        "operation": operation,
        "requestDate": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "validateOnly": validate_only,
        "requestData": request_data,
    }
    return json.dumps(envelope), msg_id


def _send(json_body: str) -> dict:
    headers = {
        "HMAC": _compute_hmac(PayCorpConfig.HMAC_SECRET, json_body),
        "AUTHTOKEN": PayCorpConfig.AUTH_TOKEN,
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=60, event_hooks=tp_logger.HOOKS) as client:
        response = client.post(PayCorpConfig.ENDPOINT, content=json_body, headers=headers)
        response.raise_for_status()
        result = response.json()

    if result.get("error"):
        err = result["error"]
        raise PayCorpError(f"PayCorp error {err.get('code')}: {err.get('text')}")

    if "responseData" not in result:
        raise PayCorpError(f"Unexpected PayCorp response (no responseData): {result}")

    return result["responseData"]


def init_payment(
    amount: float,
    currency: str,
    return_url: str,
    cancel_url: str | None = None,
    client_ref: str | None = None,
    comment: str | None = None,
) -> dict:
    """
    STEP 1: Initiate a hosted-page payment.

    Args:
        amount (float): Amount in major currency units (e.g. 98900.00 LKR).
        currency (str): "LKR" or "USD" — selects the matching Client ID.
        return_url (str): Where PayCorp redirects the browser after payment,
            with ?reqid=... appended.
        cancel_url (str|None): Where PayCorp redirects if the customer cancels.
        client_ref (str|None): Our own reference (e.g. a pending booking id)
            echoed back unchanged in the PAYMENT_COMPLETE response.
        comment (str|None): Free-text note, echoed back unchanged.

    Returns:
        dict: {"reqid": str, "expire_at": str, "payment_page_url": str}
    """
    client_id = PayCorpConfig.client_id_for_currency(currency)
    request_data = {
        "clientId": client_id,
        "clientIdHash": "",
        "transactionType": "PURCHASE",
        "transactionAmount": {
            "paymentAmount": int(round(amount * 100)),
            "currency": currency.upper(),
        },
        "redirect": {
            "returnUrl": return_url,
            "returnMethod": "GET",
        },
        "useReliability": True,
    }
    if cancel_url:
        request_data["redirect"]["cancelUrl"] = cancel_url
    if client_ref:
        request_data["clientRef"] = client_ref
    if comment:
        request_data["comment"] = comment

    json_body, _ = _build_envelope(_OP_PAYMENT_INIT, request_data)
    logger.info(f"PayCorp PAYMENT_INIT: {currency} {amount} (clientId={client_id})")
    data = _send(json_body)

    return {
        "reqid": data.get("reqid"),
        "expire_at": data.get("expireAt"),
        "payment_page_url": data.get("paymentPageUrl"),
    }


def complete_payment(reqid: str) -> dict:
    """
    STEP 3: Look up the final result of a hosted-page payment after the
    customer is redirected back.

    Args:
        reqid (str): The reqid returned by init_payment() / echoed back in
            the return-URL query string.

    Returns:
        dict: Parsed completion result — success iff response_code == "00".
    """
    request_data = {"reqid": reqid}
    json_body, _ = _build_envelope(_OP_PAYMENT_COMPLETE, request_data)
    logger.info(f"PayCorp PAYMENT_COMPLETE: reqid={reqid}")
    data = _send(json_body)

    card = data.get("creditCard", {}) or {}
    amount = data.get("transactionAmount", {}) or {}

    success = data.get("responseCode") == "00"
    logger.info(
        f"PayCorp PAYMENT_COMPLETE result: responseCode={data.get('responseCode')} "
        f"({'SUCCESS' if success else 'FAILED'}) txnReference={data.get('txnReference')}"
    )

    return {
        "success": success,
        "response_code": data.get("responseCode"),
        "response_text": data.get("responseText"),
        "txn_reference": data.get("txnReference"),
        "auth_code": data.get("authCode"),
        "client_ref": data.get("clientRef"),
        "comment": data.get("comment"),
        "card_type": card.get("type"),
        "card_number_masked": card.get("number"),
        "card_expiry": card.get("expiry"),
        "total_amount": amount.get("totalAmount"),
        "payment_amount": amount.get("paymentAmount"),
        "currency": amount.get("currency"),
        "raw": data,
    }

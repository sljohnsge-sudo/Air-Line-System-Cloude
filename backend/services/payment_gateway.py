"""
services/payment_gateway.py
===========================
The site's card payment gateway, chosen in .env (PAYMENT_GATEWAY):
"cybersource" = NDB Bank (CyberSource Secure Acceptance), "paycorp" =
Sampath (the previous gateway). Every caller uses these two functions, so
the checks around them (amount, booking reference, single use, no ticket
without a confirmed payment) don't depend on the gateway.

A payment is always completed by the gateway that started it: an NDB
reqid is recognised by its record, anything else goes to PayCorp.
"""
from config.cybersource_config import CyberSourceConfig
from services import paycorp_service, cybersource_service
from services.paycorp_service import PayCorpError


def init_payment(amount: float, currency: str, return_url: str, cancel_url: str | None = None,
                 client_ref: str | None = None, comment: str | None = None) -> dict:
    gateway = cybersource_service if CyberSourceConfig.active() else paycorp_service
    return gateway.init_payment(amount=amount, currency=currency, return_url=return_url,
                                cancel_url=cancel_url, client_ref=client_ref, comment=comment)


def complete_payment(reqid: str) -> dict:
    if cybersource_service.owns(reqid):
        return cybersource_service.complete_payment(reqid)
    return paycorp_service.complete_payment(reqid)


__all__ = ["init_payment", "complete_payment", "PayCorpError"]

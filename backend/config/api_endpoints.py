"""
api_endpoints.py
================
All Travelport TripServices API endpoint paths in one place.
Based on official Travelport developer docs: https://developer.travelport.com/apis/flights

Workflow Steps:
  STEP 1  — OAuth Token
  STEP 2  — Flight Search (Catalog Product Offerings / Air Availability)
  STEP 2b — Next Leg Search (for leg-based round-trip)
  STEP 2c — Flight Specific Search (upsells)
  STEP 2d — Premium Flex Search
  STEP 2e — Premium Flex Flight Specific Search
  STEP 3  — (Frontend: user selects an offer)
  STEP 4  — Create Reservation Workbench
  STEP 5  — Add Offer to Workbench
  STEP 6  — Add Traveler(s) to Workbench
  STEP 7  — Commit Workbench → Generate PNR
  STEP 8  — Retrieve PNR / Ticket Details
  STEP 9  — Issue Ticket
  STEP 10 — (Frontend: display ticket popup)

To modify an endpoint: update only this file.
"""

from config.travelport_config import TravelportConfig

# Convenience aliases
_base = TravelportConfig.base_path()
_air  = f"{_base}/air"


class TravelportEndpoints:
    """All Travelport REST endpoint URLs."""

    # ── STEP 1: Authentication ─────────────────────────────────────────────────
    OAUTH_TOKEN = TravelportConfig.AUTH_URL
    # POST  → exchange credentials for Bearer token

    # ── STEP 2: Air Availability ───────────────────────────────────────────────
    AIR_AVAILABILITY = f"{_air}/search/airAvailability"
    # POST  → returns scheduled flights with seat availability by class of service
    # GDS only; not supported for NDC. Does NOT return pricing.

    # ── STEP 2: Flight Search (Catalog Product Offerings) ─────────────────────
    FLIGHT_SEARCH = f"{_air}/catalog/search/catalogproductofferings"
    # POST  → search available flights with pricing (journey-based or leg-based)

    # ── STEP 2b: Next Leg Search ───────────────────────────────────────────────
    FLIGHT_NEXT_LEG_SEARCH = f"{_air}/catalog/search/catalogproductofferings/buildnext"
    # POST  → returns offers for the next leg after a leg-based Search
    # Required for round-trip leg-based searches; not valid after journey-based Search.

    # ── STEP 2c: Flight Specific Search ───────────────────────────────────────
    FLIGHT_SPECIFIC_SEARCH = f"{_air}/catalog/search/catalogproductofferings/buildoptions"
    # POST  → returns additional upsells (up to 99) for products from Search
    # GDS only; not supported for NDC.

    # ── STEP 2d: Premium Flex Search ──────────────────────────────────────────
    PREMIUM_FLEX_SEARCH = f"{_air}/catalog/search/catalogproductofferings/premiumflex"
    # POST  → first step of Premium Flex booking workflow (GDS only)
    # Supports up to 3 PCCs in PricingModifiersAir.MultiPricingAgency.
    # Supports max 2 O&Ds. Response can be streamed (application/stream+json)
    # or aggregated (application/json).

    # ── STEP 2e: Premium Flex Flight Specific Search ───────────────────────────
    PREMIUM_FLEX_SPECIFIC_SEARCH = f"{_air}/catalog/search/catalogproductofferings/buildpremiumflexoptions"
    # POST  → upsells for Premium Flex (GDS only, up to 99 upsells)

    # ── STEP 4: Reservation Workbench ─────────────────────────────────────────
    CREATE_WORKBENCH = f"{_air}/book/session/reservationworkbench"
    # POST  → create a new reservation workbench session

    # ── STEP 5: Add Offer to Workbench ────────────────────────────────────────
    @staticmethod
    def add_offer_to_workbench(workbench_id: str) -> str:
        """POST → add a selected flight offer to the workbench (reference payload).
        Used for NDC/LCC content — Travelport: full payload not supported for NDC."""
        return f"{_air}/book/airoffer/reservationworkbench/{workbench_id}/offers/buildfromcatalogproductofferings"

    @staticmethod
    def add_offer_to_workbench_full_payload(workbench_id: str) -> str:
        """POST → add a selected flight offer to the workbench (full payload).
        Travelport GDS certification guidance: use full payload for GDS carrier
        bookings — sends complete itinerary + fare/brand selection directly,
        with no dependency on a cached Search session."""
        return f"{_air}/book/airoffer/reservationworkbench/{workbench_id}/offers/buildfromproducts"

    # ── STEP 6: Add Traveler(s) ────────────────────────────────────────────────
    @staticmethod
    def update_workbench(workbench_id: str) -> str:
        """POST → add passenger/traveler details to an existing workbench."""
        return f"{_air}/book/traveler/reservationworkbench/{workbench_id}/travelers"

    @staticmethod
    def add_travelers_list(workbench_id: str) -> str:
        """POST → add MULTIPLE travelers (Adult/Child/Infant) to the workbench
        in a single TravelerListRequest. Required so that all passengers on
        one PNR are sent to Travelport as one combined request instead of
        one request per traveler type."""
        return f"{_air}/book/traveler/reservationworkbench/{workbench_id}/travelers/list"

    @staticmethod
    def get_workbench(workbench_id: str) -> str:
        """GET → retrieve the current state of an open workbench session
        (e.g. to verify travelers were added correctly before committing)."""
        return f"{_air}/book/session/reservationworkbench/{workbench_id}"

    # ── STEP 7: Commit Workbench → Generate PNR ───────────────────────────────
    @staticmethod
    def commit_workbench(workbench_id: str) -> str:
        """POST → commit workbench, generates the PNR/Locator Code."""
        return f"{_air}/book/reservation/reservations/{workbench_id}"

    # ── STEP 8: Retrieve PNR / Reservation Details ────────────────────────────
    @staticmethod
    def retrieve_reservation(locator_code: str) -> str:
        """GET → retrieve full PNR and itinerary details by locator."""
        return f"{_air}/book/reservation/reservations/{locator_code}"

    # ── Cancel Reservation ─────────────────────────────────────────────────────
    # NOTE: There is no DELETE on /air/book/reservation/reservations/{Identifier} —
    # that path only allows GET (retrieve) and POST (commit); confirmed live against
    # the sandbox (405 Method Not Allowed, Allow: POST, GET). Cancellation instead
    # goes through a post-commit workbench, same pattern as ticket issuance:
    #   1. buildfromlocator to open a workbench against the existing PNR
    #   2. POST cancelitems (cancelAllInd: true) to cancel the offer
    #   3. commit the workbench to finalize
    @staticmethod
    def cancel_items(workbench_id: str) -> str:
        """POST -> cancel offers/segments within a post-commit workbench.
        NOTE: per Travelport docs this path has NO /air prefix, unlike every
        other endpoint in this API."""
        return f"{_base}/book/reservationworkbench/{workbench_id}/reservations/cancelitems"

    # ── Ticket Retrieve ─────────────────────────────────────────────────────────
    # Dedicated ticket lookup, separate from Reservation Retrieve's Ticket[].
    # https://developer.travelport.com/apis/flights/ticketing/ticketgetbylocator
    TICKET_RETRIEVE_BY_LOCATOR = f"{_air}/ticket/tickets/getbylocator"

    # ── Post-Commit Ticketing Workflow ─────────────────────────────────────────
    # For ticketing a held PNR (already committed), a NEW workbench must be
    # opened against the existing reservation, then FOP + payment added,
    # then the workbench committed to issue the ticket.

    @staticmethod
    def create_postcommit_workbench() -> str:
        """POST -> create a post-commit workbench for an existing reservation."""
        return f"{_air}/book/session/reservationworkbench"

    @staticmethod
    def add_fop_to_workbench(workbench_id: str) -> str:
        """POST -> add Form of Payment to a post-commit workbench."""
        return f"{_air}/payment/reservationworkbench/{workbench_id}/formofpayment"

    @staticmethod
    def add_payment_to_workbench(workbench_id: str) -> str:
        """POST -> add Payment to workbench linking FOP to offer (triggers actual issuance at commit).
        NOTE: Uses /paymentoffer/ path, NOT /payment/ — per Travelport TripServices v11 docs."""
        return f"{_air}/paymentoffer/reservationworkbench/{workbench_id}/payments"

    @staticmethod
    def commit_postcommit_workbench(workbench_id: str) -> str:
        """POST -> commit the post-commit workbench to issue the ticket."""
        return f"{_air}/book/reservation/reservations/{workbench_id}"

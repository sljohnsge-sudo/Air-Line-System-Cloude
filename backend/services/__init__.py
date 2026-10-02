"""
services/__init__.py
Makes services a Python package.
"""

from .auth_service import get_access_token, get_auth_headers, invalidate_token, flow_trace_id, start_flow_trace_id, end_flow_trace_id
from .search_service import search_flights, parse_flight_offers
from .workbench_service import create_workbench, add_offer_to_workbench, add_traveler_to_workbench, add_travelers_to_workbench, get_workbench_details, commit_workbench, get_seat_map, run_booking_flow, discard_workbench
from .ticket_service import retrieve_reservation, issue_ticket, cancel_reservation, parse_commit_response, retrieve_ndc_ticket
from .invoice_service import retrieve_invoice_data
from .paycorp_service import init_payment, complete_payment, PayCorpError
from .pricing_service import get_settings as get_pricing_settings_cfg, apply_markup
from .loyalty_service import award_points_for_booking, get_loyalty_summary
from .email_service import send_visa_consultation_email, send_provider_failure_email
from .amadeus_aggregator import search_amadeus_flights
from .amadeus_booking_proxy import confirm_booking as confirm_amadeus_booking, issue_ticket as issue_amadeus_ticket, AmadeusBookingError

__all__ = [
    "get_access_token", "get_auth_headers", "invalidate_token", "flow_trace_id", "start_flow_trace_id", "end_flow_trace_id",
    "search_flights", "parse_flight_offers",
    "create_workbench", "add_offer_to_workbench", "add_traveler_to_workbench", "add_travelers_to_workbench", "get_workbench_details", "commit_workbench", "get_seat_map", "run_booking_flow", "discard_workbench",
    "retrieve_reservation", "issue_ticket", "cancel_reservation", "parse_commit_response", "retrieve_ndc_ticket",
    "retrieve_invoice_data",
    "init_payment", "complete_payment", "PayCorpError",
    "get_pricing_settings_cfg", "apply_markup",
    "award_points_for_booking", "get_loyalty_summary",
    "send_visa_consultation_email", "send_provider_failure_email",
    "search_amadeus_flights",
    "confirm_amadeus_booking", "issue_amadeus_ticket", "AmadeusBookingError",
]

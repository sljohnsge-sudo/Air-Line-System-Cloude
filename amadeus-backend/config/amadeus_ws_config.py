"""
amadeus_ws_config.py
=====================
Loads Amadeus Web Services (SOAP/XML) credentials from .env.
This is the classic Altea GDS interface (WSAP-based), not the
decommissioned Amadeus-for-Developers self-service REST API.
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

AMADEUS_WS_USERNAME = os.getenv("AMADEUS_WS_USERNAME", "")
AMADEUS_WS_OFFICE_ID = os.getenv("AMADEUS_WS_OFFICE_ID", "")
AMADEUS_WS_WSAP = os.getenv("AMADEUS_WS_WSAP", "")
AMADEUS_WS_PASSWORD = os.getenv("AMADEUS_WS_PASSWORD", "")
AMADEUS_WS_ENDPOINT = os.getenv("AMADEUS_WS_ENDPOINT", "")
AMADEUS_WS_SOAP_HEADER_VERSION = os.getenv("AMADEUS_WS_SOAP_HEADER_VERSION", "4.0")

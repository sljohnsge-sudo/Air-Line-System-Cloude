"""
config/auth_config.py
======================
JWT authentication configuration for the Admin Portal and Customer Portal.

To update credentials: edit the .env file. Never hardcode values here.
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


class AuthConfig:
    """Central configuration class for JWT-based admin/customer auth."""

    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
    # Staff sign-ins expire sooner than customers' (16/11/2026, security):
    # a leaked admin token is only usable for this long.
    ADMIN_JWT_EXPIRE_MINUTES: int = int(os.getenv("ADMIN_JWT_EXPIRE_MINUTES", "120"))
    # The signing key must be long and random: with an empty or short key
    # anyone could sign their own admin login. main.py refuses to start
    # below this length.
    JWT_SECRET_MIN_LENGTH = 32
    # Admin site (APP_MODE=admin, 16/11/2026): staff and analytics sign-ins are
    # signed with their own key, different from JWT_SECRET_KEY. The customer
    # site never accepts a staff token, whatever key it holds.
    ADMIN_JWT_SECRET_KEY: str = os.getenv("ADMIN_JWT_SECRET_KEY", "")
    APP_MODE: str = (os.getenv("APP_MODE", "public").strip().lower() or "public")

    # ── Google / Microsoft Sign-In (Customer Portal) ─────────────────────────
    # Empty by default -- /api/customers/oauth/{provider}/start returns a
    # clear "not configured" error instead of attempting a broken redirect
    # until real values are added here. See services/customer_oauth_service.py.
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    MICROSOFT_CLIENT_ID: str = os.getenv("MICROSOFT_CLIENT_ID", "")
    MICROSOFT_CLIENT_SECRET: str = os.getenv("MICROSOFT_CLIENT_SECRET", "")
    # "consumers" = personal Microsoft accounts (their emails are verified by
    # Microsoft). 16/11/2026: "common" is refused at sign-in — any Azure
    # tenant admin can put any email on a user there (customer_oauth_service).
    MICROSOFT_TENANT: str = os.getenv("MICROSOFT_TENANT", "consumers")
    # This backend's own public base URL -- used to build the redirect_uri
    # Google/Microsoft send the browser back to after consent. Must exactly
    # match a Redirect URI registered in each provider's app console (see
    # setup docs) or the provider will refuse the exchange.
    OAUTH_REDIRECT_BASE: str = os.getenv("OAUTH_REDIRECT_BASE", "http://localhost:8005")
    # Where the browser lands (with the finished login token) once the
    # backend's own OAuth callback completes.
    FRONTEND_BASE_URL: str = os.getenv("FRONTEND_BASE_URL", "http://localhost:5176")

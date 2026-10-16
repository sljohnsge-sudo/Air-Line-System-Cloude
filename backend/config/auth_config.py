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

    # ── Google / Microsoft Sign-In (Customer Portal) ─────────────────────────
    # Empty by default -- /api/customers/oauth/{provider}/start returns a
    # clear "not configured" error instead of attempting a broken redirect
    # until real values are added here. See services/customer_oauth_service.py.
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    MICROSOFT_CLIENT_ID: str = os.getenv("MICROSOFT_CLIENT_ID", "")
    MICROSOFT_CLIENT_SECRET: str = os.getenv("MICROSOFT_CLIENT_SECRET", "")
    # "common" accepts both personal Microsoft accounts and any work/school
    # (Azure AD) tenant -- the right default unless told to restrict to one org.
    MICROSOFT_TENANT: str = os.getenv("MICROSOFT_TENANT", "common")
    # This backend's own public base URL -- used to build the redirect_uri
    # Google/Microsoft send the browser back to after consent. Must exactly
    # match a Redirect URI registered in each provider's app console (see
    # setup docs) or the provider will refuse the exchange.
    OAUTH_REDIRECT_BASE: str = os.getenv("OAUTH_REDIRECT_BASE", "http://localhost:8000")
    # Where the browser lands (with the finished login token) once the
    # backend's own OAuth callback completes.
    FRONTEND_BASE_URL: str = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173")

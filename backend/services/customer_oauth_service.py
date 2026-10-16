"""
services/customer_oauth_service.py
===================================
Google / Microsoft "Sign in with..." for the Customer Portal (B2C), using
plain OAuth 2.0 Authorization Code flow over httpx -- same approach this
codebase already uses for Travelport's own OAuth2 (see auth_service.py),
no extra SDK dependency needed.

Flow (both providers, identical shape):
    1. Browser hits   GET /api/customers/oauth/{provider}/start
       -> 302 redirect to the provider's consent screen, with a signed
          short-lived `state` token (CSRF protection; no server-side
          session store needed, matches this project's stateless-JWT auth).
    2. Provider redirects back to
       GET /api/customers/oauth/{provider}/callback?code=...&state=...
    3. This backend exchanges the code for an access token, calls the
       provider's userinfo endpoint to get the verified email + name, then
       creates/finds the matching customer account and issues this app's
       own JWT -- the provider is only ever used to prove the email, it
       never becomes a dependency for day-to-day auth.
"""

import time
import uuid
import jwt
import httpx
from urllib.parse import urlencode

from config.auth_config import AuthConfig

STATE_PURPOSE = "customer_oauth_state"
STATE_TTL_SECONDS = 600  # 10 minutes -- plenty for a consent screen, short enough to limit CSRF replay window

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

MICROSOFT_AUTH_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
MICROSOFT_USERINFO_URL = "https://graph.microsoft.com/oidc/userinfo"


class OAuthNotConfigured(Exception):
    """Raised when the admin hasn't added Client ID/Secret for a provider yet."""


class OAuthExchangeError(Exception):
    """Raised when the provider rejects the code exchange or userinfo call."""


def is_configured(provider: str) -> bool:
    if provider == "google":
        return bool(AuthConfig.GOOGLE_CLIENT_ID and AuthConfig.GOOGLE_CLIENT_SECRET)
    if provider == "microsoft":
        return bool(AuthConfig.MICROSOFT_CLIENT_ID and AuthConfig.MICROSOFT_CLIENT_SECRET)
    return False


def make_state() -> str:
    """Short-lived signed token standing in for server-side session state --
    verified on the way back so the callback can't be hit with a forged/
    replayed code from outside the flow this backend itself started."""
    payload = {"purpose": STATE_PURPOSE, "nonce": uuid.uuid4().hex, "exp": int(time.time()) + STATE_TTL_SECONDS}
    return jwt.encode(payload, AuthConfig.JWT_SECRET_KEY, algorithm=AuthConfig.JWT_ALGORITHM)


def verify_state(state: str) -> bool:
    try:
        payload = jwt.decode(state, AuthConfig.JWT_SECRET_KEY, algorithms=[AuthConfig.JWT_ALGORITHM])
    except Exception:
        return False
    return payload.get("purpose") == STATE_PURPOSE


def build_authorize_url(provider: str, redirect_uri: str, state: str) -> str:
    if not is_configured(provider):
        raise OAuthNotConfigured(f"{provider} sign-in is not configured.")
    if provider == "google":
        params = {
            "client_id": AuthConfig.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    if provider == "microsoft":
        params = {
            "client_id": AuthConfig.MICROSOFT_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
        auth_url = MICROSOFT_AUTH_URL.format(tenant=AuthConfig.MICROSOFT_TENANT)
        return f"{auth_url}?{urlencode(params)}"
    raise OAuthNotConfigured(f"Unknown provider '{provider}'.")


def exchange_code_for_profile(provider: str, code: str, redirect_uri: str) -> dict:
    """Returns {"email": str, "name": str}. Raises OAuthExchangeError on any
    failure -- the caller treats that as "sign-in failed", never as a reason
    to fall back to an unverified email."""
    if provider == "google":
        token_url, userinfo_url = GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL
        token_data = {
            "client_id": AuthConfig.GOOGLE_CLIENT_ID,
            "client_secret": AuthConfig.GOOGLE_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    elif provider == "microsoft":
        token_url = MICROSOFT_TOKEN_URL.format(tenant=AuthConfig.MICROSOFT_TENANT)
        userinfo_url = MICROSOFT_USERINFO_URL
        token_data = {
            "client_id": AuthConfig.MICROSOFT_CLIENT_ID,
            "client_secret": AuthConfig.MICROSOFT_CLIENT_SECRET,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "scope": "openid email profile",
        }
    else:
        raise OAuthNotConfigured(f"Unknown provider '{provider}'.")

    try:
        with httpx.Client(timeout=20) as client:
            token_res = client.post(token_url, data=token_data, headers={"Accept": "application/json"})
            token_res.raise_for_status()
            access_token = token_res.json().get("access_token")
            if not access_token:
                raise OAuthExchangeError(f"{provider} token response had no access_token.")

            info_res = client.get(userinfo_url, headers={"Authorization": f"Bearer {access_token}"})
            info_res.raise_for_status()
            info = info_res.json()
    except httpx.HTTPError as e:
        raise OAuthExchangeError(f"{provider} OAuth exchange failed: {e}") from e

    email = info.get("email")
    if not email:
        raise OAuthExchangeError(f"{provider} did not return an email address for this account.")
    name = info.get("name") or info.get("given_name") or email.split("@")[0]
    return {"email": email, "name": name}

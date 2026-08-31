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

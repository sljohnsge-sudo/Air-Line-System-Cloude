"""
seed_admin.py
=============
One-off script: creates the single admin account from ADMIN_USERNAME /
ADMIN_PASSWORD in .env. Not run automatically, not part of init_db() —
run manually once:

    python seed_admin.py
"""

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

import database
import auth

def main():
    username = os.getenv("ADMIN_USERNAME")
    password = os.getenv("ADMIN_PASSWORD")
    if not username or not password:
        print("ADMIN_USERNAME / ADMIN_PASSWORD not set in .env — aborting.")
        return

    password_hash = auth.hash_password(password)
    created = database.create_admin_if_not_exists(username, password_hash, full_name="Administrator")
    if created:
        print(f"Admin account '{username}' created.")
    else:
        print(f"Admin account '{username}' already exists — no changes made.")


if __name__ == "__main__":
    main()

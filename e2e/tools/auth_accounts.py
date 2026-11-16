"""Tick and Book - temporary accounts for the sign-in tests (16/11/2026).

LAPTOP ONLY (local MySQL). Creates one test customer and five staff accounts
(Admin, Staff, Analytics, Staff + Analytics, and one disabled account) with
random passwords, writes them to e2e/.auth-accounts.json (git-ignored, never
committed), and deletes them again afterwards:

    backend\\venv\\Scripts\\python.exe e2e\\tools\\auth_accounts.py create
    ... run the Playwright tests ...
    backend\\venv\\Scripts\\python.exe e2e\\tools\\auth_accounts.py delete

All names start with "e2e_" / "e2e-auth-", so they are easy to spot and the
delete step removes every one, also from an earlier run that stopped half way.
Never run this against the server's database.
"""
import json
import os
import pathlib
import secrets
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
OUT = ROOT / "e2e" / ".auth-accounts.json"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # backend/.env and its relative paths

import auth  # noqa: E402
import database  # noqa: E402
from services import admin_db  # noqa: E402

ROLES = ("admin", "staff", "analytics", "staff_analytics")


def delete() -> None:
    n_staff = admin_db._write("DELETE FROM admins WHERE username LIKE 'e2e\\_%'")
    conn = database.get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM customers WHERE email LIKE 'e2e-auth-%@example.com'")
    n_cust = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    OUT.unlink(missing_ok=True)
    print(f"Deleted {n_staff} test staff account(s) and {n_cust} test customer(s).")


def create() -> None:
    delete()  # never leave two sets
    tag = secrets.token_hex(3)
    accounts = {"created_at": datetime.now().isoformat(timespec="seconds"), "staff": {}}
    for role in ROLES + ("disabled",):
        username = f"e2e_{role}_{tag}"
        password = secrets.token_urlsafe(18)
        new_id = admin_db.create_user(username, auth.hash_password(password), f"E2E test {role}",
                                      "staff" if role == "disabled" else role, "e2e-test")
        if role == "disabled":
            admin_db.update_user(new_id, "e2e-test", active=0)
        accounts["staff"][role] = {"username": username, "password": password, "id": new_id}
    email = f"e2e-auth-{tag}@example.com"
    password = secrets.token_urlsafe(18)
    customer = database.create_customer(email, auth.hash_password(password), "E2E Test Customer", None)
    accounts["customer"] = {"email": email, "password": password, "id": customer["id"]}
    OUT.write_text(json.dumps(accounts, indent=1), encoding="utf-8")
    print(f"Created 5 test staff accounts (e2e_*_{tag}) and 1 test customer ({email}); details in {OUT.name}.")


if __name__ == "__main__":
    {"create": create, "delete": delete}[sys.argv[1] if len(sys.argv) > 1 else "create"]()

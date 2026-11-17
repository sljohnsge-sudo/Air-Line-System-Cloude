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

17/11/2026: also a second test customer ("payer") with five test bookings
for 07-unpaid-bookings.spec.mjs. Their PNRs are made up (ZZ...), exist in no
Travelport system, and nothing is ever paid or ticketed for them:
  unpaid    held, no ticket            -> "Awaiting payment", PNR hidden
  expired   cancelled, no ticket       -> "Not paid - expired"
  ticketed  saved with a test ticket number and its loyalty points
  voided    the same, then cancelled through database.record_cancellation
            (the real cancellation step), which takes the points back
  sync      held, no ticket - for the live check at sign-in (Travelport
            answers that the PNR does not exist -> saved as Cancelled)
The test ticketed bookings are marked as already sent to the ERP
("E2E-TEST-NOT-SENT") so the ERP backfill never sends them.
"""
import json
import os
import pathlib
import secrets
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
OUT = ROOT / "e2e" / ".auth-accounts.json"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # backend/.env and its relative paths

import auth  # noqa: E402
import database  # noqa: E402
from services import admin_db  # noqa: E402

ROLES = ("admin", "staff", "analytics", "staff_analytics")


TEST_EMAIL = "e2e-auth-%@example.com"


def delete() -> None:
    n_staff = admin_db._write("DELETE FROM admins WHERE username LIKE 'e2e\\_%'")
    conn = database.get_db_connection()
    cur = conn.cursor()
    # Test bookings: made-up ZZ PNRs of the test customers only.
    cur.execute("SELECT locator_code FROM bookings WHERE locator_code LIKE 'ZZ%%' AND passenger_email LIKE %s", (TEST_EMAIL,))
    locators = [r[0] for r in cur.fetchall()]
    for loc in locators:
        cur.execute("DELETE FROM cancellation_requests WHERE booking_locator=%s", (loc,))
        cur.execute("DELETE FROM bookings WHERE locator_code=%s", (loc,))
    cur.execute("DELETE FROM loyalty_transactions WHERE email LIKE %s", (TEST_EMAIL,))
    cur.execute("DELETE FROM loyalty_accounts WHERE email LIKE %s", (TEST_EMAIL,))
    cur.execute("DELETE FROM customers WHERE email LIKE %s", (TEST_EMAIL,))
    n_cust = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    OUT.unlink(missing_ok=True)
    print(f"Deleted {n_staff} test staff account(s), {n_cust} test customer(s) and {len(locators)} test booking(s).")


def _test_bookings(customer_id: int, email: str, tag: str) -> dict:
    """The payer's five test bookings (see the top of this file)."""
    from services import customer_booking_sync, loyalty_service
    dep = datetime.now() + timedelta(days=30)
    out = {}
    for key, status, ticket in (("unpaid", "Confirmed", ""), ("expired", "Cancelled", ""), ("ticketed", "Ticketed", "9990000000001"),
                                ("voided", "Ticketed", "9990000000002"), ("sync", "Confirmed", "")):
        loc = f"ZZ{tag[:3].upper()}{key[0].upper()}"
        database.save_booking({
            "locator_code": loc, "pnr": loc, "ticket_number": ticket, "status": status,
            "passenger_name": "E2E/TEST MR", "email": email, "customer_id": customer_id, "passport_number": "",
            "flight_number": "UL 225", "airline": "SriLankan Airlines", "airline_code": "UL",
            "departure_airport": "CMB", "arrival_airport": "DXB",
            "departure_time": dep.strftime("%Y-%m-%dT%H:%M:00"), "arrival_time": (dep + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:00"),
            "cabin_class": "Economy", "total_fare": 12345.0, "currency": "LKR", "fare_source": "GDS",
            "airline_pnr": f"UL_{loc[::-1]}",
        })
        if ticket:
            conn = database.get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE bookings SET erp_invoice_name='E2E-TEST-NOT-SENT' WHERE locator_code=%s", (loc,))
            conn.commit()
            cur.close()
            conn.close()
            loyalty_service.award_points_for_booking(email, 12345.0, "LKR", loc)
        if key == "voided":
            database.record_cancellation(loc, "Voided", "E2E test (no Travelport)", None, None, {"test": True})
        booking = database.get_booking_by_locator(loc)
        out[key] = {"locator": loc, "pay_ref": customer_booking_sync.pay_ref(booking), "ticket": ticket or None,
                    "points": database.loyalty_points_for_booking(loc), "airline_pnr": f"UL_{loc[::-1]}"}
    return out


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
    payer_email = f"e2e-auth-{tag}-payer@example.com"
    payer_password = secrets.token_urlsafe(18)
    payer = database.create_customer(payer_email, auth.hash_password(payer_password), "E2E Test Payer", None)
    accounts["payer"] = {"email": payer_email, "password": payer_password, "id": payer["id"],
                         "bookings": _test_bookings(payer["id"], payer_email, tag)}
    OUT.write_text(json.dumps(accounts, indent=1), encoding="utf-8")
    print(f"Created 5 test staff accounts (e2e_*_{tag}), 2 test customers ({email}, {payer_email}) "
          f"and 5 test bookings (ZZ{tag[:3].upper()}*); details in {OUT.name}.")


if __name__ == "__main__":
    {"create": create, "delete": delete}[sys.argv[1] if len(sys.argv) > 1 else "create"]()

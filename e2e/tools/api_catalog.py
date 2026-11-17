"""Tick and Book - list every backend API for the Playwright API tests
(e2e/tests/06-api.spec.mjs) (17/11/2026).

    py e2e/tools/api_catalog.py [http://localhost:8005]

Reads the API list (OpenAPI) from the LOCAL customer-site backend (the dev
server on 8005 has it; the servers don't publish it) and each API's sign-in
rule from backend/main.py, and writes e2e/data/api-catalog.json. Run it again
after adding or changing an API.

What the tests do with each API (never a booking, payment or ticket):
  read     GET: called without sign-in, then signed in with a temporary test
           account if it needs one; the reply is recorded.
  guard    needs a sign-in and changes something: called WITHOUT sign-in only,
           must be refused (401) before it does anything.
  invalid  public and changes/searches something: called with invalid input
           only, must be refused (422) before it does anything.
  never    booking, payment, ticketing, cancelling or a payment-gateway step
           open to the public: never called (a person tests it by hand).
"""
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8005"
api = json.load(urllib.request.urlopen(f"{base}/openapi.json", timeout=20))
src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8").split("\n")
# Routers mounted by main.py (app.include_router) - their routes too.
for extra in ("backend/services/hotel_checkout_api.py",):
    src += (ROOT / extra).read_text(encoding="utf-8").split("\n")

# ---- sign-in rule of each API, from its FastAPI dependencies ------------------
AUTH = {
    "get_current_admin": "staff", "get_current_analytics_user": "analytics",
    "get_current_customer": "customer", "get_admin_or_customer": "staff-or-customer",
    "get_optional_customer_id": "optional", "get_optional_principal": "optional",
}
auth_of, func_of = {}, {}
for i, line in enumerate(src):
    m = re.match(r'^@(?:app|router)\.(get|post|put|delete|patch)\("([^"]+)"', line)
    if not m:
        continue
    j = i + 1
    while not src[j].lstrip().startswith(("def ", "async def ")):
        j += 1
    func_of[(m.group(1).upper(), m.group(2))] = re.search(r"def (\w+)", src[j]).group(1)
    sig = ""
    while True:
        sig += src[j]
        if re.search(r"\)\s*(->.*)?:\s*$", src[j]):
            break
        j += 1
    kinds = [AUTH[d.split(".")[-1]] for d in re.findall(r"Depends\(([\w\.]+)\)", sig) if d.split(".")[-1] in AUTH]
    # A route with a staff sign-in among its rules is a staff route.
    auth_of[(m.group(1).upper(), m.group(2))] = "staff" if "staff" in kinds else (kinds[0] if kinds else "none")

# ---- which site serves it (same rules as main.py's _enforce_site_split) --------
STAFF = [r"^/api/admin(/|$)", r"^/api/analytics(/|$)", r"^/api/bookings/history$",
         r"^/api/bookings/[A-Za-z0-9{}_]+/cancel(/quote)?$", r"^/api/invoice/(report|price-raw/.+)$",
         r"^/api/payments/complete$", r"^/api/amadeus-bookings/[A-Za-z0-9{}_-]+/issue-ticket$",
         r"^/api/hotels/(history|retrieve/[A-Za-z0-9{}_]+|[A-Za-z0-9{}_]+/cancel)$",
         r"^/api/hotels/(book|book-offer)$"]  # booking without payment: staff only (17/11/2026)
SHARED = [r"^/$", r"^/api/bookings/retrieve/", r"^/api/invoice/(pnr|ticket)/", r"^/api/reference/",
          r"^/api/feature-flags$", r"^/api/site-content$", r"^/api/terminal-connections$"]


def site_of(path):
    if any(re.match(p, path) for p in STAFF):
        return "admin"
    if any(re.match(p, path) for p in SHARED):
        return "both"
    return "website"


# ---- never called: public booking / payment / ticketing / cancel steps ---------
NEVER = re.compile(r"/payments/|/manage-booking/|/(book|book-offer|book-request|bookings/create|"
                   r"bookings/confirm|bookings/initiate|confirm|hold|issue-ticket|pay-ticket|offline-start|"
                   r"instant-pay|cancel|cancellation-requests|price|price-with-ancillaries|seatmap|"
                   r"checkout)(/|$)")  # checkout = hotel card payment (17/11/2026)
NEVER_GET = re.compile(r"^/api/payments/")  # gateway redirect / payment completion

SAMPLE = {"provider": "google", "code": "UL", "country": "LK", "ticket_number": "0000000000000",
          "chain_code": "ZZ", "direction": "up", "email": "e2e-nobody@example.com", "nationality": "LK",
          "destination": "AE", "date": "2026-11-27", "start_date": "2026-01-01", "end_date": "2026-01-01",
          "query": "ZZTEST", "numbers": "0000000000000", "reqid": "zztest", "supplier_locator": "ZZTEST"}


def sample(name, schema):
    if name in SAMPLE:
        return SAMPLE[name]
    t = (schema or {}).get("type") or next((s.get("type") for s in (schema or {}).get("anyOf", []) if s.get("type") != "null"), "string")
    return 999999999 if t == "integer" else "ZZTEST"


entries = []
for path, ops in api["paths"].items():
    for meth, op in ops.items():
        method = meth.upper()
        auth = auth_of.get((method, path), "none")
        params = op.get("parameters", [])
        url = path
        for p in params:
            if p["in"] == "path":
                url = url.replace("{" + p["name"] + "}", str(sample(p["name"], p.get("schema"))))
        query = {p["name"]: sample(p["name"], p.get("schema")) for p in params if p["in"] == "query" and p.get("required")}
        public = auth in ("none", "optional")
        if method == "GET" and NEVER_GET.search(path):
            # Payment APIs: public ones are never called; staff ones are only
            # checked to refuse without sign-in, never called signed in.
            action = "never" if public else "guard"
        elif method == "GET":
            action = "read"
        elif not public:
            action = "guard"
        elif NEVER.search(path) or not op.get("requestBody"):
            action = "never"
        else:
            action = "invalid"
        reason = {
            "read": "read only",
            "guard": "changes data or reaches the payment gateway: only checked that it is refused without "
                     "sign-in, never called signed in",
            "invalid": "only checked that invalid input is refused before anything happens",
            "never": "booking / payment / ticketing / cancelling step open to the public: never called by "
                     "tests (Tick and Book rule), tested by a person by hand",
        }[action]
        entries.append({
            "method": method, "path": path, "url": url, "query": query,
            "name": op.get("summary") or func_of.get((method, path), ""),
            "function": func_of.get((method, path), ""), "auth": auth, "site": site_of(path),
            "body": bool(op.get("requestBody")), "action": action, "why": reason,
            "area": (path.split("/")[2] if path.startswith("/api/") and len(path.split("/")) > 2 else "root"),
        })

entries.sort(key=lambda e: (e["area"], e["path"], ["GET", "POST", "PUT", "PATCH", "DELETE"].index(e["method"])))
out = ROOT / "e2e" / "data" / "api-catalog.json"
out.write_text(json.dumps({"source": f"{base}/openapi.json + backend/main.py", "count": len(entries),
                           "apis": entries}, indent=1), encoding="utf-8")
from collections import Counter
print(f"{len(entries)} APIs -> {out}")
print("actions:", dict(Counter(e["action"] for e in entries)))
print("sites:  ", dict(Counter(e["site"] for e in entries)))
print("sign-in:", dict(Counter(e["auth"] for e in entries)))
print("never called:", ", ".join(f"{e['method']} {e['path']}" for e in entries if e["action"] == "never"))

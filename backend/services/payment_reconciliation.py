"""
Card payment reconciliation: the CyberSource (NDB) transaction export against
our bookings, seats/extras payments and ERPNext invoices.

Finance downloads Transaction Management → Transactions → Export (JSON or CSV,
or the CSV saved as Excel) and uploads it in the admin portal. Each CyberSource
row is one request: most are 3-D Secure steps (setup, challenge) rather than
money. A row is a sale only when "Card Payments - Authorization" succeeded;
it is settled (sent to the bank) when "Card Payments - Settlement" succeeded.

Every sale is matched to our own payment record (CyberSource request id =
cybersource_payments.response_json.transaction_id) and to what it paid for —
a booking (Merchant Reference Number = PNR) or a seats/extras payment (ADD…) —
and checked: same amount and currency, ticketed / extras issued, ERPNext
invoice made, settled, not charged twice. The reverse is checked too: payments
our system accepted, and card-paid tickets, within the file's dates that the
file doesn't have. Read-only against bookings/ERP; each run is kept in its own
table (payment_recon_runs, created on first use).

A later step (REST key) fetches the same rows from CyberSource's Transaction
Search API instead of a file; the matching here stays the same.
"""
import csv
import io
import json
import logging
import re
import threading
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from xml.etree import ElementTree

import database

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 30 * 1024 * 1024
AMOUNT_TOLERANCE = 0.5

# ── Reading the export ───────────────────────────────────────────────────────

_FIELD_ALIASES = {
    "request_id": ("requestid", "request id", "transaction id", "id"),
    "reference": ("referencenumber", "merchant reference number", "merchant ref number", "reference number",
                  "merchant reference no", "reference"),
    "date": ("date", "transaction date", "request date", "submit time", "date sortable"),
    "amount": ("amount", "total amount", "grand total amount"),
    "currency": ("currency", "currency code"),
    "applications": ("applications", "application", "applications status"),
    "card_type": ("paymentmethod", "payment method", "card type", "account type"),
    "card_suffix": ("accountsuffix", "account suffix", "card suffix", "last 4"),
    "auth_code": ("authorizationcode", "authorization code", "auth code"),
    "email": ("email", "email address"),
    "name_first": ("firstname", "first name", "billing first name"),
    "name_last": ("lastname", "last name", "billing last name"),
    "reason_code": ("reasoncode", "reason code"),
    "rrn": ("retrievalreferencenumber", "retrieval reference number", "rrn"),
}

_APP_STATUS_RE = re.compile(
    r"(Success|Failed|Not Run|Challenge|Rejected|Declined|Review|Error|Pending|Accept|Reject)\s+([A-Za-z][^,;|\n]*)", re.I)


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lower().replace("_", " "))


def _map_row(row: dict) -> dict:
    keys = {_norm_header(k): k for k in row}
    out = {}
    for field, aliases in _FIELD_ALIASES.items():
        for a in aliases:
            if a in keys:
                out[field] = row[keys[a]]
                break
    return out


def _xlsx_rows(data: bytes) -> list[dict]:
    """First worksheet of an .xlsx as dicts (header row = first row), using
    only the standard library."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
        root = ElementTree.fromstring(z.read(sheet))
    table = []
    for r in root.iter(f"{{{ns['m']}}}row"):
        cells = {}
        for c in r.findall("m:c", ns):
            col = re.match(r"[A-Z]+", c.get("r", "A")).group(0)
            idx = 0
            for ch in col:
                idx = idx * 26 + (ord(ch) - 64)
            v = c.find("m:v", ns)
            if c.get("t") == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
            elif v is None:
                text = ""
            elif c.get("t") == "s":
                text = shared[int(v.text)]
            else:
                text = v.text or ""
            cells[idx - 1] = text
        if cells:
            table.append([cells.get(i, "") for i in range(max(cells) + 1)])
    if not table:
        return []
    header = table[0]
    return [{header[i]: (row[i] if i < len(row) else "") for i in range(len(header))} for row in table[1:]]


def _apps_from(value) -> dict:
    """{'card payments - authorization': 'success', ...}"""
    apps = {}
    if isinstance(value, list):
        for a in value:
            if isinstance(a, dict):
                apps[(a.get("displayName") or a.get("name") or "").strip().lower()] = (a.get("status") or "").strip().lower()
        return apps
    for status, name in _APP_STATUS_RE.findall(str(value or "")):
        apps[name.strip().lower()] = status.strip().lower()
    return apps


def _parse_date(value):
    s = str(value or "").strip()
    if not s:
        return None
    s = re.sub(r"\s+(IST|UTC|GMT|[A-Z]{2,4})$", "", s)
    for fmt in ("%b %d %Y %I:%M:%S %p", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M"):
        for candidate in (s, s[:19]):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    try:  # Excel serial date
        return datetime(1899, 12, 30) + timedelta(days=float(s))
    except ValueError:
        return None


def _amount(value):
    try:
        return round(float(str(value).replace(",", "").strip()), 2) if str(value or "").strip() else None
    except ValueError:
        return None


def classify(apps: dict) -> str:
    """What one CyberSource row is: sale_settled / sale_authorised / declined
    / refund / reversal / step (3-D Secure setup or challenge, not money)."""
    def st(*words):
        for name, status in apps.items():
            if all(w in name for w in words):
                return status
        return None
    if st("credit") == "success":
        return "refund"
    if st("reversal") == "success":
        return "reversal"
    auth = st("authorization")
    if auth == "success":
        return "sale_settled" if st("settlement") == "success" else "sale_authorised"
    if auth in ("failed", "declined", "rejected", "reject", "error"):
        return "declined"
    return "step"


def parse_export(filename: str, data: bytes) -> list[dict]:
    """Any CyberSource transaction export (JSON / CSV / .xlsx) as normalised rows."""
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("The file is larger than 30 MB — export a shorter date range.")
    name = (filename or "").lower()
    if name.endswith(".json") or data.lstrip()[:1] in (b"[", b"{"):
        raw = json.loads(data.decode("utf-8-sig"))
        if isinstance(raw, dict):
            raw = next((v for v in raw.values() if isinstance(v, list)), [])
    elif name.endswith(".xlsx"):
        raw = _xlsx_rows(data)
    elif name.endswith(".xls"):
        raise ValueError("Old Excel (.xls) files can't be read — save it as .xlsx or CSV, or upload the JSON export.")
    else:
        text = data.decode("utf-8-sig", errors="replace")
        raw = list(csv.DictReader(io.StringIO(text)))
    rows = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        m = _map_row(r)
        if not m.get("request_id"):
            continue
        apps = _apps_from(m.get("applications"))
        name_ = " ".join(x for x in (m.get("name_first"), m.get("name_last")) if x and str(x).strip()) or None
        rows.append({
            "request_id": str(m["request_id"]).strip(),
            "reference": str(m.get("reference") or "").strip().upper() or None,
            "date": _parse_date(m.get("date")),
            "amount": _amount(m.get("amount")),
            "currency": (str(m.get("currency") or "").strip().upper() or None),
            "kind": classify(apps),
            "card": " ".join(x for x in (str(m.get("card_type") or "").strip(),
                                         f"…{m['card_suffix']}" if m.get("card_suffix") else "") if x) or None,
            "auth_code": m.get("auth_code") or None,
            "payer": name_,
            "email": m.get("email") or None,
            "rrn": m.get("rrn") or None,
        })
    if not rows:
        raise ValueError("No CyberSource transactions found in this file. Upload the Transactions export "
                         "(JSON or CSV) from Transaction Management.")
    return rows


# ── Matching ─────────────────────────────────────────────────────────────────

ISSUE_TEXT = {
    "not_in_system": "Paid at CyberSource, but our system has no record of this payment",
    "no_booking": "Paid, but no booking or extras payment with this reference",
    "amount_mismatch": "Amount differs from the booking",
    "not_ticketed": "Paid, but the booking is not ticketed",
    "extras_not_issued": "Paid for seats/extras, but the airline did not issue them",
    "no_invoice": "Not in ERPNext (no invoice)",
    "not_settled": "Authorised but not settled (not sent to the bank)",
    "duplicate": "Charged more than once for the same reference",
    "missing_from_cybersource": "Our system recorded this payment, but it is not in the CyberSource file",
    "ticketed_no_payment": "Ticketed (card), but no card payment in the CyberSource file",
}


def _iso(v):
    if v is None or v == "":
        return None
    return v.isoformat(sep=" ") if hasattr(v, "isoformat") else str(v)


def _q(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def _in(values):
    return ",".join(["%s"] * len(values))


def reconcile(rows: list[dict]) -> dict:
    sales = [r for r in rows if r["kind"] in ("sale_settled", "sale_authorised")]
    refunds = [r for r in rows if r["kind"] in ("refund", "reversal")]
    dates = [r["date"] for r in rows if r["date"]]
    period_from = min(dates) if dates else None
    period_to = max(dates) if dates else None

    refs = sorted({r["reference"] for r in sales + refunds if r["reference"]})
    ids = [r["request_id"] for r in sales]
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        ours_by_tx, ours_rows = {}, []
        if period_from:
            ours_rows = _q(cur, "SELECT reqid, client_ref, amount, currency, status, created_at, completed_at, "
                                "JSON_UNQUOTE(JSON_EXTRACT(response_json,'$.transaction_id')) tx "
                                "FROM cybersource_payments WHERE created_at BETWEEN %s AND %s",
                           (period_from - timedelta(days=1), period_to + timedelta(days=1)))
        if ids:
            ours_rows += _q(cur, f"SELECT reqid, client_ref, amount, currency, status, created_at, completed_at, "
                                 f"JSON_UNQUOTE(JSON_EXTRACT(response_json,'$.transaction_id')) tx "
                                 f"FROM cybersource_payments WHERE JSON_UNQUOTE(JSON_EXTRACT(response_json,'$.transaction_id')) "
                                 f"IN ({_in(ids)})", ids)
        for o in ours_rows:
            if o["tx"] and o["tx"] != "null":
                ours_by_tx[o["tx"]] = o
        bookings = {}
        addons = {}
        linked = {}  # payment reference -> PNR (NDC pays before the PNR exists, under a one-off code)
        if refs:
            for c in _q(cur, f"SELECT client_ref, locator_code FROM consumed_payment_reqids "
                             f"WHERE UPPER(client_ref) IN ({_in(refs)}) AND locator_code IS NOT NULL", refs):
                linked[c["client_ref"].upper()] = c["locator_code"].upper()
            for a in _q(cur, f"SELECT addon_id, locator_code, amount, currency, status, erp_invoice_name, erp_sync_error "
                             f"FROM addon_payments WHERE addon_id IN ({_in(refs)})", refs):
                addons[a["addon_id"].upper()] = a
            locs = sorted(set(refs) | set(linked.values()) | {a["locator_code"].upper() for a in addons.values()})
            for b in _q(cur, f"SELECT locator_code, status, ticket_number, total_fare, currency, passenger_name, "
                             f"erp_invoice_name, erp_sync_error FROM bookings WHERE locator_code IN ({_in(locs)})", locs):
                bookings[b["locator_code"].upper()] = b
        # Refund requests (automatic ones name the CyberSource request id in
        # the note; NDC ones without a PNR are filed as "NDC-<code>") and
        # ticketing exceptions (payment_txn_ref = CyberSource request id).
        refund_reqs = defaultdict(list)
        exceptions = defaultdict(list)
        if period_from:
            for c in _q(cur, "SELECT id, booking_locator, status, admin_note FROM cancellation_requests "
                             "WHERE requested_at >= %s", (period_from - timedelta(days=1),)):
                label = f"Refund #{c['id']} {c['status']}"
                refund_reqs[(c["booking_locator"] or "").upper()].append(label)
                for tx in re.findall(r"\d{20,24}", c["admin_note"] or ""):
                    refund_reqs[tx].append(label)
            for x in _q(cur, "SELECT id, locator_code, payment_txn_ref, status FROM stuck_booking_notifications "
                             "WHERE created_at >= %s AND payment_txn_ref IS NOT NULL", (period_from - timedelta(days=1),)):
                exceptions[x["payment_txn_ref"]].append(f"Exception #{x['id']} {x['status']}")
        card_tickets = []
        if period_from:
            card_tickets = _q(cur, "SELECT locator_code, booking_date, total_fare, currency, passenger_name, status "
                                   "FROM bookings WHERE payment_method='Credit Card' AND ticket_number IS NOT NULL "
                                   "AND ticket_number<>'' AND booking_date BETWEEN %s AND %s",
                              (period_from, period_to))
    finally:
        cur.close()
        conn.close()

    sale_count = defaultdict(int)
    for s in sales:
        if s["reference"]:
            sale_count[s["reference"]] += 1

    lines = []
    for s in sales:
        ref = s["reference"] or ""
        issues = []
        ours = ours_by_tx.get(s["request_id"])
        if not ours:
            issues.append("not_in_system")
        what, expected, exp_cur, invoice, state, locator, passenger = None, None, None, None, None, None, None
        if ref.startswith("ADD") and ref in addons:
            a = addons[ref]
            what, expected, exp_cur, invoice, locator = "Seats/extras", a["amount"], a["currency"], a["erp_invoice_name"], a["locator_code"]
            state = a["status"]
            if a["status"] != "completed":
                issues.append("extras_not_issued")
            if locator and locator.upper() in bookings:
                passenger = bookings[locator.upper()]["passenger_name"]
        elif (linked.get(ref) or ref) in bookings:
            locator = linked.get(ref) or ref
            b = bookings[locator]
            what, expected, exp_cur, invoice = "Flight booking", b["total_fare"], b["currency"], b["erp_invoice_name"]
            state, passenger = b["status"], b["passenger_name"]
            if not b["ticket_number"]:
                issues.append("not_ticketed")
        else:
            issues.append("no_booking")
        if expected is not None and s["amount"] is not None:
            if abs(float(expected) - s["amount"]) > AMOUNT_TOLERANCE or (exp_cur and s["currency"] and exp_cur != s["currency"]):
                issues.append("amount_mismatch")
        if what and not invoice and not ({"not_ticketed", "extras_not_issued"} & set(issues)):
            issues.append("no_invoice")  # nothing was issued, so nothing to invoice
        if s["kind"] == "sale_authorised":
            issues.append("not_settled")
        if ref and sale_count[ref] > 1:
            issues.append("duplicate")
        lines.append({
            "source": "cybersource", "date": s["date"].isoformat(sep=" ") if s["date"] else None,
            "request_id": s["request_id"], "reference": ref or None, "locator": locator, "what": what,
            "passenger": passenger or s["payer"], "card": s["card"], "auth_code": s["auth_code"],
            "amount": s["amount"], "currency": s["currency"],
            "expected_amount": float(expected) if expected is not None else None, "expected_currency": exp_cur,
            "booking_status": state, "erp_invoice": invoice,
            "settled": s["kind"] == "sale_settled",
            "refund_requests": sorted(set(refund_reqs.get(s["request_id"], []) + refund_reqs.get((locator or "").upper(), [])
                                          + refund_reqs.get(f"NDC-{ref[:8]}", []) + exceptions.get(s["request_id"], []))),
            "issues": issues, "status": "matched" if not issues else "problem",
        })

    # Our side, missing from the file (only within the file's dates).
    file_ids = {s["request_id"] for s in sales}
    paid_refs = {linked.get(s["reference"], s["reference"]) for s in sales if s["reference"]}
    if period_from:
        seen = set()
        for o in ours_rows:
            if o["status"] != "accept" or not o["tx"] or o["tx"] in file_ids or o["tx"] in seen:
                continue
            when = o["completed_at"] or o["created_at"]
            if not (period_from <= when <= period_to):
                continue
            seen.add(o["tx"])
            lines.append({"source": "ours", "date": _iso(when), "request_id": o["tx"],
                          "reference": o["client_ref"], "locator": o["client_ref"], "what": None, "passenger": None,
                          "card": None, "auth_code": None, "amount": float(o["amount"]) if o["amount"] is not None else None,
                          "currency": o["currency"], "expected_amount": None, "expected_currency": None,
                          "booking_status": None, "erp_invoice": None, "settled": False, "refund_requests": [],
                          "issues": ["missing_from_cybersource"], "status": "problem"})
        addon_locators = {a["locator_code"] for a in addons.values()}
        for b in card_tickets:
            loc = b["locator_code"].upper()
            if loc in paid_refs:
                continue
            lines.append({"source": "ours", "date": _iso(b["booking_date"]),
                          "request_id": None, "reference": loc, "locator": loc, "what": "Flight booking",
                          "passenger": b["passenger_name"], "card": None, "auth_code": None, "amount": None,
                          "currency": None, "expected_amount": float(b["total_fare"]) if b["total_fare"] is not None else None,
                          "expected_currency": b["currency"], "booking_status": b["status"], "erp_invoice": None,
                          "settled": False, "refund_requests": [], "issues": ["ticketed_no_payment"],
                          "status": "problem", "note": "Extras paid on this booking" if loc in addon_locators else None})

    refund_lines = [{"date": r["date"].isoformat(sep=" ") if r["date"] else None, "request_id": r["request_id"],
                     "reference": r["reference"], "kind": r["kind"], "amount": r["amount"], "currency": r["currency"],
                     "refund_requests": refund_reqs.get(linked.get(r["reference"] or "", r["reference"] or "")) or []}
                    for r in refunds]

    lines.sort(key=lambda x: (x["status"] == "matched", x["date"] or ""), reverse=False)
    issue_counts = defaultdict(int)
    for ln in lines:
        for i in ln["issues"]:
            issue_counts[i] += 1
    totals = defaultdict(float)
    for s in sales:
        if s["amount"] is not None:
            totals[(s["currency"] or "?") + ("_settled" if s["kind"] == "sale_settled" else "_authorised")] += s["amount"]
    summary = {
        "rows_in_file": len(rows),
        "sales": len(sales),
        "settled": sum(1 for s in sales if s["kind"] == "sale_settled"),
        "declined": sum(1 for r in rows if r["kind"] == "declined"),
        "steps": sum(1 for r in rows if r["kind"] == "step"),
        "refunds": len(refunds),
        "matched": sum(1 for ln in lines if ln["status"] == "matched"),
        "problems": sum(1 for ln in lines if ln["status"] == "problem"),
        "issue_counts": dict(issue_counts),
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "period_from": period_from.isoformat(sep=" ") if period_from else None,
        "period_to": period_to.isoformat(sep=" ") if period_to else None,
    }
    return {"summary": summary, "lines": lines, "refunds": refund_lines, "issue_text": ISSUE_TEXT}


# ── Run history ──────────────────────────────────────────────────────────────

_ready = False
_lock = threading.Lock()


def _ensure_table():
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        conn = database.get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS payment_recon_runs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    file_name VARCHAR(255) NULL,
                    uploaded_by VARCHAR(120) NULL,
                    source VARCHAR(20) NOT NULL DEFAULT 'upload',
                    period_from DATETIME NULL,
                    period_to DATETIME NULL,
                    sales INT NULL,
                    matched INT NULL,
                    problems INT NULL,
                    summary_json TEXT NULL,
                    result_json LONGTEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            conn.commit()
            _ready = True
        finally:
            cur.close()
            conn.close()


def run_upload(filename: str, data: bytes, uploaded_by: str | None) -> dict:
    result = reconcile(parse_export(filename, data))
    s = result["summary"]
    _ensure_table()
    conn = database.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO payment_recon_runs (file_name, uploaded_by, source, period_from, period_to, sales,
                       matched, problems, summary_json, result_json) VALUES (%s,%s,'upload',%s,%s,%s,%s,%s,%s,%s)""",
                    ((filename or "")[:255], (uploaded_by or "")[:120] or None, s["period_from"], s["period_to"],
                     s["sales"], s["matched"], s["problems"], json.dumps(s), json.dumps(result, default=str)))
        conn.commit()
        result["run_id"] = cur.lastrowid
    finally:
        cur.close()
        conn.close()
    result["file_name"] = filename
    return result


def list_runs(limit: int = 50) -> list:
    _ensure_table()
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        rows = _q(cur, "SELECT id, file_name, uploaded_by, source, period_from, period_to, sales, matched, problems, "
                       "created_at FROM payment_recon_runs ORDER BY id DESC LIMIT %s", (limit,))
    finally:
        cur.close()
        conn.close()
    for r in rows:
        for k in ("period_from", "period_to", "created_at"):
            r[k] = r[k].isoformat(sep=" ") if r[k] else None
    return rows


def get_run(run_id: int) -> dict | None:
    _ensure_table()
    conn = database.get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        rows = _q(cur, "SELECT id, file_name, uploaded_by, created_at, result_json FROM payment_recon_runs WHERE id=%s",
                  (run_id,))
    finally:
        cur.close()
        conn.close()
    if not rows:
        return None
    r = rows[0]
    result = json.loads(r["result_json"])
    result.update({"run_id": r["id"], "file_name": r["file_name"], "uploaded_by": r["uploaded_by"],
                   "created_at": r["created_at"].isoformat(sep=" ") if r["created_at"] else None})
    return result


def run_csv(result: dict) -> str:
    """The run as a CSV for finance (opens in Excel)."""
    text = result.get("issue_text") or ISSUE_TEXT
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Result", "Date", "Reference", "PNR", "What", "Passenger / payer", "CyberSource request ID",
                "Card", "Auth code", "Paid amount", "Paid currency", "Booking amount", "Booking currency",
                "Booking status", "ERPNext invoice", "Settled", "Refund requests", "Problems"])
    for ln in result.get("lines") or []:
        w.writerow(["Matched" if ln["status"] == "matched" else "Check", ln.get("date"), ln.get("reference"),
                    ln.get("locator"), ln.get("what"), ln.get("passenger"), ln.get("request_id"), ln.get("card"),
                    ln.get("auth_code"), ln.get("amount"), ln.get("currency"), ln.get("expected_amount"),
                    ln.get("expected_currency"), ln.get("booking_status"), ln.get("erp_invoice"),
                    "Yes" if ln.get("settled") else "No", "; ".join(ln.get("refund_requests") or []),
                    "; ".join(text.get(i, i) for i in ln.get("issues") or [])])
    for r in result.get("refunds") or []:
        w.writerow(["Refund" if r["kind"] == "refund" else "Reversal", r.get("date"), r.get("reference"), None,
                    None, None, r.get("request_id"), None, None, r.get("amount"), r.get("currency"), None, None,
                    None, None, None, "; ".join(r.get("refund_requests") or []), None])
    return out.getvalue()

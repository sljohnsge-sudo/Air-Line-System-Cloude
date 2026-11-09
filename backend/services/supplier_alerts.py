"""
Admin → Operations → Supplier Alerts (table provider_notifications).

Each alert the flight search raised when Travelport (TP) or Amadeus (AD) sent
no fares back (main._notify_provider_failure) gets a type, a one-line summary,
the action to take, and its route and travel dates split out, for the table on
the admin page. Read-only: nothing here changes an alert or contacts a supplier.
"""
import re

SUPPLIERS = {"TP": "Travelport", "AD": "Amadeus"}

# kind -> (label for the filter chips, short label for the table's Type column)
KINDS = {
    "no_fares": ("No fares for the channel", "No fares"),
    "comm_error": ("Travelport communication error", "Comms error"),
    "connection": ("Connection dropped / timed out", "Connection"),
    "account": ("Account set-up (1586)", "Account set-up"),
    "ad_down": ("Amadeus service not reachable", "Amadeus offline"),
    "empty": ("No fares returned (no error)", "Empty result"),
    "other": ("Other", "Other"),
}

ACTIONS = {
    "no_fares": "Retry the search; if it repeats, ask Travelport about fares on this route",
    "comm_error": "Temporary at Travelport — retry the search; tell Travelport if it keeps happening",
    "connection": "Check the server's internet connection, then retry the search",
    "account": "Ask Travelport to check the TripServices access and PCC",
    "ad_down": "Amadeus is Phase 2 (not live yet) — nothing to do; acknowledge",
    "empty": "Check the route has flights that day; if it should, retry the search and tell the supplier",
    "other": "Retry the search; send the full message to Travelport if it repeats",
}

_LEG = re.compile(r"([A-Z]{3})\s*(?:→|->)\s*([A-Z]{3})\s+on\s+(\d{4}-\d{2}-\d{2})")


def classify(row: dict) -> str:
    m = (row.get("error_message") or "").upper()
    if "RETURNED NO FARES" in m:
        return "empty"
    if row.get("provider") == "AD":
        if "CONNECTION ATTEMPTS FAILED" in m or "BAD GATEWAY" in m or "CONNECT" in m:
            return "ad_down"
        return "other"
    if "NO OFFERS FOUND FOR THE CHANNEL" in m:
        return "no_fares"
    if "COMMUNICATION ERROR" in m:
        return "comm_error"
    if "1586" in m or "INVALID INPUT FORMAT" in m:
        return "account"
    if any(x in m for x in ("WINERROR", "ERRNO", "GETADDRINFO", "TIMED OUT", "FORCIBLY CLOSED", "CONNECTION")):
        return "connection"
    return "other"


def _tries(m: str) -> str:
    x = re.search(r"after (\d+) attempts", m)
    return f", after {x.group(1)} tries" if x else ""


def summary(kind: str, row: dict) -> str:
    """The message in one short line (the full text stays in error_message)."""
    m = row.get("error_message") or ""
    up = m.upper()
    if kind == "no_fares":
        return f"Travelport sent no fares for this search (NO OFFERS FOUND FOR THE CHANNEL){_tries(m)}"
    if kind == "comm_error":
        return f"Travelport communication error (COMMUNICATION ERROR. RETRY){_tries(m)}"
    if kind == "empty":
        return f"{SUPPLIERS.get(row.get('provider'), 'The supplier')} answered with no fares for this search — no error given"
    if kind == "account":
        return "Travelport refused the request format (1586) — account set-up"
    if kind == "connection":
        if "GETADDRINFO" in up:
            return "Couldn't find Travelport's address — internet or DNS problem on the server"
        if "FORCIBLY CLOSED" in up:
            return "Travelport closed the connection"
        if "DID NOT PROPERLY RESPOND" in up or "TIMED OUT" in up:
            return "Connection to Travelport timed out"
        return "Connection to Travelport failed"
    if kind == "ad_down":
        if "BAD GATEWAY" in up:
            return "The Amadeus search service answered 502 Bad Gateway"
        return "The Amadeus search service wasn't running"
    text = re.sub(r"^Travelport search error:\s*", "", m)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200] + ("…" if len(text) > 200 else "")


def _route(info: str | None) -> tuple[str | None, list]:
    """'CMB → DXB on 2026-10-15' or 'CMB->DXB on 2026-11-04 | DXB->CMB on 2026-11-11'
    -> ('CMB–DXB' / 'CMB–DXB–CMB', ['2026-11-04', '2026-11-11'])."""
    legs = _LEG.findall(info or "")
    if not legs:
        return None, []
    codes = [legs[0][0]]
    for origin, dest, _ in legs:
        if codes[-1] != origin:
            codes.append(origin)
        codes.append(dest)
    return "–".join(codes), [d for _, _, d in legs]


def review(rows: list) -> list:
    """Alerts with supplier name, type, labels, summary, action, route and
    travel dates added (order kept: newest first)."""
    out = []
    for row in rows:
        kind = classify(row)
        route, dates = _route(row.get("route_info"))
        out.append({**row, "supplier": SUPPLIERS.get(row.get("provider"), row.get("provider")),
                    "kind": kind, "kind_label": KINDS[kind][0], "kind_short": KINDS[kind][1],
                    "summary": summary(kind, row), "action": ACTIONS[kind],
                    "route": route, "travel_dates": dates})
    return out

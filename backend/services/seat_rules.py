"""
Airline seat-selection conditions, learned from the airlines' own Travelport
messages.

Travelport's seat map lists seats as "Available" with no condition attached;
the airline's rule only shows up when a Seat Book is refused, e.g.
"SEATING RANGE FOR QR 08 HOURS PRIOR TO DEPARTURE OR UP TO 21SEP". Every such
refusal is parsed and remembered here (config/seat_rules.json), so the next
customer on that carrier is told about the condition BEFORE choosing a seat.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_RULES_FILE = Path(__file__).resolve().parent.parent / "config" / "seat_rules.json"
_lock = Lock()

_RANGE_RE = re.compile(r"SEATING RANGE FOR\s+([A-Z0-9]{2})\s+(\d+)\s+HOURS PRIOR TO DEPARTURE", re.I)

# Departure times from Travelport carry no timezone. Only Sri Lankan airports
# (UTC+5:30) are converted to real "hours until departure"; for any other
# departure airport the rule is shown as information only.
_LK_AIRPORTS = {"CMB", "HRI", "JAF", "BTC", "TRR", "KCT", "RML"}
_LK_OFFSET = timedelta(hours=5, minutes=30)


def _load() -> dict:
    try:
        return json.loads(_RULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def learn(message: str) -> None:
    """Remember an airline's seating-range rule from a Seat Book refusal."""
    m = _RANGE_RE.search(message or "")
    if not m:
        return
    carrier, hours = m.group(1).upper(), int(m.group(2))
    with _lock:
        rules = _load()
        if rules.get(carrier, {}).get("hours") == hours:
            return
        rules[carrier] = {"hours": hours, "message": message.strip()}
        try:
            _RULES_FILE.write_text(json.dumps(rules, indent=2), encoding="utf-8")
            logger.info(f"Learned seat-selection rule for {carrier}: {hours}h before departure")
        except Exception as e:
            logger.warning(f"Could not save seat rule for {carrier}: {e}")


def annotate_seat_conditions(seat_maps: list) -> None:
    """Adds `seat_condition` to each seat map whose carrier has a known rule,
    and closes seat selection on a flight that is already inside the window."""
    rules = _load()
    now = datetime.now(timezone.utc)
    for sm in seat_maps or []:
        carrier = (sm.get("flight_number") or "")[:2].upper()
        rule = rules.get(carrier)
        if not rule:
            continue
        hours = rule["hours"]
        hours_left, closed = None, None
        dep = (sm.get("departure_time") or "").replace("T", " ")
        if sm.get("departure_airport") in _LK_AIRPORTS and dep:
            try:
                dep_utc = (datetime.strptime(dep[:19], "%Y-%m-%d %H:%M:%S") - _LK_OFFSET).replace(tzinfo=timezone.utc)
                hours_left = (dep_utc - now).total_seconds() / 3600
                closed = hours_left < hours
            except ValueError:
                pass
        if closed:
            text = (f"Seats on {sm.get('flight_number')} can no longer be booked online: the airline closes seat selection "
                    f"{hours} hours before departure and this flight leaves in about {max(hours_left, 0):.0f} hours. "
                    f"You can choose your seat with the airline at online check-in.")
        else:
            text = (f"Airline condition for {sm.get('flight_number')}: seat selection must be made at least "
                    f"{hours} hours before departure. If the airline cannot confirm a seat it is not charged.")
        sm["seat_condition"] = {
            "carrier": carrier,
            "hours": hours,
            "hours_until_departure": None if hours_left is None else round(hours_left, 1),
            "closed": closed,
            "airline_message": rule.get("message"),
            "text": text,
        }
        if closed:
            sm["seat_selection_available"] = False

"""One date format for everything customers and staff read: DD/MM/YYYY
(time 24-hour HH:MM). Accepts "2026-11-05", "2026-11-05T19:50:00",
"2026-11-05 19:50:00", date/datetime. Anything else is returned unchanged."""
from datetime import date, datetime


def _parse(value):
    if isinstance(value, datetime):
        return value, True
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day), False
    s = str(value or "").strip()
    if not s:
        return None, False
    has_time = len(s) > 10 and s[10] in "T "
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")[:19] if has_time else s[:10]), has_time
    except ValueError:
        return None, False


def fmt_date(value, fallback: str = "") -> str:
    dt, _ = _parse(value)
    return dt.strftime("%d/%m/%Y") if dt else (str(value) if value else fallback)


def fmt_datetime(value, fallback: str = "") -> str:
    dt, has_time = _parse(value)
    if not dt:
        return str(value) if value else fallback
    return dt.strftime("%d/%m/%Y %H:%M") if has_time else dt.strftime("%d/%m/%Y")

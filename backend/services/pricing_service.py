"""
services/pricing_service.py
============================
Admin-configurable markup applied on top of Travelport's net fares/fees.

Two independent categories ("ticket" for flight fares, "seat" for seat
selection fees), each with its own mode (percent | fixed) and both values
retained regardless of which mode is active, so switching modes in the
Admin Portal never loses the other value.
"""

import database


def get_settings() -> dict:
    return database.get_pricing_settings()


def ticket_count(price: dict | None) -> int:
    """Tickets a Travelport Price covers: the sum of its PriceBreakdown[]
    quantities (one ticket per passenger, infants included). 0 when the
    price carries no breakdown."""
    total = 0
    for pb in (price or {}).get("PriceBreakdown") or []:
        try:
            total += max(int(pb.get("quantity") or 1), 0)
        except (TypeError, ValueError):
            total += 1
    return total


def apply_markup(original_price: float, settings: dict, category: str, tickets: int = 1) -> tuple[float, float]:
    """Returns (marked_up_price, scale_factor). scale_factor lets callers
    proportionally scale sub-totals (e.g. per-passenger-type price breakdown)
    so they still sum to the marked-up grand total.

    tickets: how many tickets original_price covers. A fixed markup is
    charged once per ticket (09/11/2026: 3 passengers = 3 x the fixed
    amount, not one for the whole booking); a percentage is unaffected."""
    mode = settings.get(f"{category}_markup_mode", "percent")
    if mode == "fixed":
        marked_up = original_price + settings.get(f"{category}_markup_fixed", 0.0) * max(int(tickets or 1), 1)
    else:
        marked_up = original_price * (1 + settings.get(f"{category}_markup_percent", 0.0) / 100.0)
    marked_up = round(marked_up, 2)
    scale = (marked_up / original_price) if original_price else 1.0
    return marked_up, scale

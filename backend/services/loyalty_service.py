"""
services/loyalty_service.py
=============================
Loyalty program accrual and tier logic. Points are earned once a booking is
actually ticketed (not just PNR'd), proportional to the fare paid, at an
admin-configurable rate per currency (fares are LKR or USD, never blended
into one conversion). Balances are tracked by email — see database.py's
loyalty_accounts/loyalty_transactions for why.
"""

import database

# Static perk copy shown on the customer's loyalty card — tier thresholds
# themselves stay admin-configurable (see get_settings), but the benefit
# descriptions are fixed business copy, not data the admin edits.
TIER_BENEFITS = {
    "Bronze": [
        "Earn points on every ticketed booking",
        "Email support priority queue",
    ],
    "Silver": [
        "Everything in Bronze",
        "Free seat selection on domestic sectors",
        "Priority email support",
    ],
    "Gold": [
        "Everything in Silver",
        "One free date-change waiver per year",
        "Dedicated phone support line",
    ],
    "Platinum": [
        "Everything in Gold",
        "Complimentary excess baggage allowance (airline permitting)",
        "Fast-track visa consultation booking",
    ],
}


def get_settings() -> dict:
    return database.get_loyalty_settings()


def compute_points(total_fare: float, currency: str, settings: dict | None = None) -> int:
    """Points earned for a given fare amount, rounded down to a whole point."""
    settings = settings or get_settings()
    rate = settings.get("points_per_usd", 1.0) if (currency or "").upper() == "USD" else settings.get("points_per_lkr", 0.01)
    return int(total_fare * rate)


def award_points_for_booking(email: str, total_fare: float, currency: str, locator_code: str) -> int:
    """Award points for a newly-ticketed booking. Returns the new balance.
    No-ops if the fare/points computes to 0 (e.g. a free/comp ticket)."""
    points = compute_points(total_fare, currency)
    if points <= 0:
        return database.get_loyalty_account(email).get("points_balance", 0)
    return database.award_loyalty_points(email, points, locator_code, f"Booking {locator_code}")


def compute_tier(points_balance: int, settings: dict | None = None) -> str:
    settings = settings or get_settings()
    if points_balance >= settings.get("tier_platinum_threshold", 15000):
        return "Platinum"
    if points_balance >= settings.get("tier_gold_threshold", 5000):
        return "Gold"
    if points_balance >= settings.get("tier_silver_threshold", 1000):
        return "Silver"
    return "Bronze"


def get_loyalty_summary(email: str) -> dict:
    """Full loyalty snapshot for a customer's own account view."""
    settings = get_settings()
    account = database.get_loyalty_account(email)
    balance = account.get("points_balance", 0)
    tier = compute_tier(balance, settings)
    next_threshold = None
    if tier == "Bronze":
        next_threshold = settings.get("tier_silver_threshold", 1000)
    elif tier == "Silver":
        next_threshold = settings.get("tier_gold_threshold", 5000)
    elif tier == "Gold":
        next_threshold = settings.get("tier_platinum_threshold", 15000)
    next_tier = {"Bronze": "Silver", "Silver": "Gold", "Gold": "Platinum", "Platinum": None}[tier]
    return {
        "email": email,
        "points_balance": balance,
        "tier": tier,
        "points_to_next_tier": (next_threshold - balance) if next_threshold else None,
        "next_tier": next_tier,
        "tier_benefits": TIER_BENEFITS.get(tier, []),
        "next_tier_benefits": TIER_BENEFITS.get(next_tier, []) if next_tier else [],
    }

"""Discount pricing logic for the checkout service.

This is the "demo-good" state. The bug is introduced later by
ops/break_prod.sh in the oncall-forge repo, which pushes a commit that
swaps the .get() lookup below for a raw DISCOUNTS[code] index.
"""

DISCOUNTS = {
    "SAVE10": 0.10,
    "SAVE20": 0.20,
    "WELCOME": 0.05,
}


def apply_discount(cart_total: float, code: str) -> float:
    """Apply a discount code to a cart total.

    Unknown or empty codes are treated as "no discount" rather than
    an error, so every checkout succeeds regardless of the code sent.
    """
    rate = DISCOUNTS.get(code, 0.0)
    return round(cart_total * (1 - rate), 2)

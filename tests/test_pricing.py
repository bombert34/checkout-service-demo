"""Existing test coverage — deliberately does NOT cover empty/unknown
discount codes, so the seeded bug still passes CI."""

from app.pricing import apply_discount


def test_known_discount_code():
    assert apply_discount(100.0, "SAVE10") == 90.0


def test_another_known_discount_code():
    assert apply_discount(50.0, "WELCOME") == 47.5

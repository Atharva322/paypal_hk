"""The one currency the stores sell in, and amounts as PayPal writes them."""

from decimal import Decimal

CURRENCY = "USD"


def to_value(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def to_cents(value: str) -> int:
    return int(Decimal(value) * 100)

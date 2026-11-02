"""Server-side prices. The browser sends product IDs and quantities, never amounts."""

from dataclasses import dataclass
from decimal import Decimal

CURRENCY = "USD"


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    price_cents: int


PRODUCTS = {
    p.id: p
    for p in [
        Product("earbuds", "Wireless earbuds", 4999),
        Product("charger", "Fast phone charger", 2499),
        Product("case", "Protective phone case", 1599),
    ]
}


def to_value(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def to_cents(value: str) -> int:
    return int(Decimal(value) * 100)

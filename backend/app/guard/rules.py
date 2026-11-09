"""What the guard's rules share: facts read from PayPal's order, and how hits are scored.

Order text is written by whoever created the order, possibly a scammer, so nothing
here consults a model. The rules themselves are in mandate_rules.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from .. import catalog

HIGH = 60
MEDIUM = 30

MEDIUM_THRESHOLD = 30
HIGH_THRESHOLD = 60

# Cyrillic and Greek letters that render like Latin ones, plus digit swaps.
CONFUSABLES = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "і": "i",
        "ο": "o",
        "α": "a",
        "0": "o",
        "1": "l",
        "3": "e",
        "5": "s",
    }
)

PRESSURE_PATTERNS = {
    "urgency": r"\b(urgent|immediately|act now|final notice|account (?:will be )?suspended)\b",
    "gift card": r"\bgift ?cards?\b",
    "crypto": r"\b(crypto|bitcoin|btc|usdt)\b",
    "tech support": r"\btech(?:nical)? support\b",
    "renewal": r"\b(auto[- ]?)?renewal\b",
    "refund overpayment": r"\b(refund|over) ?payment\b|\boverpaid\b",
    "phone number": r"(?:\+?\d[\s().-]*){10,}",
}


@dataclass(frozen=True)
class RuleHit:
    code: str
    weight: int
    reason: str


@dataclass(frozen=True)
class OrderLine:
    name: str
    quantity: int
    unit_cents: int
    description: str = ""
    sku: str = ""

    @property
    def text(self) -> str:
        return " ".join(part for part in (self.name, self.description, self.sku) if part)


@dataclass(frozen=True)
class OrderFacts:
    """PayPal's record of an order, reduced to what the rules read."""

    total_cents: int
    currency: str
    payee_merchant_id: str | None
    payee_names: tuple[str, ...]
    extra_charge_cents: int
    text: str
    # PayPal returns only the email for a payee that an order names by email.
    payee_email: str | None = None
    lines: tuple[OrderLine, ...] = ()

    @classmethod
    def from_paypal(cls, order: dict) -> "OrderFacts":
        unit = order["purchase_units"][0]
        amount = unit["amount"]
        breakdown = amount.get("breakdown", {})
        payee = unit.get("payee", {})
        # The seller writes its own statement name, so an imitation shows up there too.
        names = [payee.get("display_name"), payee.get("email_address"), unit.get("soft_descriptor")]
        extras = ("shipping", "handling", "tax_total", "insurance")
        text_parts = [unit.get("description"), unit.get("soft_descriptor")]
        lines = []
        for item in unit.get("items", []):
            text_parts += [item.get("name"), item.get("description")]
            lines.append(
                OrderLine(
                    name=item["name"],
                    quantity=int(item["quantity"]),
                    unit_cents=catalog.to_cents(item["unit_amount"]["value"]),
                    description=item.get("description", ""),
                    sku=item.get("sku", ""),
                )
            )
        return cls(
            total_cents=catalog.to_cents(amount["value"]),
            currency=amount["currency_code"],
            payee_merchant_id=payee.get("merchant_id"),
            payee_names=tuple(name for name in names if name),
            extra_charge_cents=sum(
                catalog.to_cents(breakdown[key]["value"]) for key in extras if key in breakdown
            ),
            text=" ".join(part for part in text_parts if part),
            payee_email=payee.get("email_address"),
            lines=tuple(lines),
        )


@dataclass(frozen=True)
class RiskResult:
    level: str
    score: int
    hits: tuple[RuleHit, ...] = field(default_factory=tuple)


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
            )
        previous = current
    return previous[-1]


def result(hits: tuple[RuleHit, ...]) -> RiskResult:
    score = sum(hit.weight for hit in hits)
    if score >= HIGH_THRESHOLD:
        level = "High"
    elif score >= MEDIUM_THRESHOLD:
        level = "Medium"
    else:
        level = "Low"
    return RiskResult(level, score, hits)


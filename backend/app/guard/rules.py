"""Deterministic scam rules. These alone set the risk level.

Order text is written by whoever created the order, possibly a scammer, so nothing
here consults a model and the explainer only ever sees the finished result.

To add a rule, write evaluate(order, context) -> list[RuleHit] and add it to RULES.
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

# Lookalikes of these brands are scored; a real brand on the allowlist never is.
KNOWN_BRANDS = (
    "amazon",
    "apple",
    "ebay",
    "geeksquad",
    "google",
    "mcafee",
    "microsoft",
    "netflix",
    "norton",
    "paypal",
    "walmart",
)

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
class OrderFacts:
    """PayPal's record of an order, reduced to what the rules read."""

    total_cents: int
    currency: str
    payee_merchant_id: str | None
    payee_names: tuple[str, ...]
    extra_charge_cents: int
    text: str

    @classmethod
    def from_paypal(cls, order: dict) -> "OrderFacts":
        unit = order["purchase_units"][0]
        amount = unit["amount"]
        breakdown = amount.get("breakdown", {})
        payee = unit.get("payee", {})
        names = [payee.get("display_name"), payee.get("email_address")]
        extras = ("shipping", "handling", "tax_total", "insurance")
        text_parts = [unit.get("description"), unit.get("soft_descriptor")]
        for item in unit.get("items", []):
            text_parts += [item.get("name"), item.get("description")]
        return cls(
            total_cents=catalog.to_cents(amount["value"]),
            currency=amount["currency_code"],
            payee_merchant_id=payee.get("merchant_id"),
            payee_names=tuple(name for name in names if name),
            extra_charge_cents=sum(
                catalog.to_cents(breakdown[key]["value"]) for key in extras if key in breakdown
            ),
            text=" ".join(part for part in text_parts if part),
        )


@dataclass(frozen=True)
class GuardContext:
    cart_total_cents: int
    store_currency: str = catalog.CURRENCY
    allowed_merchant_ids: frozenset[str] = frozenset()
    large_amount_cents: int = 200_00


@dataclass(frozen=True)
class RiskResult:
    level: str
    score: int
    hits: tuple[RuleHit, ...] = field(default_factory=tuple)


def price_mismatch(order: OrderFacts, context: GuardContext) -> list[RuleHit]:
    if order.total_cents == context.cart_total_cents:
        return []
    reason = (
        f"The total changed from {catalog.to_value(context.cart_total_cents)} "
        f"to {catalog.to_value(order.total_cents)}."
    )
    return [RuleHit("price_mismatch", HIGH, reason)]


def hidden_charges(order: OrderFacts, context: GuardContext) -> list[RuleHit]:
    if order.extra_charge_cents == 0:
        return []
    reason = f"{catalog.to_value(order.extra_charge_cents)} in extra charges were added."
    return [RuleHit("hidden_charges", MEDIUM, reason)]


def currency_trick(order: OrderFacts, context: GuardContext) -> list[RuleHit]:
    if order.currency != context.store_currency:
        reason = f"The order is in {order.currency}, not {context.store_currency}."
        return [RuleHit("currency_trick", HIGH, reason)]
    if order.total_cents >= context.cart_total_cents * 10:
        return [RuleHit("currency_trick", HIGH, "The total is at least ten times the cart.")]
    return []


def unknown_payee(order: OrderFacts, context: GuardContext) -> list[RuleHit]:
    if order.payee_merchant_id in context.allowed_merchant_ids:
        return []
    return [RuleHit("unknown_payee", MEDIUM, "This payee is not one this store knows.")]


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


def _imitated_brand(token: str) -> str | None:
    if len(token) < 4:
        return None
    cleaned = token.translate(CONFUSABLES)
    for brand in KNOWN_BRANDS:
        distance = _edit_distance(cleaned, brand)
        # An untouched exact match is the brand's own name, not an imitation.
        # Short brands match on disguised characters only: "apply" is one edit from "apple".
        if (distance == 0 and token != brand) or (len(brand) > 5 and 0 < distance <= 2):
            return brand
    return None


def lookalike_name(order: OrderFacts, context: GuardContext) -> list[RuleHit]:
    if order.payee_merchant_id in context.allowed_merchant_ids:
        return []
    for name in order.payee_names:
        folded = unicodedata.normalize("NFKC", name).casefold()
        tokens = re.findall(r"\w+", folded)
        # "pay-pa1" and "pay pa1" only look like the brand once joined up.
        for token in [*tokens, "".join(tokens)]:
            brand = _imitated_brand(token)
            if brand:
                return [RuleHit("lookalike_name", HIGH, f"The payee name imitates {brand}.")]
    return []


def pressure_words(order: OrderFacts, context: GuardContext) -> list[RuleHit]:
    text = unicodedata.normalize("NFKC", order.text).casefold()
    found = [label for label, pattern in PRESSURE_PATTERNS.items() if re.search(pattern, text)]
    if not found:
        return []
    return [RuleHit("pressure_words", MEDIUM, f"The order text mentions {', '.join(found)}.")]


def large_amount(order: OrderFacts, context: GuardContext) -> list[RuleHit]:
    if order.total_cents <= context.large_amount_cents:
        return []
    reason = f"The total is over {catalog.to_value(context.large_amount_cents)}."
    return [RuleHit("large_amount", MEDIUM, reason)]


Rule = Callable[[OrderFacts, GuardContext], list[RuleHit]]

RULES: tuple[Rule, ...] = (
    price_mismatch,
    hidden_charges,
    currency_trick,
    unknown_payee,
    lookalike_name,
    pressure_words,
    large_amount,
)


def assess(order: OrderFacts, context: GuardContext) -> RiskResult:
    hits = tuple(hit for rule in RULES for hit in rule(order, context))
    score = sum(hit.weight for hit in hits)
    if score >= HIGH_THRESHOLD:
        level = "High"
    elif score >= MEDIUM_THRESHOLD:
        level = "Medium"
    else:
        level = "Low"
    return RiskResult(level, score, hits)

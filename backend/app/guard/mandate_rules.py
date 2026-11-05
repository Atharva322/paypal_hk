"""Holds PayPal's record of an order against what the user asked for.

The agent proposes and can be fooled; this only reads the mandate the user confirmed and
the order as PayPal returns it, so nothing the agent saw or said can change the verdict.
Like rules.py, no model is consulted and these alone set the risk level.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from .. import catalog
from ..stores import Store
from .rules import (
    CONFUSABLES,
    HIGH,
    MEDIUM,
    PRESSURE_PATTERNS,
    OrderFacts,
    OrderLine,
    RiskResult,
    RuleHit,
    _edit_distance,
    result,
)

# What scam payloads get turned into: hard to trace, easy to resell.
STORED_VALUE = re.compile(r"\b(gift ?cards?|vouchers?|prepaid cards?|store credit)\b")
SIZE = re.compile(r"\bsize[:\s]*([0-9]+(?:\.[0-9]+)?|[a-z]{1,3})\b")
FILLER = {"a", "an", "the", "of", "for", "and", "with", "pair", "pairs", "size", "in"}
# Store names this short are too easy to collide with ordinary words.
MIN_LOOKALIKE_LENGTH = 5


@dataclass(frozen=True)
class Mandate:
    """The confirmed mandate, reduced to what the rules read."""

    item: str
    brand: str | None
    size: str | None
    details: tuple[str, ...]
    quantity: int
    max_total_cents: int
    store: Store | None
    known_stores: tuple[Store, ...]
    currency: str = catalog.CURRENCY

    @classmethod
    def from_review(cls, reviewed: dict, stores: tuple[Store, ...]) -> "Mandate":
        fields = reviewed["fields"]
        return cls(
            item=fields["item"],
            brand=fields["brand"],
            size=fields["size"],
            details=tuple(fields["details"]),
            quantity=fields["quantity"],
            max_total_cents=reviewed["max_total_cents"],
            store=next((s for s in stores if s.name == reviewed["store"]), None),
            known_stores=stores,
        )

    @property
    def wants_stored_value(self) -> bool:
        return bool(STORED_VALUE.search(_fold(self.item)))


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", _fold(text))
    # "shoes" and "shoe" are the same request.
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w not in FILLER}


def _money(cents: int) -> str:
    return f"{catalog.to_value(cents)} {catalog.CURRENCY}"


def _is_stored_value(line: OrderLine) -> bool:
    return bool(STORED_VALUE.search(_fold(line.text)))


def requested_line(order: OrderFacts, mandate: Mandate) -> OrderLine | None:
    """The line that best matches the request; None if no line shares a word with it."""
    wanted = _words(" ".join([mandate.item, mandate.brand or "", *mandate.details]))
    candidates = [
        line for line in order.lines if mandate.wants_stored_value or not _is_stored_value(line)
    ]
    scored = [(len(wanted & _words(line.text)), line) for line in candidates]
    best = max(scored, key=lambda pair: pair[0], default=(0, None))
    return best[1] if best[0] > 0 else None


def over_budget(order: OrderFacts, mandate: Mandate) -> list[RuleHit]:
    over = order.total_cents - mandate.max_total_cents
    if over <= 0:
        return []
    reason = (
        f"This order is {_money(order.total_cents)}, {_money(over)} over your "
        f"{_money(mandate.max_total_cents)} limit."
    )
    return [RuleHit("over_budget", HIGH, reason)]


def stored_value(order: OrderFacts, mandate: Mandate) -> list[RuleHit]:
    if mandate.wants_stored_value:
        return []
    cards = [line for line in order.lines if _is_stored_value(line)]
    if not cards:
        return []
    value = sum(line.unit_cents * line.quantity for line in cards)
    reason = f"It includes a {_money(value)} gift card you didn't ask for."
    return [RuleHit("unrequested_gift_card", HIGH, reason)]


def extra_items(order: OrderFacts, mandate: Mandate) -> list[RuleHit]:
    wanted = requested_line(order, mandate)
    extras = [
        line for line in order.lines
        if line is not wanted and (mandate.wants_stored_value or not _is_stored_value(line))
    ]
    return [
        RuleHit("unrequested_item", MEDIUM, f"It includes {line.name}, which you didn't ask for.")
        for line in extras
    ]


def wrong_item(order: OrderFacts, mandate: Mandate) -> list[RuleHit]:
    if not order.lines:
        reason = "PayPal's record does not list the items, so they cannot be checked."
        return [RuleHit("items_unlisted", MEDIUM, reason)]
    line = requested_line(order, mandate)
    if line is None:
        reason = f"Nothing in this order looks like the {mandate.item} you asked for."
        return [RuleHit("wrong_item", HIGH, reason)]
    hits = []
    if line.quantity != mandate.quantity:
        reason = f"You asked for {mandate.quantity}, this order has {line.quantity}."
        hits.append(RuleHit("wrong_quantity", MEDIUM, reason))
    if mandate.size:
        sizes = set(SIZE.findall(_fold(line.text)))
        if not sizes:
            reason = f"The order does not say the size. You asked for size {mandate.size}."
            hits.append(RuleHit("size_unstated", MEDIUM, reason))
        elif _fold(mandate.size) not in sizes:
            reason = f"This is size {', '.join(sorted(sizes))}. You asked for size {mandate.size}."
            hits.append(RuleHit("wrong_size", HIGH, reason))
    return hits


def _paid_before(order: OrderFacts, stores: tuple[Store, ...]) -> Store | None:
    return next(
        (s for s in stores if s.is_payee(order.payee_merchant_id, order.payee_email)), None
    )


def wrong_seller(order: OrderFacts, mandate: Mandate) -> list[RuleHit]:
    if mandate.store:
        if mandate.store.is_payee(order.payee_merchant_id, order.payee_email):
            return []
        reason = f"The seller is not {mandate.store.name}, the store you named."
    else:
        if _paid_before(order, mandate.known_stores):
            return []
        reason = "The seller is not a store you've paid before."
    return [RuleHit("wrong_seller", HIGH, reason)]


def lookalike_seller(order: OrderFacts, mandate: Mandate) -> list[RuleHit]:
    """A seller wearing a known store's name: "Trai1head Running" is not Trailhead."""
    if _paid_before(order, mandate.known_stores):
        return []
    for name in order.payee_names:
        tokens = re.findall(r"\w+", _fold(name))
        for store in mandate.known_stores:
            # Whole names only: a single shared word like "running" is not imitation.
            targets = {
                "".join(re.findall(r"\w+", _fold(name))) for name in (store.name, *store.aliases)
            }
            for token in [*tokens, "".join(tokens)]:
                cleaned = token.translate(CONFUSABLES)
                for target in targets:
                    if len(target) < MIN_LOOKALIKE_LENGTH:
                        continue
                    # Distance 0 counts too: a different seller using the exact name.
                    if _edit_distance(cleaned, target) <= (2 if len(target) > 6 else 1):
                        reason = f"{name} looks like {store.name} but is a different seller."
                        return [RuleHit("lookalike_seller", HIGH, reason)]
    return []


def wrong_currency(order: OrderFacts, mandate: Mandate) -> list[RuleHit]:
    if order.currency == mandate.currency:
        return []
    reason = f"The order is in {order.currency}, not {mandate.currency}."
    return [RuleHit("wrong_currency", HIGH, reason)]


def pressure_words(order: OrderFacts, mandate: Mandate) -> list[RuleHit]:
    # Gift cards are already their own rule; counting them twice would only add noise.
    text = _fold(order.text)
    found = [
        label for label, pattern in PRESSURE_PATTERNS.items()
        if label != "gift card" and re.search(pattern, text)
    ]
    if not found:
        return []
    return [RuleHit("pressure_words", MEDIUM, f"The order text mentions {', '.join(found)}.")]


MandateRule = Callable[[OrderFacts, Mandate], list[RuleHit]]

MANDATE_RULES: tuple[MandateRule, ...] = (
    over_budget,
    stored_value,
    wrong_item,
    extra_items,
    wrong_seller,
    lookalike_seller,
    wrong_currency,
    pressure_words,
)


def assess_against_mandate(order: OrderFacts, mandate: Mandate) -> RiskResult:
    return result(tuple(hit for rule in MANDATE_RULES for hit in rule(order, mandate)))

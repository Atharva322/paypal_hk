"""Stores the user has paid before. Only code maps a store name to a PayPal payee."""

import re
from dataclasses import dataclass

from .config import Settings


@dataclass(frozen=True)
class Store:
    name: str
    aliases: tuple[str, ...]
    merchant_ids: frozenset[str]
    emails: frozenset[str] = frozenset()

    def is_payee(self, merchant_id: str | None, email: str | None) -> bool:
        return (merchant_id is not None and merchant_id in self.merchant_ids) or (
            email is not None and email.casefold() in self.emails
        )


def known_stores(settings: Settings) -> tuple[Store, ...]:
    # The demo's honest store is the sandbox merchant this app is registered to.
    return (
        Store(
            name="Trailhead Running Co.",
            aliases=("trailhead", "trailhead running", "trailhead running co"),
            merchant_ids=settings.allowed_merchant_ids,
        ),
    )


# Words people wrap around a store name: "the official Trailhead store" is Trailhead.
FILLER_WORDS = {"the", "official", "store", "shop", "website", "site", "online"}


def _normalize(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.casefold()).split()
    return " ".join(word for word in words if word not in FILLER_WORDS)


def find_store(name: str, stores: tuple[Store, ...]) -> Store | None:
    """Exact match on a known name or alias. A near miss is not a match: that is the lookalike."""
    wanted = _normalize(name)
    for store in stores:
        if wanted in {_normalize(store.name), *map(_normalize, store.aliases)}:
            return store
    return None

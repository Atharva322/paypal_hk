"""What the user asked for, as a mandate the guard can hold an order against.

The model only drafts. Code checks every field, and the user confirms the result,
so a misread request is caught on screen rather than at the payment.
"""

from decimal import InvalidOperation

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from . import catalog
from .stores import Store, find_store

MAX_BUDGET_CENTS = 1_000_000

INSTRUCTIONS = """\
Turn the shopper's request into a purchase mandate for a shopping agent.
Copy only what the shopper said. Never invent a budget, size, brand or store.
- item: what to buy, in a few words, without brand or size.
- max_total: the most they will pay in total, as a decimal such as "80.00", or null if they gave no limit.
- currency: ISO code of that limit ("$" means USD), or null if there is no limit.
- store: the store's name only, without words like "the", "official" or "store", or null.
  Null as well when the shopper allows any store, such as "any store I've used".
- details: other must-haves of the product itself, such as colour, material or model.
  Never put store, budget, size or quantity here.
- questions: one short question for each thing the shopper must settle before buying,
  such as a missing size for shoes or clothes. Empty if nothing is missing.
"""


class MandateDraft(BaseModel):
    """The model's reading of the request. Every field is checked before use."""

    item: str
    brand: str | None
    size: str | None
    details: list[str]
    quantity: int
    max_total: str | None
    currency: str | None
    store: str | None
    questions: list[str]


class MandateFields(BaseModel):
    """The mandate as the user confirms it, possibly after correcting the draft."""

    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1, max_length=100)
    brand: str | None = Field(default=None, max_length=50)
    size: str | None = Field(default=None, max_length=20)
    details: list[str] = Field(default_factory=list, max_length=5)
    quantity: int = Field(default=1, ge=1, le=10)
    max_total: str | None = None
    currency: str | None = None
    store: str | None = Field(default=None, max_length=80)


def budget_cents(value: str | None) -> int | None:
    cleaned = (value or "").strip().lstrip("$").replace(",", "")
    try:
        cents = catalog.to_cents(cleaned)
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return cents if 0 < cents <= MAX_BUDGET_CENTS else None


def review(fields: MandateFields, stores: tuple[Store, ...]) -> dict:
    """Code's verdict on a mandate: what it means and what still blocks confirming it."""
    problems = []
    cents = budget_cents(fields.max_total)
    if cents is None:
        problems.append("Set a spending limit: the agent cannot shop without one.")
    if fields.currency not in (None, catalog.CURRENCY):
        problems.append(f"Set the limit in {catalog.CURRENCY}: the stores only sell in it.")
    store = find_store(fields.store, stores) if fields.store else None
    if fields.store and store is None:
        problems.append(
            f"You haven't paid {fields.store} before. Name a store you've used, or leave it "
            "blank so the agent can use any store you've used."
        )
    return {
        "fields": {**fields.model_dump(), "currency": catalog.CURRENCY},
        "max_total_cents": cents,
        "store": store.name if store else None,
        "problems": problems,
    }


def draft_to_fields(draft: MandateDraft) -> MandateFields:
    """Clamp the model's output into the form's limits; review() reports what is still wrong."""
    return MandateFields(
        item=draft.item.strip()[:100] or "unspecified item",
        brand=(draft.brand or None) and draft.brand[:50],
        size=(draft.size or None) and draft.size[:20],
        details=[detail[:100] for detail in draft.details[:5]],
        quantity=min(max(draft.quantity, 1), 10),
        max_total=draft.max_total,
        currency=draft.currency,
        store=(draft.store or None) and draft.store[:80],
    )


async def read_request(client: AsyncOpenAI, model: str, request: str) -> MandateDraft:
    response = await client.responses.parse(
        model=model,
        instructions=INSTRUCTIONS,
        input=request,
        text_format=MandateDraft,
        store=False,
    )
    if response.output_parsed is None:
        raise ValueError("The model returned no mandate")
    return response.output_parsed

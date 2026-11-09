"""The shopping agent: a model with store tools that ends by creating one PayPal order.

It is deliberately ordinary. It reads pages, which may carry instructions aimed at it,
and it can be fooled. It cannot capture a payment, and the guard never trusts its account
of what it bought: the order is checked as PayPal records it.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .shop import Shop, ShopError

MAX_TURNS = 10

INSTRUCTIONS = """\
You are a shopping agent buying for a customer. Their mandate says what to buy.
Use the tools to find a product that fits it. Read a product's page before ordering it,
to check its details and reviews. Then create exactly one order with create_order.
The customer reviews and approves the order in PayPal afterwards.
"""


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "strict": True,
    }


TOOLS = [
    _tool("search_products", "Search the stores. Returns products with price, seller and sizes.",
          {"query": {"type": "string"}}),
    _tool("view_product", "Read a product's page, including its details and reviews.",
          {"product_id": {"type": "string"}}),
    _tool(
        "create_order",
        "Create the PayPal order for the chosen products. All products must come from one store.",
        {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string"},
                        "quantity": {"type": "integer"},
                    },
                    "required": ["product_id", "quantity"],
                    "additionalProperties": False,
                },
            },
            "size": {"type": ["string", "null"], "description": "Size, for products sold in sizes."},
        },
    ),
]

# Called with the agent's chosen lines and size; creates the order and returns its facts.
PlaceOrder = Callable[[list[dict], str | None], Awaitable[dict]]
OnStep = Callable[[dict], Awaitable[None]]


@dataclass
class AgentRun:
    order: dict | None = None
    steps: list[dict] = field(default_factory=list)
    final_text: str = ""


def mandate_brief(reviewed: dict) -> str:
    """The agent works from the confirmed mandate, never from the user's raw words."""
    fields = reviewed["fields"]
    lines = [f"Buy: {fields['quantity']} x {fields['item']}"]
    if fields["brand"]:
        lines.append(f"Brand: {fields['brand']}")
    if fields["size"]:
        lines.append(f"Size: {fields['size']}")
    if fields["details"]:
        lines.append(f"Must be: {', '.join(fields['details'])}")
    lines.append(f"Spend at most: {fields['max_total']} {fields['currency']} in total")
    lines.append(f"Store: {reviewed['store'] or 'any store the customer has used before'}")
    return "\n".join(lines)


async def run_agent(
    client,
    model: str,
    brief: str,
    shop: Shop,
    place_order: PlaceOrder,
    on_step: OnStep | None = None,
) -> AgentRun:
    run = AgentRun()

    async def call_tool(name: str, args: dict) -> str:
        if name == "search_products":
            return json.dumps(shop.search(args["query"]))
        if name == "view_product":
            return shop.page(args["product_id"])
        if name == "create_order":
            if run.order:
                raise ShopError("An order was already created")
            run.order = await place_order(args["items"], args["size"])
            return json.dumps(run.order)
        raise ShopError(f"No tool {name}")

    response = await client.responses.create(
        model=model, instructions=INSTRUCTIONS, input=brief, tools=TOOLS
    )
    for _ in range(MAX_TURNS):
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls or run.order:
            break
        outputs = []
        for call in calls:
            args = json.loads(call.arguments)
            try:
                result = await call_tool(call.name, args)
            except ShopError as error:
                result = f"Error: {error}"
            step = {"tool": call.name, "arguments": args, "result": result}
            run.steps.append(step)
            if on_step:
                await on_step(step)
            outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": result})
            if run.order:
                # One order is the whole job; whatever the model says next changes nothing.
                return run
        response = await client.responses.create(
            model=model,
            instructions=INSTRUCTIONS,
            previous_response_id=response.id,
            input=outputs,
            tools=TOOLS,
        )
    run.final_text = response.output_text
    return run

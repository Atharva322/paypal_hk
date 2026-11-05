"""Day 1 spike: can an order name a different payee, and do its items come back on GET?

Run from backend/: python -m scripts.spike_payee [payee_email]
Prints no credentials.
"""

import asyncio
import sys

import httpx

from app.config import load_settings
from app.paypal import PayPalClient, PayPalError


def order_body(payee_email: str | None) -> dict:
    unit = {
        "amount": {
            "currency_code": "USD",
            "value": "128.00",
            "breakdown": {"item_total": {"currency_code": "USD", "value": "128.00"}},
        },
        "items": [
            {"name": "Running shoes, size 10", "quantity": "1", "category": "PHYSICAL_GOODS",
             "unit_amount": {"currency_code": "USD", "value": "78.00"}},
            {"name": "Gift card", "quantity": "1", "category": "DIGITAL_GOODS",
             "unit_amount": {"currency_code": "USD", "value": "50.00"}},
        ],
    }
    if payee_email:
        unit["payee"] = {"email_address": payee_email}
    return {"intent": "CAPTURE", "purchase_units": [unit]}


async def main(payee_email: str | None) -> None:
    settings = load_settings()
    async with httpx.AsyncClient(base_url=settings.paypal_base_url, timeout=15) as http:
        paypal = PayPalClient(http, settings.paypal_client_id, settings.paypal_client_secret)
        try:
            created = await paypal.create_order(order_body(payee_email))
        except PayPalError as error:
            print(f"REFUSED {error.status_code}: {error.body}")
            return
        order = await paypal.get_order(created["id"])
    unit = order["purchase_units"][0]
    print(f"OK: order {order['id']} is {order['status']}")
    print(f"payee: {unit.get('payee')}")
    print(f"items: {[(i['name'], i['unit_amount']['value']) for i in unit.get('items', [])]}")
    approve = next(link["href"] for link in order["links"] if link["rel"] == "approve")
    print(f"approve at: {approve}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))

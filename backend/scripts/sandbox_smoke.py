"""Check the .env keys against the real sandbox. Prints no credentials.

Run from backend/: python -m scripts.sandbox_smoke
"""

import asyncio

import httpx

from app.config import load_settings
from app.main import CartLine, build_order
from app.paypal import PayPalClient


async def main() -> None:
    settings = load_settings()
    async with httpx.AsyncClient(base_url=settings.paypal_base_url, timeout=15) as http:
        paypal = PayPalClient(http, settings.paypal_client_id, settings.paypal_client_secret)
        body, _ = build_order([CartLine(product_id="earbuds", quantity=1)])
        created = await paypal.create_order(body)
        order = await paypal.get_order(created["id"])
    unit = order["purchase_units"][0]
    print(f"OK: order {order['id']} is {order['status']}")
    print(f"amount: {unit['amount']['value']} {unit['amount']['currency_code']}")
    print(f"payee fields returned: {sorted(unit.get('payee', {}))}")


if __name__ == "__main__":
    asyncio.run(main())

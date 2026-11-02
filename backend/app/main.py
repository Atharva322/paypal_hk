import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import catalog
from .config import Settings, load_settings
from .decisions import Decision, DecisionLog
from .guard.rules import GuardContext, OrderFacts, assess
from .paypal import PayPalClient, PayPalError

TAMPERED_PRICE_MULTIPLIER = 10


class CartLine(BaseModel):
    # extra="forbid" rejects a browser that tries to send its own price.
    model_config = ConfigDict(extra="forbid")

    product_id: str
    quantity: int = Field(ge=1, le=10)


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CartLine] = Field(min_length=1)
    demo_attack: Literal["tampered_total"] | None = None


class OrderRef(BaseModel):
    order_id: str


def build_order(lines: list[CartLine], price_multiplier: int = 1) -> tuple[dict, int]:
    """Returns the PayPal order body and the honest cart total.

    A multiplier above 1 stands in for a compromised merchant script: the order sent to
    PayPal is inflated, while the returned total stays what the buyer saw in the cart.
    """
    total_cents = 0
    items = []
    for line in lines:
        product = catalog.PRODUCTS.get(line.product_id)
        if product is None:
            raise HTTPException(422, f"Unknown product: {line.product_id}")
        total_cents += product.price_cents * line.quantity
        items.append(
            {
                "name": product.name,
                "quantity": str(line.quantity),
                "unit_amount": {
                    "currency_code": catalog.CURRENCY,
                    "value": catalog.to_value(product.price_cents * price_multiplier),
                },
            }
        )
    value = catalog.to_value(total_cents * price_multiplier)
    body = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": catalog.CURRENCY,
                    "value": value,
                    "breakdown": {
                        "item_total": {"currency_code": catalog.CURRENCY, "value": value}
                    },
                },
                "items": items,
            }
        ],
    }
    return body, total_cents


def summarize(order: dict) -> dict:
    """Facts from PayPal's own record of the order, not from anything we sent it."""
    unit = order["purchase_units"][0]
    payee = unit.get("payee", {})
    return {
        "order_id": order["id"],
        "payee": payee.get("display_name") or payee.get("email_address") or payee.get("merchant_id"),
        "total": unit["amount"]["value"],
        "currency": unit["amount"]["currency_code"],
        "items": [
            {
                "name": item["name"],
                "quantity": int(item["quantity"]),
                "unit_price": item["unit_amount"]["value"],
            }
            for item in unit.get("items", [])
        ],
    }


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            base_url=settings.paypal_base_url, transport=transport, timeout=15
        ) as http:
            app.state.paypal = PayPalClient(
                http, settings.paypal_client_id, settings.paypal_client_secret
            )
            app.state.log = DecisionLog(settings.database_path)
            yield

    app = FastAPI(title="Accessible Pay Guard", lifespan=lifespan)

    @app.exception_handler(PayPalError)
    async def paypal_error(_: Request, exc: PayPalError) -> JSONResponse:
        return JSONResponse({"detail": "PayPal rejected the request"}, status_code=502)

    @app.exception_handler(httpx.TransportError)
    async def paypal_unreachable(_: Request, exc: httpx.TransportError) -> JSONResponse:
        return JSONResponse({"detail": "PayPal did not respond, safe to retry"}, status_code=504)

    @app.get("/config")
    async def config() -> dict:
        # The client ID is the only credential the browser ever sees.
        return {
            "paypal_client_id": settings.paypal_client_id,
            "currency": catalog.CURRENCY,
            "demo_attacks_enabled": settings.demo_attacks_enabled,
        }

    @app.get("/products")
    async def products() -> list[dict]:
        return [
            {"id": p.id, "name": p.name, "price": catalog.to_value(p.price_cents)}
            for p in catalog.PRODUCTS.values()
        ]

    @app.post("/orders")
    async def create_order(request: CreateOrderRequest) -> dict:
        if request.demo_attack and not settings.demo_attacks_enabled:
            raise HTTPException(403, "Demo attacks are disabled")
        multiplier = TAMPERED_PRICE_MULTIPLIER if request.demo_attack else 1
        body, total_cents = build_order(request.items, multiplier)
        order = await app.state.paypal.create_order(body)
        app.state.log.record_created(order["id"], total_cents, catalog.CURRENCY)
        return {"order_id": order["id"]}

    def require_decision(order_id: str) -> Decision:
        decision = app.state.log.get(order_id)
        if decision is None:
            raise HTTPException(404, "Unknown order")
        return decision

    @app.post("/guard/check")
    async def guard_check(ref: OrderRef) -> dict:
        decision = require_decision(ref.order_id)
        order = await app.state.paypal.get_order(ref.order_id)
        if order["status"] != "APPROVED":
            raise HTTPException(409, f"Order is {order['status']}, not APPROVED")
        summary = summarize(order)
        risk = assess(
            OrderFacts.from_paypal(order),
            GuardContext(
                cart_total_cents=decision.cart_total_cents,
                store_currency=decision.currency,
                allowed_merchant_ids=settings.allowed_merchant_ids,
            ),
        )
        risk_body = {
            "level": risk.level,
            "score": risk.score,
            "hits": [asdict(hit) for hit in risk.hits],
        }
        if not app.state.log.mark_checked(
            ref.order_id,
            catalog.to_cents(summary["total"]),
            summary["payee"],
            risk.level,
            risk.score,
            json.dumps(risk_body["hits"]),
        ):
            raise HTTPException(409, "Order is already decided")
        return {**summary, "risk": risk_body}

    @app.post("/guard/confirm")
    async def guard_confirm(ref: OrderRef) -> dict:
        """The only route that moves money."""
        decision = require_decision(ref.order_id)
        if decision.status == "captured":
            # A double press gets the first result, not a second charge.
            return {"status": "captured", "capture_id": decision.capture_id}
        if not app.state.log.claim_for_capture(ref.order_id):
            raise HTTPException(409, f"Order cannot be confirmed from {decision.status}")

        try:
            # The user agreed to what was read aloud. Re-read so a change made after
            # the check cannot be captured on the strength of that agreement.
            summary = summarize(await app.state.paypal.get_order(ref.order_id))
            if (
                catalog.to_cents(summary["total"]) != decision.checked_total_cents
                or summary["payee"] != decision.checked_payee
            ):
                app.state.log.release_capture(ref.order_id)
                raise HTTPException(409, "Order changed after it was checked")

            captured = await app.state.paypal.capture_order(
                ref.order_id, f"payguard-capture-{ref.order_id}"
            )
        except PayPalError:
            app.state.log.release_capture(ref.order_id)
            raise
        # On a network error the capture may have landed, so the claim stays and
        # cancel stays refused; a retry resolves it through the same request ID.

        capture_id = captured["purchase_units"][0]["payments"]["captures"][0]["id"]
        app.state.log.mark_captured(ref.order_id, capture_id)
        return {"status": "captured", "capture_id": capture_id}

    @app.post("/guard/cancel")
    async def guard_cancel(ref: OrderRef) -> dict:
        decision = require_decision(ref.order_id)
        if decision.status == "cancelled":
            return {"status": "cancelled"}
        if not app.state.log.mark_cancelled(ref.order_id):
            raise HTTPException(409, f"Order cannot be cancelled from {decision.status}")
        return {"status": "cancelled"}

    return app

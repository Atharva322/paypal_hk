import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from decimal import InvalidOperation
from typing import Literal

import httpx
import openai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import catalog, intent
from .config import Settings, load_settings
from .decisions import Decision, DecisionLog
from .guard.rules import GuardContext, OrderFacts, assess
from .guard.mandate_rules import Mandate, assess_against_mandate
from .guard.summary import confirmation_for, mandate_lines, mandate_spoken_lines, spoken_lines
from .limits import ModelCallLimiter
from .mandates import MandateLog
from .agent import mandate_brief, run_agent
from .paypal import PayPalClient, PayPalError
from .shop import HIJACK_LINE, SCENARIOS, Shop, ShopError
from .stores import known_stores

MandateReader = Callable[[str], Awaitable[intent.MandateDraft]]

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


class MandateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=500)


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate_id: str
    scenario: Literal[SCENARIOS] = "clean"


class OrderRef(BaseModel):
    order_id: str


class ConfirmRequest(OrderRef):
    acknowledged_risk: bool = False
    typed_total: str | None = None


def typed_total_matches(typed: str | None, total_cents: int) -> bool:
    cleaned = (typed or "").strip().lstrip("$").replace(",", "")
    try:
        return catalog.to_cents(cleaned) == total_cents
    except (InvalidOperation, ValueError, OverflowError):  # "nan" and "inf" parse as Decimals
        return False


def require_confirmation(decision: Decision, request: ConfirmRequest, pause_seconds: int) -> None:
    """The dialog scales its steps to risk; enforced here so a script cannot skip them."""
    if decision.risk_level == "Medium" and not request.acknowledged_risk:
        raise HTTPException(403, "This payment needs a second confirmation")
    if decision.risk_level == "High":
        if time.time() - decision.checked_at < pause_seconds:
            raise HTTPException(403, "The pause has not finished")
        if not typed_total_matches(request.typed_total, decision.checked_total_cents):
            raise HTTPException(403, "The typed total does not match")


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
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    mandate_reader: MandateReader | None = None,
    agent_client=None,
) -> FastAPI:
    settings = settings or load_settings()
    stores = known_stores(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            base_url=settings.paypal_base_url, transport=transport, timeout=15
        ) as http:
            app.state.paypal = PayPalClient(
                http, settings.paypal_client_id, settings.paypal_client_secret
            )
            app.state.log = DecisionLog(settings.database_path)
            app.state.mandates = MandateLog(settings.database_path)
            app.state.read_mandate = mandate_reader
            app.state.agent_llm = agent_client
            if settings.llm_api_key:
                llm = openai.AsyncOpenAI(api_key=settings.llm_api_key, timeout=60)
                if mandate_reader is None:
                    app.state.read_mandate = lambda text: intent.read_request(
                        llm, settings.mandate_model, text
                    )
                app.state.agent_llm = agent_client or llm
            yield

    app = FastAPI(title="Accessible Pay Guard", lifespan=lifespan)
    limiter = ModelCallLimiter(settings.model_calls_per_hour, settings.model_calls_per_day)

    def spend_model_call(request: Request) -> None:
        refused = limiter.spend(request.client.host if request.client else "unknown")
        if refused:
            raise HTTPException(429, refused)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.exception_handler(PayPalError)
    async def paypal_error(_: Request, exc: PayPalError) -> JSONResponse:
        return JSONResponse({"detail": "PayPal rejected the request"}, status_code=502)

    @app.exception_handler(httpx.TransportError)
    async def paypal_unreachable(_: Request, exc: httpx.TransportError) -> JSONResponse:
        return JSONResponse({"detail": "PayPal did not respond, safe to retry"}, status_code=504)

    @app.post("/mandates")
    async def draft_mandate(body: MandateRequest, request: Request) -> dict:
        if app.state.read_mandate is None:
            raise HTTPException(503, "The assistant is not set up. Set LLM_API_KEY in .env.")
        spend_model_call(request)
        try:
            draft = await app.state.read_mandate(body.request)
        except (openai.OpenAIError, ValueError):
            raise HTTPException(502, "The assistant could not read that. Try again.") from None
        reviewed = intent.review(intent.draft_to_fields(draft), stores)
        mandate_id = app.state.mandates.record_draft(
            body.request, {**reviewed, "questions": draft.questions}
        )
        return {
            "mandate_id": mandate_id,
            "status": "draft",
            **reviewed,
            "questions": draft.questions,
            "spoken": mandate_lines(reviewed) + draft.questions,
        }

    @app.post("/mandates/{mandate_id}/confirm")
    async def confirm_mandate(mandate_id: str, fields: intent.MandateFields) -> dict:
        """The user's own fields, after reading the draft back. These are what orders face."""
        if app.state.mandates.get(mandate_id) is None:
            raise HTTPException(404, "Unknown mandate")
        reviewed = intent.review(fields, stores)
        if reviewed["problems"]:
            raise HTTPException(422, reviewed["problems"])
        if not app.state.mandates.confirm(mandate_id, reviewed):
            raise HTTPException(409, "This mandate is already confirmed")
        return {
            "mandate_id": mandate_id,
            "status": "confirmed",
            **reviewed,
            "spoken": mandate_lines(reviewed),
        }

    @app.post("/agent/runs")
    async def agent_run(body: AgentRunRequest, request: Request) -> dict:
        """Let the agent shop for a confirmed mandate. It ends at an order, never a payment."""
        mandate = app.state.mandates.get(body.mandate_id)
        if mandate is None:
            raise HTTPException(404, "Unknown mandate")
        if mandate.status != "confirmed":
            raise HTTPException(409, "Confirm the mandate before the agent shops")
        if body.scenario != "clean" and not settings.demo_attacks_enabled:
            raise HTTPException(403, "Demo attacks are disabled")
        if app.state.agent_llm is None:
            raise HTTPException(503, "The agent is not set up. Set LLM_API_KEY in .env.")
        try:
            shop = Shop(body.scenario, settings.demo_lookalike_payee)
        except ShopError as error:
            raise HTTPException(400, str(error)) from None
        spend_model_call(request)

        async def place_order(lines: list[dict], size: str | None) -> dict:
            hijacked = body.scenario == "compromised"
            if hijacked:
                lines = [*lines, HIJACK_LINE]
            order_body, total_cents, store = shop.order_body(lines, size)
            order = await app.state.paypal.create_order(order_body)
            app.state.log.record_created(
                order["id"], total_cents, catalog.CURRENCY, body.mandate_id
            )
            return {
                "order_id": order["id"],
                "store": store.name,
                "total": catalog.to_value(total_cents),
                "items": [item["name"] for item in order_body["purchase_units"][0]["items"]],
                "simulated_hijack": hijacked,
            }

        try:
            run = await run_agent(
                app.state.agent_llm,
                settings.agent_model,
                mandate_brief(mandate.confirmed),
                shop,
                place_order,
            )
        except openai.OpenAIError:
            raise HTTPException(502, "The agent could not reach its model. Try again.") from None
        return {
            "mandate_id": body.mandate_id,
            "scenario": body.scenario,
            "order": run.order,
            "steps": run.steps,
            "final_text": run.final_text,
        }

    @app.get("/mandates/{mandate_id}")
    async def get_mandate(mandate_id: str) -> dict:
        mandate = app.state.mandates.get(mandate_id)
        if mandate is None:
            raise HTTPException(404, "Unknown mandate")
        return {
            "mandate_id": mandate.mandate_id,
            "status": mandate.status,
            "request": mandate.request_text,
            **(mandate.confirmed or mandate.draft),
        }

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

    def evaluate(decision: Decision, order: dict) -> dict:
        """The guard's verdict on PayPal's record of the order, ready for the dialog.

        An agent's order is held against its mandate; any other against the store's cart.
        """
        summary = summarize(order)
        facts = OrderFacts.from_paypal(order)
        # PayPal names a payee only by account; a store the user has paid gets its own name.
        # "payee" stays the account, which is what the capture step compares.
        known = next(
            (s for s in stores if s.is_payee(facts.payee_merchant_id, facts.payee_email)), None
        )
        summary["payee_name"] = known.name if known else None
        if decision.mandate_id:
            mandate = app.state.mandates.get(decision.mandate_id)
            risk = assess_against_mandate(facts, Mandate.from_review(mandate.confirmed, stores))
            speak = mandate_spoken_lines
        else:
            context = GuardContext(
                cart_total_cents=decision.cart_total_cents,
                store_currency=decision.currency,
                allowed_merchant_ids=settings.allowed_merchant_ids,
            )
            risk = assess(facts, context)
            speak = spoken_lines
        confirmation = confirmation_for(risk.level, settings.high_risk_pause_seconds)
        return {
            **summary,
            "risk": {
                "level": risk.level,
                "score": risk.score,
                "hits": [asdict(hit) for hit in risk.hits],
            },
            "confirmation": confirmation,
            "spoken": speak(summary, risk, confirmation),
        }

    @app.post("/guard/preview")
    async def guard_preview(ref: OrderRef) -> dict:
        """The same verdict before approval, so a bad order can be refused before PayPal opens.

        Records nothing: only the check after approval counts.
        """
        decision = require_decision(ref.order_id)
        order = await app.state.paypal.get_order(ref.order_id)
        return evaluate(decision, order)

    @app.post("/guard/check")
    async def guard_check(ref: OrderRef) -> dict:
        decision = require_decision(ref.order_id)
        order = await app.state.paypal.get_order(ref.order_id)
        if order["status"] != "APPROVED":
            raise HTTPException(409, f"Order is {order['status']}, not APPROVED")
        verdict = evaluate(decision, order)
        if not app.state.log.mark_checked(
            ref.order_id,
            catalog.to_cents(verdict["total"]),
            verdict["payee"],
            verdict["risk"]["level"],
            verdict["risk"]["score"],
            json.dumps(verdict["risk"]["hits"]),
            time.time(),
            json.dumps(verdict),
        ):
            raise HTTPException(409, "Order is already decided")
        return verdict

    @app.get("/receipts/{order_id}")
    async def receipt(order_id: str) -> dict:
        """What was asked, what PayPal's order said, what the user was told, and the decision.

        The evidence a dispute over an agent's purchase would need, and no card rule asks for yet.
        """
        row = app.state.log.receipt_row(order_id)
        if row is None:
            raise HTTPException(404, "Unknown order")
        asked = None
        if row["mandate_id"]:
            mandate = app.state.mandates.get(row["mandate_id"])
            asked = {
                "request": mandate.request_text,
                "mandate": mandate.confirmed["fields"],
                "read_back": mandate_lines(mandate.confirmed),
            }
        return {
            "order_id": order_id,
            "decision": row["status"],
            "decided_at": row["updated_at"],
            "capture_id": row["capture_id"],
            "asked": asked,
            "shown": json.loads(row["shown"]) if row["shown"] else None,
        }

    @app.post("/guard/confirm")
    async def guard_confirm(ref: ConfirmRequest) -> dict:
        """The only route that moves money."""
        decision = require_decision(ref.order_id)
        if decision.status == "captured":
            # A double press gets the first result, not a second charge.
            return {"status": "captured", "capture_id": decision.capture_id}
        if decision.status == "checked":
            require_confirmation(decision, ref, settings.high_risk_pause_seconds)
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

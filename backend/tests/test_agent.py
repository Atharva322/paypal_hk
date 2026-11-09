import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.guard.mandate_rules import Mandate, assess_against_mandate
from app.guard.rules import OrderFacts
from app.intent import MandateDraft
from app.main import create_app
from app.shop import Shop, ShopError
from app.stores import known_stores

from .fake_paypal import PAYEE, FakePayPal

LOOKALIKE_PAYEE = "outlet@trai1head.example"

SHOES_DRAFT = MandateDraft(
    item="running shoes", brand=None, size="10", details=[], quantity=1,
    max_total="80.00", currency="USD", store="Trailhead", questions=[],
)
CONFIRM = {"item": "running shoes", "size": "10", "quantity": 1, "max_total": "80",
           "currency": "USD", "store": "Trailhead"}


class ScriptedModel:
    """Stands in for the Responses API: replays tool calls, records what it was sent."""

    def __init__(self, *turns: list[tuple[str, dict]]):
        self.turns = list(turns)
        self.requests: list[dict] = []
        self.responses = SimpleNamespace(create=self._create)

    async def _create(self, **request):
        self.requests.append(request)
        calls = self.turns.pop(0) if self.turns else []
        output = [
            SimpleNamespace(type="function_call", name=name, arguments=json.dumps(args),
                            call_id=f"call{len(self.requests)}{i}")
            for i, (name, args) in enumerate(calls)
        ]
        return SimpleNamespace(id=f"resp{len(self.requests)}", output=output, output_text="Done.")


def buy(*lines: tuple[str, int], size="10") -> list[tuple[str, dict]]:
    items = [{"product_id": pid, "quantity": quantity} for pid, quantity in lines]
    return [("create_order", {"items": items, "size": size})]


def make_client(tmp_path, model, **overrides) -> tuple[TestClient, FakePayPal]:
    paypal = FakePayPal()

    async def reader(_: str) -> MandateDraft:
        return SHOES_DRAFT

    defaults = {
        "database_path": str(tmp_path / "test.db"),
        "allowed_merchant_ids": frozenset({PAYEE["merchant_id"]}),
        "demo_attacks_enabled": True,
        "demo_lookalike_payee": LOOKALIKE_PAYEE,
    }
    settings = Settings("id", "secret", **{**defaults, **overrides})
    app = create_app(settings, httpx.MockTransport(paypal.handler), reader, model)
    return TestClient(app), paypal


def confirmed_mandate(client) -> str:
    mandate_id = client.post("/mandates", json={"request": "shoes"}).json()["mandate_id"]
    assert client.post(f"/mandates/{mandate_id}/confirm", json=CONFIRM).status_code == 200
    return mandate_id


def run(client, mandate_id, scenario="clean"):
    return client.post("/agent/runs", json={"mandate_id": mandate_id, "scenario": scenario})


def test_clean_run_creates_an_order_the_guard_passes(tmp_path):
    model = ScriptedModel(
        [("search_products", {"query": "running shoes"})],
        [("view_product", {"product_id": "th-road"})],
        buy(("th-road", 1)),
    )
    client, paypal = make_client(tmp_path, model)
    with client:
        response = run(client, confirmed_mandate(client))
    body = response.json()
    assert body["order"]["total"] == "54.00"
    assert [step["tool"] for step in body["steps"]] == ["search_products", "view_product", "create_order"]
    order = paypal.orders[body["order"]["order_id"]]
    assert order["purchase_units"][0]["payee"] == PAYEE
    mandate = Mandate("running shoes", None, "10", (), 1, 8000,
                      *_stores_and_trailhead())
    assert assess_against_mandate(OrderFacts.from_paypal(order), mandate).level == "Low"


def _stores_and_trailhead():
    stores = known_stores(Settings("id", "secret", allowed_merchant_ids=frozenset({"MERCHANT1"})))
    return stores[0], stores


def test_the_agent_sees_the_mandate_not_the_raw_request(tmp_path):
    model = ScriptedModel(buy(("th-road", 1)))
    client, _ = make_client(tmp_path, model)
    with client:
        run(client, confirmed_mandate(client))
    brief = model.requests[0]["input"]
    assert "Spend at most: 80 USD" in brief
    assert "Store: Trailhead Running Co." in brief


def test_an_injected_page_reaches_the_agent_in_the_gift_card_scenario(tmp_path):
    model = ScriptedModel([("view_product", {"product_id": "th-road"})], buy(("th-road", 1), ("th-gift-25", 1)))
    client, paypal = make_client(tmp_path, model)
    with client:
        body = run(client, confirmed_mandate(client), "gift_card").json()
    assert "th-gift-25" in body["steps"][0]["result"]
    items = paypal.orders[body["order"]["order_id"]]["purchase_units"][0]["items"]
    assert [item["sku"] for item in items] == ["th-road", "th-gift-25"]


def test_lookalike_scenario_orders_carry_the_lookalike_payee(tmp_path):
    model = ScriptedModel(buy(("tl-road", 1)))
    client, paypal = make_client(tmp_path, model)
    with client:
        body = run(client, confirmed_mandate(client), "lookalike").json()
    unit = paypal.orders[body["order"]["order_id"]]["purchase_units"][0]
    assert unit["payee"] == {"email_address": LOOKALIKE_PAYEE}
    assert unit["soft_descriptor"] == "Trai1head Running"
    mandate = Mandate("running shoes", None, "10", (), 1, 8000, *_stores_and_trailhead())
    risk = assess_against_mandate(OrderFacts.from_paypal(paypal.orders[body["order"]["order_id"]]), mandate)
    assert {hit.code for hit in risk.hits} == {"wrong_seller", "lookalike_seller"}


def test_shop_errors_go_back_to_the_agent_so_it_can_correct_itself(tmp_path):
    model = ScriptedModel(buy(("th-road", 1), size="99"), buy(("th-road", 1)))
    client, _ = make_client(tmp_path, model)
    with client:
        body = run(client, confirmed_mandate(client)).json()
    assert body["steps"][0]["result"].startswith("Error: ")
    assert body["order"]["total"] == "54.00"


def test_the_agent_stops_after_one_order(tmp_path):
    model = ScriptedModel(buy(("th-road", 1)) + buy(("th-socks", 1)))
    client, paypal = make_client(tmp_path, model)
    with client:
        run(client, confirmed_mandate(client))
    assert len(paypal.orders) == 1


def test_orders_are_linked_to_their_mandate(tmp_path):
    model = ScriptedModel(buy(("th-road", 1)))
    client, _ = make_client(tmp_path, model)
    with client:
        mandate_id = confirmed_mandate(client)
        order_id = run(client, mandate_id).json()["order"]["order_id"]
        assert client.app.state.log.get(order_id).mandate_id == mandate_id


def test_an_unconfirmed_mandate_cannot_shop(tmp_path):
    client, _ = make_client(tmp_path, ScriptedModel())
    with client:
        draft_id = client.post("/mandates", json={"request": "shoes"}).json()["mandate_id"]
        assert run(client, draft_id).status_code == 409


def test_attack_scenarios_need_demo_mode(tmp_path):
    client, _ = make_client(tmp_path, ScriptedModel(), demo_attacks_enabled=False)
    with client:
        assert run(client, confirmed_mandate(client), "gift_card").status_code == 403


def test_one_order_per_store():
    shop = Shop("lookalike", LOOKALIKE_PAYEE)
    with pytest.raises(ShopError, match="One order per store"):
        shop.order_body([{"product_id": "th-socks", "quantity": 1},
                         {"product_id": "tl-road", "quantity": 1}], "10")


def test_size_10_is_sold_out_only_in_the_lookalike_scenario():
    assert "10" in Shop("clean").search("road shoes")[0]["sizes_in_stock"]
    with pytest.raises(ShopError, match="not in stock in size 10"):
        Shop("lookalike", LOOKALIKE_PAYEE).order_body([{"product_id": "th-road", "quantity": 1}], "10")


def test_the_gift_card_is_never_listed_in_search():
    assert all(p["product_id"] != "th-gift-25" for p in Shop("gift_card").search("gift card"))


def stream(client, mandate_id, scenario="clean") -> list[dict]:
    response = client.post("/agent/runs/stream", json={"mandate_id": mandate_id, "scenario": scenario})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson"
    return [json.loads(line) for line in response.text.splitlines()]


def test_the_stream_sends_each_step_then_the_result(tmp_path):
    model = ScriptedModel(
        [("search_products", {"query": "running shoes"})],
        [("view_product", {"product_id": "th-road"})],
        buy(("th-road", 1)),
    )
    client, _ = make_client(tmp_path, model)
    with client:
        events = stream(client, confirmed_mandate(client))
    assert [event["type"] for event in events] == ["step", "step", "step", "done"]
    assert [event["step"]["tool"] for event in events[:3]] == [
        "search_products", "view_product", "create_order"
    ]
    assert events[-1]["run"]["order"]["total"] == "54.00"
    assert events[-1]["run"]["steps"] == [event["step"] for event in events[:3]]


def test_a_refused_run_is_a_plain_error_not_a_stream(tmp_path):
    client, _ = make_client(tmp_path, ScriptedModel())
    with client:
        draft_id = client.post("/mandates", json={"request": "shoes"}).json()["mandate_id"]
        response = client.post("/agent/runs/stream", json={"mandate_id": draft_id})
    assert response.status_code == 409


def test_a_model_failure_mid_run_ends_the_stream_with_an_error(tmp_path):
    class Unreachable:
        async def _create(self, **_):
            raise openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))

        responses = SimpleNamespace()

    model = Unreachable()
    model.responses.create = model._create
    client, paypal = make_client(tmp_path, model)
    with client:
        events = stream(client, confirmed_mandate(client))
    assert events == [
        {"type": "error", "detail": "The agent could not reach its model. Try again."}
    ]
    assert paypal.orders == {}


def test_the_shop_prices_and_totals_every_order_itself():
    body, total, store = Shop("clean").order_body(
        [{"product_id": "th-road", "quantity": 1}, {"product_id": "th-socks", "quantity": 2}], "10"
    )
    assert total == 78_00
    assert body["purchase_units"][0]["amount"] == {
        "currency_code": "USD",
        "value": "78.00",
        "breakdown": {"item_total": {"currency_code": "USD", "value": "78.00"}},
    }
    assert store.name == "Trailhead Running Co."


@pytest.mark.parametrize(
    ("line", "error"),
    [
        ({"product_id": "nope", "quantity": 1}, "No product nope"),
        ({"product_id": "th-socks", "quantity": 0}, "Quantity must be"),
        ({"product_id": "th-socks", "quantity": 6}, "Quantity must be"),
    ],
)
def test_the_shop_refuses_unknown_products_and_quantities(line, error):
    with pytest.raises(ShopError, match=error):
        Shop("clean").order_body([line], "10")

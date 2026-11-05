import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.intent import MandateDraft
from app.main import create_app
from app.stores import known_stores, find_store

from .fake_paypal import PAYEE, FakePayPal

SHOES = MandateDraft(
    item="running shoes",
    brand=None,
    size="10",
    details=[],
    quantity=1,
    max_total="80.00",
    currency="USD",
    store="Trailhead",
    questions=[],
)

CONFIRMED_FIELDS = {
    "item": "running shoes",
    "size": "10",
    "quantity": 1,
    "max_total": "80",
    "currency": "USD",
    "store": "Trailhead Running Co.",
}


def make_client(tmp_path, draft=SHOES, error: Exception | None = None) -> TestClient:
    async def reader(_: str) -> MandateDraft:
        if error:
            raise error
        return draft

    settings = Settings(
        "id",
        "secret",
        database_path=str(tmp_path / "test.db"),
        allowed_merchant_ids=frozenset({PAYEE["merchant_id"]}),
    )
    app = create_app(settings, httpx.MockTransport(FakePayPal().handler), reader)
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as test_client:
        yield test_client


def draft(client) -> dict:
    response = client.post("/mandates", json={"request": "Trailhead running shoes size 10 under $80"})
    assert response.status_code == 200
    return response.json()


def test_draft_resolves_a_known_store_and_reads_back(client):
    body = draft(client)
    assert body["status"] == "draft"
    assert body["store"] == "Trailhead Running Co."
    assert body["max_total_cents"] == 8000
    assert body["problems"] == []
    assert body["spoken"] == [
        "Buy 1 running shoes, size 10.",
        "Spend at most 80 dollars, US dollars, in total.",
        "Only from Trailhead Running Co., a store you've paid before.",
    ]


def test_missing_budget_blocks_confirmation(tmp_path):
    with make_client(tmp_path, SHOES.model_copy(update={"max_total": None})) as client:
        body = draft(client)
        assert "Set a spending limit" in body["problems"][0]
        fields = {**CONFIRMED_FIELDS, "max_total": None}
        response = client.post(f"/mandates/{body['mandate_id']}/confirm", json=fields)
        assert response.status_code == 422


def test_a_store_never_paid_is_not_trusted(tmp_path):
    with make_client(tmp_path, SHOES.model_copy(update={"store": "Trai1head Running"})) as client:
        body = draft(client)
        assert body["store"] is None
        assert "haven't paid Trai1head Running before" in body["problems"][0]


def test_model_questions_are_read_back(tmp_path):
    asking = SHOES.model_copy(update={"size": None, "questions": ["What size do you need?"]})
    with make_client(tmp_path, asking) as client:
        assert draft(client)["spoken"][-1] == "What size do you need?"


def test_confirm_uses_the_users_fields_not_the_draft(client):
    mandate_id = draft(client)["mandate_id"]
    corrected = {**CONFIRMED_FIELDS, "max_total": "60.00"}
    response = client.post(f"/mandates/{mandate_id}/confirm", json=corrected)
    assert response.status_code == 200
    assert response.json()["max_total_cents"] == 6000
    stored = client.get(f"/mandates/{mandate_id}").json()
    assert stored["status"] == "confirmed"
    assert stored["max_total_cents"] == 6000


def test_a_confirmed_mandate_cannot_be_rewritten(client):
    mandate_id = draft(client)["mandate_id"]
    assert client.post(f"/mandates/{mandate_id}/confirm", json=CONFIRMED_FIELDS).status_code == 200
    bigger = {**CONFIRMED_FIELDS, "max_total": "5000"}
    assert client.post(f"/mandates/{mandate_id}/confirm", json=bigger).status_code == 409
    assert client.get(f"/mandates/{mandate_id}").json()["max_total_cents"] == 8000


@pytest.mark.parametrize(
    "change",
    [
        {"max_total": "0"},
        {"max_total": "-5"},
        {"max_total": "nan"},
        {"max_total": "99999999"},
        {"currency": "EUR"},
    ],
)
def test_bad_limits_are_refused(client, change):
    mandate_id = draft(client)["mandate_id"]
    response = client.post(f"/mandates/{mandate_id}/confirm", json={**CONFIRMED_FIELDS, **change})
    assert response.status_code == 422


def test_unknown_fields_are_refused(client):
    mandate_id = draft(client)["mandate_id"]
    sneaky = {**CONFIRMED_FIELDS, "payee": "attacker@example.com"}
    assert client.post(f"/mandates/{mandate_id}/confirm", json=sneaky).status_code == 422


def test_assistant_failure_is_reported_not_raised(tmp_path):
    with make_client(tmp_path, error=openai.APIConnectionError(request=httpx.Request("POST", "x"))) as client:
        response = client.post("/mandates", json={"request": "shoes"})
        assert response.status_code == 502


def test_without_a_key_the_assistant_is_off(tmp_path):
    settings = Settings("id", "secret", database_path=str(tmp_path / "test.db"))
    with TestClient(create_app(settings, httpx.MockTransport(FakePayPal().handler))) as client:
        assert client.post("/mandates", json={"request": "shoes"}).status_code == 503


@pytest.mark.parametrize(
    ("name", "found"),
    [
        ("Trailhead", True),
        ("the official Trailhead store", True),
        ("TRAILHEAD RUNNING CO", True),
        ("Trai1head Running", False),
        ("Trailhead Outlet", False),
    ],
)
def test_only_exact_store_names_match(name, found):
    stores = known_stores(Settings("id", "secret"))
    assert (find_store(name, stores) is not None) == found

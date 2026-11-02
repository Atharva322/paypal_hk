import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from .fake_paypal import FakePayPal

EARBUDS = {"items": [{"product_id": "earbuds", "quantity": 1}]}


@pytest.fixture
def paypal():
    return FakePayPal()


@pytest.fixture
def client(paypal, tmp_path):
    settings = Settings("id", "secret", database_path=str(tmp_path / "test.db"))
    app = create_app(settings, httpx.MockTransport(paypal.handler))
    with TestClient(app) as test_client:
        yield test_client


def create_order(client) -> str:
    response = client.post("/orders", json=EARBUDS)
    assert response.status_code == 200
    return response.json()["order_id"]


def checked_order(client, paypal) -> str:
    order_id = create_order(client)
    paypal.approve(order_id)
    assert client.post("/guard/check", json={"order_id": order_id}).status_code == 200
    return order_id


def test_order_uses_server_price(client, paypal):
    order_id = create_order(client)
    amount = paypal.orders[order_id]["purchase_units"][0]["amount"]
    assert amount["value"] == "49.99"
    assert amount["currency_code"] == "USD"


def test_quantities_are_totalled_on_the_server(client, paypal):
    response = client.post("/orders", json={"items": [{"product_id": "case", "quantity": 3}]})
    order = paypal.orders[response.json()["order_id"]]
    assert order["purchase_units"][0]["amount"]["value"] == "47.97"


def test_browser_cannot_send_a_price(client):
    body = {"items": [{"product_id": "earbuds", "quantity": 1, "price": "0.01"}]}
    assert client.post("/orders", json=body).status_code == 422


def test_unknown_product_is_rejected(client, paypal):
    body = {"items": [{"product_id": "nope", "quantity": 1}]}
    assert client.post("/orders", json=body).status_code == 422
    assert paypal.orders == {}


def test_token_is_cached(client, paypal):
    create_order(client)
    create_order(client)
    assert paypal.token_calls == 1


def test_check_returns_paypals_record(client, paypal):
    order_id = create_order(client)
    paypal.approve(order_id)
    summary = client.post("/guard/check", json={"order_id": order_id}).json()
    assert summary["payee"] == "merchant@example.com"
    assert summary["total"] == "49.99"
    assert summary["currency"] == "USD"
    assert summary["items"] == [
        {"name": "Wireless earbuds", "quantity": 1, "unit_price": "49.99"}
    ]


def test_check_refuses_unapproved_order(client):
    order_id = create_order(client)
    assert client.post("/guard/check", json={"order_id": order_id}).status_code == 409


def test_confirm_captures_once_with_request_id(client, paypal):
    order_id = checked_order(client, paypal)
    response = client.post("/guard/confirm", json={"order_id": order_id})
    assert response.json() == {"status": "captured", "capture_id": "CAPTURE1"}
    assert paypal.captures == [order_id]


def test_double_confirm_does_not_capture_twice(client, paypal):
    order_id = checked_order(client, paypal)
    first = client.post("/guard/confirm", json={"order_id": order_id})
    second = client.post("/guard/confirm", json={"order_id": order_id})
    assert first.json() == second.json()
    assert paypal.captures == [order_id]


def test_confirm_without_check_never_captures(client, paypal):
    order_id = create_order(client)
    paypal.approve(order_id)
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 409
    assert paypal.captures == []


def test_cancel_then_confirm_never_captures(client, paypal):
    order_id = checked_order(client, paypal)
    assert client.post("/guard/cancel", json={"order_id": order_id}).status_code == 200
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 409
    assert paypal.captures == []


def test_cancelled_order_cannot_be_rechecked(client, paypal):
    order_id = checked_order(client, paypal)
    client.post("/guard/cancel", json={"order_id": order_id})
    assert client.post("/guard/check", json={"order_id": order_id}).status_code == 409


def test_cancel_after_capture_is_refused(client, paypal):
    order_id = checked_order(client, paypal)
    client.post("/guard/confirm", json={"order_id": order_id})
    assert client.post("/guard/cancel", json={"order_id": order_id}).status_code == 409


def test_amount_changed_after_check_never_captures(client, paypal):
    order_id = checked_order(client, paypal)
    paypal.orders[order_id]["purchase_units"][0]["amount"]["value"] = "499.90"
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 409
    assert paypal.captures == []


def test_payee_changed_after_check_never_captures(client, paypal):
    order_id = checked_order(client, paypal)
    paypal.orders[order_id]["purchase_units"][0]["payee"]["email_address"] = "evil@example.com"
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 409
    assert paypal.captures == []


def test_failed_capture_can_be_retried(client, paypal):
    order_id = checked_order(client, paypal)
    paypal.fail_next_capture = True
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 502
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 200
    assert paypal.captures == [order_id]


def test_lost_capture_reply_blocks_cancel_and_retry_charges_once(client, paypal):
    order_id = checked_order(client, paypal)
    paypal.drop_next_capture_reply = True
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 504
    assert client.post("/guard/cancel", json={"order_id": order_id}).status_code == 409
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 200
    assert paypal.captures == [order_id]


def test_unknown_order_is_404_on_every_guard_route(client):
    for route in ("/guard/check", "/guard/confirm", "/guard/cancel"):
        assert client.post(route, json={"order_id": "NOPE"}).status_code == 404

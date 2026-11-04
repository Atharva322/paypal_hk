import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from .fake_paypal import PAYEE, FakePayPal

EARBUDS = {"items": [{"product_id": "earbuds", "quantity": 1}]}


@pytest.fixture
def paypal():
    return FakePayPal()


def make_client(paypal, tmp_path, **settings_overrides) -> TestClient:
    settings = Settings(
        "id",
        "secret",
        database_path=str(tmp_path / "test.db"),
        allowed_merchant_ids=frozenset({PAYEE["merchant_id"]}),
        **settings_overrides,
    )
    return TestClient(create_app(settings, httpx.MockTransport(paypal.handler)))


@pytest.fixture
def client(paypal, tmp_path):
    with make_client(paypal, tmp_path) as test_client:
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


def test_clean_order_checks_low(client, paypal):
    order_id = create_order(client)
    paypal.approve(order_id)
    risk = client.post("/guard/check", json={"order_id": order_id}).json()["risk"]
    assert risk == {"level": "Low", "score": 0, "hits": []}


def test_order_edited_at_paypal_checks_high_against_the_stored_cart(client, paypal):
    order_id = create_order(client)
    paypal.orders[order_id]["purchase_units"][0]["amount"]["value"] = "499.90"
    paypal.approve(order_id)
    risk = client.post("/guard/check", json={"order_id": order_id}).json()["risk"]
    assert risk["level"] == "High"
    assert risk["hits"][0]["reason"] == "The total changed from 49.99 to 499.90."


def test_risk_is_written_to_the_decision_log(client, paypal):
    order_id = checked_order(client, paypal)
    decision = client.app.state.log.get(order_id)
    with sqlite3.connect(client.app.state.log._path) as db:
        row = db.execute(
            "SELECT risk_level, risk_score, rule_hits FROM decisions WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    assert decision.status == "checked"
    assert row == ("Low", 0, "[]")


def test_log_from_before_risk_columns_is_upgraded(paypal, tmp_path):
    with sqlite3.connect(tmp_path / "test.db") as db:
        db.execute(
            "CREATE TABLE decisions (order_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "cart_total_cents INTEGER NOT NULL, currency TEXT NOT NULL, "
            "checked_total_cents INTEGER, checked_payee TEXT, capture_id TEXT, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    with make_client(paypal, tmp_path) as upgraded:
        checked_order(upgraded, paypal)


def test_demo_attack_is_refused_unless_enabled(client, paypal):
    response = client.post("/orders", json={**EARBUDS, "demo_attack": "tampered_total"})
    assert response.status_code == 403
    assert paypal.orders == {}


def test_demo_attack_is_caught_and_cancel_captures_nothing(paypal, tmp_path):
    with make_client(paypal, tmp_path, demo_attacks_enabled=True) as client:
        body = {**EARBUDS, "demo_attack": "tampered_total"}
        order_id = client.post("/orders", json=body).json()["order_id"]
        assert paypal.orders[order_id]["purchase_units"][0]["amount"]["value"] == "499.90"
        paypal.approve(order_id)
        risk = client.post("/guard/check", json={"order_id": order_id}).json()["risk"]
        assert risk["level"] == "High"
        assert {"price_mismatch", "currency_trick"} <= {hit["code"] for hit in risk["hits"]}
        assert client.post("/guard/cancel", json={"order_id": order_id}).status_code == 200
    assert paypal.captures == []


def flagged_order(client, paypal, value: str = "60.00", extras: bool = False) -> str:
    """An approved, checked order: tampered total for High, added shipping for Medium."""
    order_id = create_order(client)
    amount = paypal.orders[order_id]["purchase_units"][0]["amount"]
    if extras:
        amount["breakdown"]["shipping"] = {"currency_code": "USD", "value": "5.00"}
        amount["value"] = "49.99"
    else:
        amount["value"] = value
    paypal.approve(order_id)
    assert client.post("/guard/check", json={"order_id": order_id}).status_code == 200
    return order_id


def test_medium_risk_needs_acknowledgement(client, paypal):
    order_id = flagged_order(client, paypal, extras=True)
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 403
    assert paypal.captures == []
    body = {"order_id": order_id, "acknowledged_risk": True}
    assert client.post("/guard/confirm", json=body).status_code == 200


def test_high_risk_refuses_a_plain_confirm(paypal, tmp_path):
    with make_client(paypal, tmp_path, high_risk_pause_seconds=0) as client:
        order_id = flagged_order(client, paypal)
        body = {"order_id": order_id, "acknowledged_risk": True}
        assert client.post("/guard/confirm", json=body).status_code == 403
    assert paypal.captures == []


@pytest.mark.parametrize("typed", ["49.99", "60", "sixty", "", "nan", "inf"])
def test_high_risk_refuses_a_wrong_total(paypal, tmp_path, typed):
    with make_client(paypal, tmp_path, high_risk_pause_seconds=0) as client:
        order_id = flagged_order(client, paypal)
        body = {"order_id": order_id, "typed_total": typed if typed != "60" else "60.01"}
        assert client.post("/guard/confirm", json=body).status_code == 403
    assert paypal.captures == []


@pytest.mark.parametrize("typed", ["60.00", "60", "$60.00", " 60.00 "])
def test_high_risk_accepts_the_real_total_after_the_pause(paypal, tmp_path, typed):
    with make_client(paypal, tmp_path, high_risk_pause_seconds=0) as client:
        order_id = flagged_order(client, paypal)
        body = {"order_id": order_id, "typed_total": typed}
        assert client.post("/guard/confirm", json=body).status_code == 200
    assert paypal.captures == [order_id]


def test_high_risk_refuses_inside_the_pause(paypal, tmp_path):
    with make_client(paypal, tmp_path, high_risk_pause_seconds=10) as client:
        order_id = flagged_order(client, paypal)
        body = {"order_id": order_id, "typed_total": "60.00"}
        response = client.post("/guard/confirm", json=body)
    assert response.status_code == 403
    assert response.json()["detail"] == "The pause has not finished"
    assert paypal.captures == []


def test_check_tells_the_dialog_how_to_confirm(paypal, tmp_path):
    with make_client(paypal, tmp_path) as client:
        order_id = create_order(client)
        paypal.orders[order_id]["purchase_units"][0]["amount"]["value"] = "60.00"
        paypal.approve(order_id)
        result = client.post("/guard/check", json={"order_id": order_id}).json()
    assert result["confirmation"] == {"method": "type_total", "pause_seconds": 10}
    assert result["spoken"][0] == "Warning: high risk. The total changed from 49.99 to 60.00."


def test_unknown_order_is_404_on_every_guard_route(client):
    for route in ("/guard/check", "/guard/confirm", "/guard/cancel"):
        assert client.post(route, json={"order_id": "NOPE"}).status_code == 404

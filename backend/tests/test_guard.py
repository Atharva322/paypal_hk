"""The guard's check, confirm and cancel: capture happens once, only as the user decided."""

import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.intent import MandateDraft
from app.main import create_app
from app.shop import Shop

from .fake_paypal import PAYEE, FakePayPal
from .test_agent import CONFIRM, SHOES_DRAFT

SHOES = [{"product_id": "th-road", "quantity": 1}]
# Socks nobody asked for: one Medium rule, and still under the $80 limit.
SHOES_AND_SOCKS = [*SHOES, {"product_id": "th-socks", "quantity": 1}]


@pytest.fixture
def paypal():
    return FakePayPal()


def make_client(paypal, tmp_path, **settings_overrides) -> TestClient:
    async def reader(_: str) -> MandateDraft:
        return SHOES_DRAFT

    settings = Settings(
        "id",
        "secret",
        database_path=str(tmp_path / "test.db"),
        allowed_merchant_ids=frozenset({PAYEE["merchant_id"]}),
        **settings_overrides,
    )
    return TestClient(create_app(settings, httpx.MockTransport(paypal.handler), reader))


@pytest.fixture
def client(paypal, tmp_path):
    with make_client(paypal, tmp_path) as test_client:
        yield test_client


def create_order(client, lines=SHOES) -> str:
    """An order as the agent places it: for a confirmed mandate, priced by the shop."""
    mandate_id = client.post("/mandates", json={"request": "shoes"}).json()["mandate_id"]
    assert client.post(f"/mandates/{mandate_id}/confirm", json=CONFIRM).status_code == 200
    body, total_cents, _ = Shop("clean").order_body(lines, "10")
    order = client.portal.call(client.app.state.paypal.create_order, body)
    client.app.state.log.record_created(order["id"], total_cents, "USD", mandate_id)
    return order["id"]


def checked_order(client, paypal, lines=SHOES) -> str:
    order_id = create_order(client, lines)
    paypal.approve(order_id)
    assert client.post("/guard/check", json={"order_id": order_id}).status_code == 200
    return order_id


def over_budget_order(client, paypal) -> str:
    """Edited at PayPal to 90.00, over the $80 limit: High."""
    order_id = create_order(client)
    paypal.orders[order_id]["purchase_units"][0]["amount"]["value"] = "90.00"
    paypal.approve(order_id)
    assert client.post("/guard/check", json={"order_id": order_id}).status_code == 200
    return order_id


def test_token_is_cached(client, paypal):
    create_order(client)
    create_order(client)
    assert paypal.token_calls == 1


def test_check_returns_paypals_record(client, paypal):
    order_id = create_order(client)
    paypal.approve(order_id)
    summary = client.post("/guard/check", json={"order_id": order_id}).json()
    assert summary["payee"] == "merchant@example.com"
    assert summary["payee_name"] == "Trailhead Running Co."
    assert summary["total"] == "54.00"
    assert summary["currency"] == "USD"
    assert summary["items"] == [
        {"name": "Trailhead Road running shoes, size 10", "quantity": 1, "unit_price": "54.00"}
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
    paypal.orders[order_id]["purchase_units"][0]["amount"]["value"] = "540.00"
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


def test_matching_order_checks_low(client, paypal):
    order_id = create_order(client)
    paypal.approve(order_id)
    risk = client.post("/guard/check", json={"order_id": order_id}).json()["risk"]
    assert risk == {"level": "Low", "score": 0, "hits": []}


def test_order_edited_at_paypal_is_judged_on_paypals_record(client, paypal):
    order_id = create_order(client)
    paypal.orders[order_id]["purchase_units"][0]["amount"]["value"] = "540.00"
    paypal.approve(order_id)
    risk = client.post("/guard/check", json={"order_id": order_id}).json()["risk"]
    assert risk["level"] == "High"
    assert "over_budget" in {hit["code"] for hit in risk["hits"]}


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


def test_medium_risk_needs_acknowledgement(client, paypal):
    order_id = checked_order(client, paypal, SHOES_AND_SOCKS)
    assert client.app.state.log.get(order_id).risk_level == "Medium"
    assert client.post("/guard/confirm", json={"order_id": order_id}).status_code == 403
    assert paypal.captures == []
    body = {"order_id": order_id, "acknowledged_risk": True}
    assert client.post("/guard/confirm", json=body).status_code == 200


def test_high_risk_refuses_a_plain_confirm(paypal, tmp_path):
    with make_client(paypal, tmp_path, high_risk_pause_seconds=0) as client:
        order_id = over_budget_order(client, paypal)
        body = {"order_id": order_id, "acknowledged_risk": True}
        assert client.post("/guard/confirm", json=body).status_code == 403
    assert paypal.captures == []


@pytest.mark.parametrize("typed", ["54.00", "90.01", "ninety", "", "nan", "inf"])
def test_high_risk_refuses_a_wrong_total(paypal, tmp_path, typed):
    with make_client(paypal, tmp_path, high_risk_pause_seconds=0) as client:
        order_id = over_budget_order(client, paypal)
        body = {"order_id": order_id, "typed_total": typed}
        assert client.post("/guard/confirm", json=body).status_code == 403
    assert paypal.captures == []


@pytest.mark.parametrize("typed", ["90.00", "90", "$90.00", " 90.00 "])
def test_high_risk_accepts_the_real_total_after_the_pause(paypal, tmp_path, typed):
    with make_client(paypal, tmp_path, high_risk_pause_seconds=0) as client:
        order_id = over_budget_order(client, paypal)
        body = {"order_id": order_id, "typed_total": typed}
        assert client.post("/guard/confirm", json=body).status_code == 200
    assert paypal.captures == [order_id]


def test_high_risk_refuses_inside_the_pause(paypal, tmp_path):
    with make_client(paypal, tmp_path, high_risk_pause_seconds=10) as client:
        order_id = over_budget_order(client, paypal)
        body = {"order_id": order_id, "typed_total": "90.00"}
        response = client.post("/guard/confirm", json=body)
    assert response.status_code == 403
    assert response.json()["detail"] == "The pause has not finished"
    assert paypal.captures == []


def test_check_tells_the_dialog_how_to_confirm(paypal, tmp_path):
    with make_client(paypal, tmp_path) as client:
        order_id = create_order(client)
        paypal.orders[order_id]["purchase_units"][0]["amount"]["value"] = "90.00"
        paypal.approve(order_id)
        result = client.post("/guard/check", json={"order_id": order_id}).json()
    assert result["confirmation"] == {"method": "type_total", "pause_seconds": 10}
    assert result["spoken"][0] == "Warning: high risk. This order is not what you asked for."


def test_unknown_order_is_404_on_every_guard_route(client):
    for route in ("/guard/check", "/guard/preview", "/guard/confirm", "/guard/cancel"):
        assert client.post(route, json={"order_id": "NOPE"}).status_code == 404

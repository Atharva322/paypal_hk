"""The whole flow, per scenario: mandate, agent, preview, approval, check, decision, receipt."""

import time

import pytest

from .test_agent import ScriptedModel, buy, confirmed_mandate, make_client, run

PAUSE = 1


def shop(tmp_path, scenario: str, *lines: tuple[str, int]):
    client, paypal = make_client(tmp_path, ScriptedModel(buy(*lines)), high_risk_pause_seconds=PAUSE)
    client.__enter__()
    mandate_id = confirmed_mandate(client)
    order = run(client, mandate_id, scenario).json()["order"]
    return client, paypal, order


@pytest.fixture
def teardown():
    clients = []
    yield clients.append
    for client in clients:
        client.__exit__(None, None, None)


def checked(client, paypal, order_id) -> dict:
    paypal.approve(order_id)
    response = client.post("/guard/check", json={"order_id": order_id})
    assert response.status_code == 200
    return response.json()


def test_clean_order_previews_low_and_pays_with_one_press(tmp_path, teardown):
    client, paypal, order = shop(tmp_path, "clean", ("th-road", 1))
    teardown(client)
    preview = client.post("/guard/preview", json={"order_id": order["order_id"]}).json()
    assert preview["risk"]["level"] == "Low"
    verdict = checked(client, paypal, order["order_id"])
    assert verdict["payee_name"] == "Trailhead Running Co."
    assert verdict["spoken"][:2] == [
        "This order matches what you asked for.",
        "Paying Trailhead Running Co.",
    ]
    paid = client.post("/guard/confirm", json={"order_id": order["order_id"]})
    assert paid.json()["status"] == "captured"


def test_preview_records_nothing(tmp_path, teardown):
    client, _, order = shop(tmp_path, "clean", ("th-road", 1))
    teardown(client)
    client.post("/guard/preview", json={"order_id": order["order_id"]})
    assert client.app.state.log.get(order["order_id"]).status == "created"


def test_compromised_agent_order_is_high_and_names_the_gift_card(tmp_path, teardown):
    client, paypal, order = shop(tmp_path, "compromised", ("th-road", 1))
    teardown(client)
    assert order["simulated_hijack"] is True
    assert order["total"] == "79.00"  # inside the $80 limit: a spending cap would pass it
    verdict = checked(client, paypal, order["order_id"])
    assert verdict["risk"]["level"] == "High"
    assert "It includes a 25.00 USD gift card you didn't ask for." in verdict["spoken"]
    assert verdict["confirmation"]["method"] == "type_total"


def test_lookalike_order_spells_the_seller(tmp_path, teardown):
    client, paypal, order = shop(tmp_path, "lookalike", ("tl-road", 1))
    teardown(client)
    verdict = checked(client, paypal, order["order_id"])
    assert verdict["payee_name"] is None
    reasons = [hit["reason"] for hit in verdict["risk"]["hits"]]
    assert any("T-R-A-I-1-H-E-A-D" in reason for reason in reasons)


def test_high_risk_needs_the_pause_and_the_typed_total(tmp_path, teardown):
    client, paypal, order = shop(tmp_path, "compromised", ("th-road", 1))
    teardown(client)
    order_id = order["order_id"]
    checked(client, paypal, order_id)
    assert client.post("/guard/confirm", json={"order_id": order_id, "typed_total": "79.00"}).status_code == 403
    time.sleep(PAUSE)
    assert client.post("/guard/confirm", json={"order_id": order_id, "typed_total": "54.00"}).status_code == 403
    paid = client.post("/guard/confirm", json={"order_id": order_id, "typed_total": "79.00"})
    assert paid.json()["status"] == "captured"


def test_cancelled_order_is_never_captured_and_the_receipt_says_so(tmp_path, teardown):
    client, paypal, order = shop(tmp_path, "compromised", ("th-road", 1))
    teardown(client)
    order_id = order["order_id"]
    verdict = checked(client, paypal, order_id)
    assert client.post("/guard/cancel", json={"order_id": order_id}).json()["status"] == "cancelled"
    assert paypal.captures == []

    receipt = client.get(f"/receipts/{order_id}").json()
    assert receipt["decision"] == "cancelled"
    assert receipt["asked"]["request"] == "shoes"
    assert receipt["asked"]["mandate"]["max_total"] == "80"
    assert receipt["shown"]["spoken"] == verdict["spoken"]
    assert receipt["shown"]["risk"]["level"] == "High"


def test_receipt_of_a_paid_order_carries_the_capture(tmp_path, teardown):
    client, paypal, order = shop(tmp_path, "clean", ("th-road", 1))
    teardown(client)
    checked(client, paypal, order["order_id"])
    capture_id = client.post("/guard/confirm", json={"order_id": order["order_id"]}).json()["capture_id"]
    receipt = client.get(f"/receipts/{order['order_id']}").json()
    assert (receipt["decision"], receipt["capture_id"]) == ("captured", capture_id)


def test_an_order_changed_after_the_check_is_not_captured(tmp_path, teardown):
    client, paypal, order = shop(tmp_path, "clean", ("th-road", 1))
    teardown(client)
    checked(client, paypal, order["order_id"])
    paypal.orders[order["order_id"]]["purchase_units"][0]["amount"]["value"] = "540.00"
    assert client.post("/guard/confirm", json={"order_id": order["order_id"]}).status_code == 409
    assert paypal.captures == []

"""Day 3 check: the real agent shops each scenario and creates real sandbox orders.

Run from backend/: python -m scripts.run_scenarios [runs_per_scenario]
Uses the real model and the PayPal sandbox. Prints no credentials.
"""

import sys

from fastapi.testclient import TestClient

from app.config import load_settings
from app.guard.mandate_rules import Mandate, assess_against_mandate
from app.guard.rules import OrderFacts
from app.main import create_app
from app.shop import SCENARIOS
from app.stores import known_stores

REQUEST = "Running shoes from Trailhead, size 10, under $80"
CONFIRM = {"item": "running shoes", "size": "10", "quantity": 1, "max_total": "80",
           "currency": "USD", "store": "Trailhead"}


def main(runs: int) -> None:
    settings = load_settings()
    stores = known_stores(settings)
    with TestClient(create_app(settings)) as client:
        mandate_id = client.post("/mandates", json={"request": REQUEST}).json()["mandate_id"]
        reviewed = client.post(f"/mandates/{mandate_id}/confirm", json=CONFIRM).json()
        mandate = Mandate.from_review(reviewed, stores)
        for scenario in SCENARIOS:
            for number in range(1, runs + 1):
                response = client.post(
                    "/agent/runs", json={"mandate_id": mandate_id, "scenario": scenario}
                )
                body = response.json()
                if response.status_code != 200 or not body["order"]:
                    print(f"{scenario} #{number}: no order ({response.status_code}) "
                          f"{body.get('detail') or body.get('final_text')}")
                    continue
                order = body["order"]
                # The app's PayPal client lives on the test client's event loop.
                paypal_order = client.portal.call(
                    client.app.state.paypal.get_order, order["order_id"]
                )
                risk = assess_against_mandate(OrderFacts.from_paypal(paypal_order), mandate)
                print(f"{scenario} #{number}: {order['store']}, {order['total']}, "
                      f"{order['items']} -> guard {risk.level} {[h.code for h in risk.hits]}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2)

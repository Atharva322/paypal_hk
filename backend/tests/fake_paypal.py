import json

import httpx

PAYEE = {"email_address": "merchant@example.com", "merchant_id": "MERCHANT1"}


class FakePayPal:
    """In-memory Orders API, including PayPal-Request-Id replay."""

    def __init__(self):
        self.orders: dict[str, dict] = {}
        self.token_calls = 0
        self.captures: list[str] = []
        self.capture_replies: dict[str, dict] = {}
        self.fail_next_capture = False
        self.drop_next_capture_reply = False

    def approve(self, order_id: str) -> None:
        self.orders[order_id]["status"] = "APPROVED"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/oauth2/token":
            self.token_calls += 1
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if request.method == "POST" and path == "/v2/checkout/orders":
            order_id = f"ORDER{len(self.orders) + 1}"
            body = json.loads(request.content)
            # Like PayPal: the app's own merchant unless the order names another payee.
            sent = body["purchase_units"][0]
            unit = {**sent, "payee": sent.get("payee") or dict(PAYEE)}
            self.orders[order_id] = {"id": order_id, "status": "CREATED", "purchase_units": [unit]}
            return httpx.Response(201, json={"id": order_id, "status": "CREATED"})
        order_id = path.split("/")[4]
        if request.method == "GET":
            return httpx.Response(200, json=self.orders[order_id])
        return self._capture(order_id, request)

    def _capture(self, order_id: str, request: httpx.Request) -> httpx.Response:
        if self.fail_next_capture:
            self.fail_next_capture = False
            return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY"})
        request_id = request.headers["PayPal-Request-Id"]
        if request_id not in self.capture_replies:
            self.captures.append(order_id)
            self.orders[order_id]["status"] = "COMPLETED"
            self.capture_replies[request_id] = {
                "id": order_id,
                "status": "COMPLETED",
                "purchase_units": [
                    {"payments": {"captures": [{"id": f"CAPTURE{len(self.captures)}"}]}}
                ],
            }
        if self.drop_next_capture_reply:
            # The money moved but our side never hears about it.
            self.drop_next_capture_reply = False
            raise httpx.ReadTimeout("reply lost", request=request)
        return httpx.Response(201, json=self.capture_replies[request_id])

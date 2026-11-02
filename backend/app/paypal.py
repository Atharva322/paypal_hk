import asyncio
import time

import httpx

# Refresh early so a token never expires mid-request.
TOKEN_EXPIRY_MARGIN_SECONDS = 60


class PayPalError(Exception):
    """PayPal answered with an error status, so the call definitely did not succeed."""

    def __init__(self, status_code: int, body: str):
        super().__init__(f"PayPal returned {status_code}: {body}")
        self.status_code = status_code


class PayPalClient:
    def __init__(self, http: httpx.AsyncClient, client_id: str, client_secret: str):
        self._http = http
        self._auth = (client_id, client_secret)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            response = await self._http.post(
                "/v1/oauth2/token",
                auth=self._auth,
                data={"grant_type": "client_credentials"},
            )
            body = self._parse(response)
            self._token = body["access_token"]
            self._token_expires_at = (
                time.monotonic() + body["expires_in"] - TOKEN_EXPIRY_MARGIN_SECONDS
            )
            return self._token

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        headers.update(kwargs.pop("headers", {}))
        return self._parse(await self._http.request(method, path, headers=headers, **kwargs))

    @staticmethod
    def _parse(response: httpx.Response) -> dict:
        if response.is_error:
            raise PayPalError(response.status_code, response.text)
        return response.json()

    async def create_order(self, body: dict) -> dict:
        return await self._request("POST", "/v2/checkout/orders", json=body)

    async def get_order(self, order_id: str) -> dict:
        return await self._request("GET", f"/v2/checkout/orders/{order_id}")

    async def capture_order(self, order_id: str, request_id: str) -> dict:
        # The same PayPal-Request-Id replays the first result instead of charging again.
        return await self._request(
            "POST",
            f"/v2/checkout/orders/{order_id}/capture",
            headers={"PayPal-Request-Id": request_id, "Content-Type": "application/json"},
        )

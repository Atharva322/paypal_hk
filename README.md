# Accessible Pay Guard

A drop-in layer that makes PayPal Checkout safe to use by ear. After the buyer approves and before capture, it reads the real order aloud, runs a scam check, and waits for a deliberate confirmation.

Status: guard checkpoint and rules engine built (phases 0 to 3). See the [architecture and build plan](docs/pay-guard-architecture-and-build-plan.pdf).

## Run locally

Needs Python 3.12+, Node 22+ and a [PayPal developer](https://developer.paypal.com) account.

1. In the developer dashboard, create a sandbox REST app, plus sandbox business and personal accounts.
2. Copy `.env.example` to `.env` and fill in the sandbox client ID and secret. Run the smoke test below, then put the merchant ID it prints into `ALLOWED_MERCHANT_IDS`.
3. Start the backend:

   ```sh
   cd backend
   python -m venv .venv
   .venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
   .venv/Scripts/python -m scripts.sandbox_smoke             # checks your keys
   .venv/Scripts/python -m uvicorn app.asgi:app --reload
   ```

4. In a second terminal, start the store and open http://localhost:5173:

   ```sh
   cd demo-store
   npm install
   npm run dev
   ```

5. Add an item, pay with the sandbox personal account, then confirm or cancel in the review step.

## Test

```sh
cd backend
.venv/Scripts/python -m pytest
```

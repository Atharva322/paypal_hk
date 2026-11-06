# Pay Guard

An intent-locked checkout for AI shopping agents on PayPal. You tell the agent what you want and your limit; it shops and creates a PayPal order; before any money moves, the guard re-reads the order from PayPal and holds it against what you asked for. You hear only what differs, in an accessible confirmation, and decide.

The agent proposes, PayPal holds the truth, and the person approves the differences. The guard's checks are plain code, not a model, so a page that fools the agent cannot talk its way past them.

Status (work in progress):

- Done: request to mandate (AI drafts, code checks, you confirm); shopping agent over mock stores (honest, poisoned product page, lookalike seller, and a labelled simulated hijack) creating real sandbox orders; rules that compare an order with its mandate; a check before PayPal opens and again after approval; the accessible risk-scaled confirmation dialog; a consent receipt of what was asked, shown and decided.
- Next: polish, deployment and the demo video.

The original merchant-side guard is described in the [first build plan](docs/pay-guard-architecture-and-build-plan.pdf).

## Run locally

Needs Python 3.12+, Node 22+ and a [PayPal developer](https://developer.paypal.com) account.

1. In the developer dashboard, create a sandbox REST app, plus sandbox business and personal accounts. For the lookalike scenario, add a second business account named "Trai1head Running".
2. Copy `.env.example` to `.env` and fill in the sandbox client ID and secret, an OpenAI API key, and the lookalike account's email. Run the smoke test below, then put the merchant ID it prints into `ALLOWED_MERCHANT_IDS`.
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

5. Type what the agent should buy, check what it reads back, and confirm the mandate.

To watch the real agent shop all three scenarios and see the guard's verdict on each sandbox order:

```sh
cd backend
.venv/Scripts/python -m scripts.run_scenarios 3
```

## Test

```sh
cd backend
.venv/Scripts/python -m pytest

cd ../guard-ui
npm install
npx playwright test   # real-browser dialog and axe-core checks; uses Edge by default

cd ../demo-store
npm install
npx playwright test   # the whole page flow per scenario, with PayPal's script stubbed
```

Set `PW_CHANNEL=chrome` or `PW_CHANNEL=chromium` to run the browser tests elsewhere.

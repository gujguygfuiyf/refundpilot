# RefundPilot API (FastAPI)

AI reads the evidence, a deterministic policy decides the amount, a merchant approves, PayPal executes the refund.
See `../docs/DESIGN.md` for the full design.

## Run locally

```bash
cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                             # add ,gemini for the backup provider, ,postgres for Render
cp .env.example .env                                # fill PayPal + OpenAI keys
uvicorn app.main:app --reload                       # http://127.0.0.1:8000/docs
```

Without PayPal keys the app uses an in-memory `FakePayPal`; without AI keys it uses `FakeAssessor`. Both are
reported by `GET /health`, so you can build the frontend before any credentials exist.

## Seed demo orders (real sandbox captures)

```bash
python scripts/mint_orders.py --count 8
```

Each order prints an approve link; pay it with the sandbox Personal account, the script captures it and appends to
`seed/orders.json`. The API imports that file on startup (and on `POST /dev/seed`). Mint 25 to 30 before submission:
judges will consume refundable balance.

## Tests

```bash
pytest -q
```

Covers the policy engine, the claim state machine, idempotent approval (double click, PayPal error, timeout +
reconcile replay with the same `PayPal-Request-Id`, PENDING finalised exactly once by a webhook) and the HTTP surface.

## Endpoints

Buyer (anonymous): `GET /orders`, `POST /claims` (multipart: order_id, buyer_text, photos[]), `GET /claims/{id}`,
`GET /claims/{id}/photos/{n}`, `POST /claims/{id}/evidence`, `POST /claims/{id}/cancel`.

Merchant (`Authorization: Bearer $MERCHANT_TOKEN`): `GET /merchant/claims`, `GET /merchant/claims/{id}`,
`POST /merchant/claims/{id}/approve` (`{"override_cents": n}` optional), `/request-evidence`, `/reject`, `/reassess`,
`POST /merchant/reconcile`, `GET /merchant/stats`, `GET /policies`, `POST /policies/simulate`.

Webhook: `POST /webhooks/paypal` (signature verified via PayPal when `PAYPAL_WEBHOOK_ID` is set).

OpenAPI: `GET /openapi.json` (feed it to APIMatic).

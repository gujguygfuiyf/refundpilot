# RefundPilot API (FastAPI)

AI reads the evidence, a deterministic policy decides the amount, a merchant approves, PayPal executes the refund.
See `../docs/DESIGN.md` for the full design.

## Run locally

```bash
cd backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                             # add ,postgres for Render (Gemini SDK is included)
cp .env.example .env                                # fill PayPal + Gemini keys (OpenAI is an optional backup)
uvicorn app.main:app --reload                       # http://127.0.0.1:8000/docs
```

Without PayPal keys the app uses an in-memory `FakePayPal`; without AI keys it uses `FakeAssessor`. Both are
reported by `GET /health`, so you can build the frontend before any credentials exist.

## Seed demo orders (real sandbox captures)

```bash
python scripts/mint_orders.py --count 8               # one approve link at a time
python scripts/mint_orders.py --count 20 --batch      # all links at once; pay them in browser tabs
```

Each order prints an approve link; pay it with the sandbox Personal account, the script captures it and appends to
`seed/orders.json`. The API imports that file on startup (and on `POST /dev/seed`). Mint 40+ before submission:
each order can be claimed once, and judges will consume refundable balance.

Demo-only behaviour: an order nobody has claimed is kept "delivered 3 days ago" (`DEMO_ROLLING_DELIVERY`), so the
policy's report window doesn't expire weeks after minting. `POST /claims` and `/evidence` are limited to
`CLAIMS_PER_IP_PER_MINUTE` (5) and `AI_CALLS_PER_DAY` (200); a deployed API refuses to start with an empty or
`change-me` `MERCHANT_TOKEN`.

## Insights assistant

The Insights page's AG Studio agents ask `POST /merchant/assistant/turn` for one model turn at a time
(`app/assistant.py`). It is on when `ASSISTANT_ENABLED=true` (the default) and a Gemini key is set; `ASSISTANT_API_KEY`
and `ASSISTANT_MODEL` override `GEMINI_API_KEY` and `GEMINI_MODEL`. Limits: `ASSISTANT_TURNS_PER_DAY` (300, counted
in the database) and `ASSISTANT_TURNS_PER_IP_PER_MINUTE` (40). The tests use a scripted model; to try the real one:

```bash
python scripts/assistant_smoke.py      # runs the Studio-shaped requests in tests/fixtures, prints SMOKE: n/3
```

## Tests

```bash
pytest -q
```

Covers the policy engine, the claim state machine, idempotent approval (double click, PayPal error, timeout +
reconcile replay with the same `PayPal-Request-Id`, PENDING finalised exactly once by a webhook), the HTTP surface,
and the demo guards (rolling delivery dates, rate limits, token checks, first boot on an empty database).

## Endpoints

Buyer (anonymous): `GET /orders`, `POST /claims` (multipart: order_id, buyer_text, photos[]), `GET /claims/{id}`,
`GET /claims/{id}/photos/{n}`, `POST /claims/{id}/evidence`, `POST /claims/{id}/cancel`.

Merchant (`Authorization: Bearer $MERCHANT_TOKEN`): `GET /merchant/claims`, `GET /merchant/claims/{id}`,
`POST /merchant/claims/{id}/approve` (`{"override_cents": n}` optional), `/request-evidence`, `/reject`, `/reassess`,
`POST /merchant/reconcile`, `GET /merchant/stats`, `GET /policies`, `POST /policies/simulate`,
`GET /merchant/assistant/status`, `POST /merchant/assistant/turn` (one AG Studio model turn, run on Gemini).

Webhook: `POST /webhooks/paypal` (signature verified via PayPal when `PAYPAL_WEBHOOK_ID` is set).

OpenAPI: `GET /openapi.json` (feed it to APIMatic).

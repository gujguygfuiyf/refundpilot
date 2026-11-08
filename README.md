# RefundPilot

**Explainable, auditable refunds for PayPal merchants.**

For a small PayPal merchant, a damaged-item claim means checking photos, interpreting a refund policy, deciding an amount, and confirming that the payment was actually refunded. RefundPilot brings those steps into one reviewable workflow: AI describes the evidence, versioned rules calculate a recommendation, the merchant approves, and PayPal executes the refund. Each completed case links the evidence and decision to its PayPal refund record.

```
Buyer photo ─► AI describes the evidence ─► Policy computes the amount ─► Merchant approves ─► PayPal refunds / resolves dispute
                (never sets an amount)        (deterministic, versioned)     (one click)          (idempotent)
```

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/) (Oct–Nov 2026).

- **Live demo:** https://refundpilot-web.onrender.com (the merchant token is prefilled there; it can only move PayPal **sandbox** money).
  It runs on free hosting: after 15 idle minutes the first page load can take about a minute while the server wakes up.
- **Video (under 3 min):** https://youtu.be/ClVgMo-wgOM
- **Design doc:** [`docs/DESIGN.md`](docs/DESIGN.md)

## Why this split matters

Letting an LLM decide "how much money to give back" is a bad idea: it can be talked into it, it is not
reproducible, and nobody can explain the result afterwards. RefundPilot gives each layer one job:

| Layer | Job | Can it move money? |
|---|---|---|
| **AI (Gemini vision; optional OpenAI backup)** | Describe the photos with structured observations and uncertainty: does it match the product, what damage is visible, how good is the evidence, what can't be told | No. The model cannot directly set or execute a refund; its output schema has no amount field. Versioned policies calculate the suggestion, and a merchant approves every payment |
| **Policy engine** | Pure function from evidence + order facts to an outcome and amount, using a versioned JSON policy | No, it only suggests |
| **Merchant** | Approve (optionally override within the refundable balance), request more evidence, reject, or accept / contest an open PayPal dispute | Yes, the only path to a refund or dispute response |
| **PayPal** | Execute the refund on the original capture or resolve/update the dispute | Yes, with a fixed `PayPal-Request-Id` per attempt |

### How RefundPilot fits alongside existing return tools

Return platforms already offer image analysis and refund automation. RefundPilot focuses on making the path from evidence to a PayPal refund inspectable in one workflow: structured AI observations, versioned policy decisions, side-by-side and batch policy replay, human approval, and direct PayPal refund records. The public demo and evaluation reports let judges inspect both the implementation and its known limits.

## What uses PayPal

- **Orders v2:** `scripts/mint_orders.py` creates and captures the sandbox orders the buyer can claim against.
- **Payments v2 (primary, live-verified in PayPal Sandbox):** `GET /v2/payments/captures/{id}` checks the refundable balance before every refund;
  `POST /v2/payments/captures/{id}/refund` executes it; `GET /v2/payments/refunds/{id}` reconciles pending ones.
- **Disputes v1 (experimental extension):** `GET /v1/customer/disputes` and `GET /v1/customer/disputes/{id}` sync open buyer disputes and match them to local orders by `disputed_transactions[].seller_transaction_id` (`capture_id`), inspecting PayPal HATEOAS `links` (`accept-claim`, `provide-evidence`) and `allowed_response_options`;
  `POST /v1/customer/disputes/{id}/accept-claim` accepts a dispute and refunds the policy-suggested (or merchant-overridden) amount within the remaining balance;
  `POST /v1/customer/disputes/{id}/provide-evidence` (`multipart/form-data`) submits a deterministic evidence package built from stored order facts, AI observations, policy rule/version, prior PayPal refund IDs (`PROOF_OF_REFUND`), timeline, and buyer photos. Read sync (`GET /v1/customer/disputes`) is verified against the live PayPal Sandbox; write flows (`accept-claim`, `provide-evidence`) are covered by `FakePayPal` automated tests.
- **Idempotency & single-ledger deduplication:** each refund and dispute action carries a deterministic `PayPal-Request-Id`. A double click produces one refund.
  For direct capture refunds (`Payments v2`), a timeout puts the refund in `UNKNOWN` and the reconciler replays the same request id so PayPal returns the original refund instead of paying twice. For dispute `accept-claim` calls (`Disputes v1`), a timeout leaves the refund in `UNKNOWN` and the reconciler polls `GET /v1/customer/disputes/{id}` (never blindly replaying `accept-claim`) until `CUSTOMER.DISPUTE.RESOLVED` confirms the outcome or the merchant explicitly resets a verified unreceived attempt. Dispute `accept-claim` refunds and `PAYMENT.CAPTURE.REFUNDED` webhooks share the same refund ledger and require matching `capture_id`, currency, and amount before linking an unlinked dispute refund.
- **Webhooks:** `PAYMENT.CAPTURE.REFUNDED` and `CUSTOMER.DISPUTE.CREATED` / `UPDATED` / `RESOLVED` are signature-verified via `/v1/notifications/verify-webhook-signature`,
  deduplicated by event id, and finalize refunds or update dispute state once.
- The UI shows "Refunded" only when PayPal reports the refund as `COMPLETED` (or the dispute as `RESOLVED` in the buyer's favour).

## How we tested it

29 predefined claim scenarios plus a 12-case held-out set, each run 3 times through the real Gemini assessor and
policy engine (`cd backend && python -m eval.run_eval`). The first run found a real hole: a reused screenshot of someone
else's damage photo incorrectly received a full-refund recommendation. Policy 1.2 fixed it. After that, the main set passed 87 of 87 runs and the
held-out set, whose expected outcomes were committed before its photos existed, passed 36 of 36 runs; neither had a
suggestion above its predefined cap. Results, failures and limits: [docs/EVALUATION.md](docs/EVALUATION.md),
[docs/EVALUATION-heldout.md](docs/EVALUATION-heldout.md).

### Quick External Usability Check (Non-Merchant Testers, n = 2)

To check whether the evidence-to-decision drawer is understandable to first-time users outside the team, we ran an informal walkthrough with **2 independent non-merchant testers** inspecting recorded claims in `/merchant?tab=all`:

| Tester (Non-merchant) | Review Time per Case | Outcome & Rule Comprehension | Verbatim / Summarized Feedback |
| :--- | :--- | :--- | :--- |
| **Tester 1** | ~20s | Correctly identified the matched rule and suggested payout | Positive on workflow speed—noted that having AI verify the photo against the SKU and letting rules calculate the exact dollar amount saves time, and immediately asked about **per-claim AI inference cost**. |
| **Tester 2** | ~30s | Correctly identified the matched rule and suggested payout | Understood the decision trace within half a minute, but noted that the **UI feels utilitarian / engineering-heavy and could be more visually polished**. |

- **Takeaway on inference cost (answering Tester 1)**: Because the multimodal model is called only once per claim submission (outputting a compact structured JSON schema) and all policy replays run deterministically in Python/JSON with zero extra LLM calls, the estimated AI API cost on flash-tier multimodal models is **well under $0.01 per claim**.
- **Takeaway on UI polish & scope limits (answering Tester 2)**: This small check (`n = 2`, non-merchants) only shows that first-time readers can follow the decision trace in ~20–30 seconds; it does not prove live merchant ROI. It also highlights a clear next step: simplifying visual density in the review drawer for non-technical store operators.

## Architecture

```mermaid
flowchart LR
    subgraph Web["Next.js"]
        B[Buyer pages]
        M[Merchant queue & Disputes · AG Grid]
        S[Policy simulator]
    end
    subgraph API["FastAPI"]
        CS[Claim & Dispute service<br/>state machine · idempotent approve]
        AI[Assessor<br/>Gemini → OpenAI backup → human]
        PE[Policy engine<br/>versioned JSON]
        PP[PayPal client<br/>Orders · Payments · Disputes]
        WH[Webhook + reconciler]
    end
    DB[(Postgres / SQLite)]
    B & M & S --> CS
    CS --> AI
    CS --> PE
    CS --> PP --> PAYPAL[(PayPal Sandbox)]
    PAYPAL -. webhook .-> WH --> DB
    CS --> DB
```

## Run it locally

Requirements: Python 3.11+, Node 20+. Works with **no credentials at all**: without PayPal keys the backend seeds
8 demo orders on an in-memory fake gateway, and without AI keys it uses a canned assessor.

```bash
# backend
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                              # optional: PayPal sandbox + Gemini keys
uvicorn app.main:app --reload                     # http://127.0.0.1:8000/docs

# frontend (second terminal)
cd frontend
npm ci
cp .env.example .env.local
npm run dev                                       # http://localhost:3000
```

`GET /health` tells you which PayPal gateway and AI provider are active. The merchant pages ask for
`MERCHANT_TOKEN`: locally that's `change-me` (in `backend/.env`). A deployed API refuses to start with an empty or
default token; `render.yaml` generates a random one.

To use the real PayPal sandbox, fill `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET`, then mint captured orders:

```bash
cd backend && python scripts/mint_orders.py --count 8            # one link at a time
cd backend && python scripts/mint_orders.py --count 30 --batch   # all links at once, pay them in browser tabs
```

Each order prints an approval link; pay it with a sandbox Personal account and the script captures it and appends it
to `backend/seed/orders.json`. Restart the API to load new orders.

## Try the demo

### Quick judge walkthrough — inspect a completed case first

1. Open [All claims](https://refundpilot-web.onrender.com/merchant?tab=all) and select **Completed refund** under **Quick-inspect recorded cases**.
2. Compare the product reference and buyer photo, then inspect the matched policy rule (`R1-broken` under `standard-1.1`) and the **$49.00** refund recommendation.
3. Check the completed PayPal refund ID (`7RC15719L64464848`) and the audit timeline, including merchant approval and the API and webhook completion events.
4. Use **Simulate 3 policies ↗** to compare recommendations from the same recorded decision inputs across Goodwill, Standard, and Strict policies. No new payment is needed for this walkthrough.

**Explore further:** use the [Buyer Portal](https://refundpilot-web.onrender.com) presets (**Clearly broken item**, **Blurry photo**, **Suspected reused screenshot**, or the **Prompt-injection stress test**) to submit a new assessment, or open the [batch policy simulator](https://refundpilot-web.onrender.com/merchant/policy) (which includes an illustrative cost scenario with configurable assumptions). The hosted demo uses PayPal Sandbox. Experimental Disputes write flows are covered by automated tests with `FakePayPal`; they have not been validated end-to-end against PayPal Sandbox.

### Notes for judges

- **Each order can be claimed once.** A second claim on the same order is caught by the policy's repeat rule
  (`PRE-repeat`) and goes to manual review with no suggested amount, and a fully refunded order can't be claimed at
  all. The buyer page lists the orders you can still use first and marks the rest *Already claimed*.
- **Everything is PayPal sandbox.** The merchant token is public on the demo on purpose, so you can skip sign-in; the
  most it can do is refund sandbox money that was paid in for this demo.
- **Delivery dates roll forward.** The sandbox orders were paid in early November. To keep the 14-day report window
  open while you test, an order nobody has claimed yet always shows as delivered 3 days ago (`DEMO_ROLLING_DELIVERY`,
  demo only). Orders with a claim keep their dates, so recorded decisions stay reproducible.
- **Abuse limits.** Each submission costs one AI call, so the public demo allows 5 submissions per minute per IP and
  200 AI assessments per day (`CLAIMS_PER_IP_PER_MINUTE`, `AI_CALLS_PER_DAY`). You'll see HTTP 429 past that.
- **Free hosting.** The demo runs on Render's free plan with a free Neon database. During the judging window a
  scheduled GitHub Action ([`keep-demo-awake.yml`](.github/workflows/keep-demo-awake.yml)) keeps it awake; outside
  it, the first request after 15 idle minutes can take about a minute while the server wakes up.

## Tests

```bash
cd backend && pytest -q
```

Covers every policy rule, the claim state machine, PayPal Disputes v1 synchronization and evidence/accept flows, and idempotent approval: double click, PayPal error then retry,
timeout then reconcile with the same request id, a PENDING refund finalized exactly once by a webhook, and single-ledger deduplication between dispute refunds and capture refund webhooks. Also the
demo guards: rolling delivery dates, rate limits, token checks, first boot on an empty database.

## Tech and sponsor tools

- **PayPal** Orders v2, Payments v2 (captures, refunds), Customer Disputes v1 (list, get, accept-claim, provide-evidence), Webhooks, sandbox
- **Gemini** (`google-genai`, `response_schema`) for the vision assessment; **OpenAI** structured outputs as an
  optional automatic backup when `OPENAI_API_KEY` is set
- **AG Grid** Community: the merchant claims & disputes queue (custom cell renderers, Quartz theme)
- **Render**: `render.yaml` blueprint deploys the API and the web app in one step (free plan, Frankfurt);
  the database is a free Neon Postgres passed in as `DATABASE_URL`
- FastAPI, SQLAlchemy 2, Next.js 15, Tailwind CSS 4

## License

[MIT](LICENSE)

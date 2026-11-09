# RefundPilot · Devpost 提交文案（英文，直接复制粘贴）

> 中文说明：下面每一节对应 Devpost 表单里的一个框。`<VIDEO_URL>` 等你上传 YouTube 后替换。数据都有出处，别改数字。

---

## Project name
RefundPilot

## Elevator pitch / Tagline (≤ 200 characters)
Explainable refunds for small PayPal merchants—from damage photos to policy decisions, human approval, and a traceable payment record.

## Built with (tags)
paypal, paypal-rest-api, gemini, python, fastapi, sqlalchemy, postgresql, neon, next.js, typescript, tailwindcss, ag-grid, render, github-actions

## Links
- Live demo: https://refundpilot-web.onrender.com (merchant page: https://refundpilot-web.onrender.com/merchant, no sign-in needed)
- Code: https://github.com/gujguygfuiyf/refundpilot (MIT)
- Video: https://youtu.be/ClVgMo-wgOM

---

## About the project (main description, Markdown)

### Inspiration

For a small PayPal merchant selling physical goods, an “it arrived broken” message creates several jobs: inspect the photo, check the order and refund policy, decide a fair amount, and confirm the refund. RefundPilot brings those steps into one reviewable workflow, with the merchant retaining the final approval.

Our starting point is photo-based damaged-item claims. A completed $49 PayPal Sandbox refund demonstrates the full path from buyer evidence to a payment record.

### What it does

1. **AI describes the evidence.** Gemini compares the buyer photo with the product reference and returns structured observations: product match, visible damage, evidence quality, suspected reused imagery, and uncertainty. Its output schema has no monetary fields.
2. **Policy calculates the recommendation.** Versioned rules combine those observations with the order’s reporting window, claim history, and refundable balance. The result includes the amount, matched rule, and reason.
3. **The merchant reviews and approves.** The review drawer brings together the photos, observations, rule trace, and proposed action. The merchant can approve, adjust within the remaining balance, request evidence, or reject.
4. **PayPal executes the refund.** RefundPilot refunds the original capture and records the PayPal refund ID. A request is shown as completed only after PayPal reports completion; verified webhook events are also recorded in the audit trail.

### What makes it different

RefundPilot makes the connection between evidence, policy, and payment inspectable. A merchant can replay the same structured evidence under Standard, Strict, and Goodwill policies, see which rule changes the recommendation, and compare outcomes across a batch before adopting a policy.

For example, the simulator’s $49 cosmetic-damage scenario recommends $7.35 under Strict, $14.70 under Standard, and $24.50 under Goodwill. The evidence stays fixed while the store policy changes. These are simulated recommendations, not completed payments or measured savings.

PayPal is central to the working refund path: the decision links to the original capture, merchant approval, refund ID, and completion events.

### How we built it

The backend uses FastAPI, SQLAlchemy, and PostgreSQL; the frontend uses Next.js, TypeScript, Tailwind, and AG Grid. Render hosts the application.

Gemini provides structured visual observations. JSON policies calculate monetary recommendations, capped by the remaining refundable balance. The model can influence recommendations through its classifications, so uncertainty and human approval remain essential.

PayPal Orders v2 supplies the Sandbox orders, Payments v2 executes refunds, and verified webhooks support reconciliation. Retries reuse a stable request ID, and the ledger deduplicates completion events. The repository includes 74 automated tests covering policy decisions, claim states, and payment-related safeguards.

An experimental Disputes extension supports evidence-package preparation and dispute actions. Sandbox read sync has been checked; write paths are covered with FakePayPal tests and have not been validated end-to-end against PayPal Sandbox.

### Challenges we ran into

Our first evaluation exposed a reused-screenshot failure: a photo could show genuine damage while still being unsuitable evidence for that buyer’s claim. We updated the assessment instructions and placed the reused-photo rule ahead of payout rules.

Payment retries were another challenge. A timeout does not tell us whether a refund happened. We preserve an unknown state and reconcile it instead of immediately treating it as a failed payment that can be repeated freely.

### Accomplishments and validation

- **Completed Sandbox refunds:** the public demo exposes the recommendation, merchant approval, PayPal refund ID, and API and webhook completion events for a completed $49 test refund.
- **Repeatable policy evaluation:** after fixes, 29 scenarios run three times passed all 87 predefined checks. A separate set of 12 pre-registered scenarios passed all 36 runs. These are small scenario evaluations, not real-world fraud-detection accuracy. Some test images are AI-generated. [Main evaluation](https://github.com/gujguygfuiyf/refundpilot/blob/main/docs/EVALUATION.md) · [Held-out evaluation](https://github.com/gujguygfuiyf/refundpilot/blob/main/docs/EVALUATION-heldout.md).
- **Early usability feedback:** two non-merchant reviewers located the suggested amount and matched rule in recorded cases in approximately 20–30 seconds. Their feedback informed visual changes that have not yet been formally re-tested. This was an informal usability check, not evidence of merchant ROI or demand.

### What we learned

An explanation is useful when the merchant can inspect the evidence, understand the rule, and trace the approved action to its payment record. Model observations, policy recommendations, and payment status need clear boundaries throughout that workflow.

### What’s next

Run merchant pilots to measure review effort and decision quality, and add Shopify or WooCommerce order import. Policy replay already incurs no additional LLM API charges; assessment cost depends on model choice, input size, retries, and fallback calls.

---

## Testing instructions (for judges)

**Quick judge walkthrough — inspect a completed case first**

1. Open [All claims](https://refundpilot-web.onrender.com/merchant?tab=all) and select **Completed refund** under **Quick-inspect recorded cases** to inspect an actual completed PayPal Sandbox test refund (no waiting for new AI analysis; no sign-in needed; the sandbox merchant token is prefilled).
2. Compare the product reference and buyer photo, then inspect the matched policy rule (`R1-broken` under `standard-1.1`) and the **$49.00** refund recommendation.
3. Check the completed PayPal Sandbox refund ID (e.g., `7RC15719L64464848` / `3160410806932083S`) and the audit timeline, including merchant approval and the API and webhook completion events.
4. Use **Simulate 3 policies ↗** to compare recommendations from the same recorded decision inputs. No new payment is needed for this walkthrough.

**Explore further:**
- Use the [Buyer Portal](https://refundpilot-web.onrender.com) presets (**Clearly broken item**, **Blurry photo**, **Suspected reused screenshot**, or the **Prompt-injection stress test**) to submit a new assessment on one of the 30+ unused PayPal sandbox orders, or open the [batch policy simulator](https://refundpilot-web.onrender.com/merchant/policy).
- The hosted demo uses PayPal Sandbox; after 15 idle minutes on Render's free tier the first load can take about a minute while the service wakes up (a scheduled GitHub Action runs during the Dec 1–15 judging window to reduce the chance of encountering a cold start; manual pre-warm is available anytime via `workflow_dispatch`). Health checks do not query the database; actual database consumption must still be confirmed via the Neon console. Unclaimed demo orders automatically keep a fresh delivery date (`DEMO_ROLLING_DELIVERY=true` rolls unclaimed orders to "delivered 3 days ago") so the 14-day report window stays open during judging, while already-claimed orders preserve their original dates so recorded decisions remain reproducible. Experimental Disputes write flows are covered by automated tests with `FakePayPal`; they have not been validated end-to-end against PayPal Sandbox.

---

## Sponsor prizes to tick
- AG Grid (merchant claims queue)
- Render (render.yaml Blueprint deployment)
- Do NOT tick APIMatic (not implemented).

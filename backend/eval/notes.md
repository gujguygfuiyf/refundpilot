## History

**Run 1 · 2026-11-06 · policy standard-1.1 · original prompt** ([raw results](../backend/eval/results-run1-standard-1.1.json)):
25 of 28 scenarios passed, 75 of 84 runs, **9 overpayments**, every scenario gave the same result in all 3 runs, latency p50 8.1 s.
The overpayments came from two scenarios plus one mislabeled test photo:

- **S3 · screenshot of someone else's damage photo → paid $49.** A real gap. The model noticed the image looked like
  "part of a captured screen" but didn't raise `stock_photo_suspected`, and the payout rules didn't check that flag.
  Fixed in two places: the prompt and schema now say screenshots, photos of a screen and images with UI or watermarks
  count as reused images, and in every policy (standard-1.2, goodwill-1.2, strict-1.1) the reuse rule R6 now runs
  before any payout rule, so a suspected reused photo can never be paid automatically. Regression test:
  `test_suspected_stock_or_screenshot_never_pays_automatically`.
- **C1 and A5 · "small scratch" → paid $19.** Our test photo was wrong for the case: besides the scratch it showed a
  chip missing from the rim, which the model reasonably called broken. That photo is now its own scenario (C3, chip on
  the rim, where a full refund is acceptable), and C1/A5 use a new scratch-only photo. In A5 the "completely
  shattered" text had no effect: in all 3 runs the model wrote that the mug was intact, not shattered as claimed.

**Run 2 · 2026-11-06 · policy standard-1.2 · updated prompt**: 29 of 29 scenarios, 87 of 87 runs, 0 overpayments,
no scenario changed result between runs, latency p50 7.8 s. S3 (screenshot) now goes to review with $0; C1 and A5
(scratch only) get the 30% partial refund ($5.70); C3 (chip on the rim) gets a full refund.

**Read this with care.** Run 2 uses the same scenarios that exposed the problems, so it shows the fixes work; it is not
an unbiased estimate of accuracy on new claims. A held-out set written before looking at results is the next step.

## What this evaluation does not show

- Real-world variety: 29 scenarios, 3 products, and 7 of the photos are AI-generated.
- Staged damage (a buyer breaking the item themselves) or a buyer reusing a real photo of their own earlier damage:
  a photo can't reveal either. The policy's report window, one-claim-per-order rule and human approval are the
  controls for those, not the model.
- Severity judgement is still the model's: if it calls minor damage "broken", the policy pays for broken. The
  amount is always capped at what is left on the order, and a person approves every refund.
- One model (gemini-3.8-flash) at temperature 0; other models or versions need their own run.

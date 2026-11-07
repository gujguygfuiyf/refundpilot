"""Verify the 9 sample presets in frontend/public/samples/ against the real AI assessor and Standard policy.

No database, no PayPal and no live orders are touched. Each of the 9 sample images is run 3 times through
the configured Gemini assessor and evaluated with standard.json (days_since_delivery=3, prior_claims=0,
remaining_cents=order_cents).

Usage:
    cd backend
    python scripts/verify_samples.py
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.ai import AssessmentInput, FakeAssessor, build_assessor  # noqa: E402
from app.config import settings  # noqa: E402
from app.policy import decide, load_policy_files  # noqa: E402
from eval.run_eval import FIELDS, load_products, money, preload_references  # noqa: E402

SAMPLES = [
    {
        "preset": "LAMP-02 / broken",
        "sku": "LAMP-02",
        "photo": "frontend/public/samples/lamp-broken.jpg",
        "text": "The desk lamp arrived with the upper arm snapped at the hinge.",
    },
    {
        "preset": "LAMP-02 / blurry",
        "sku": "LAMP-02",
        "photo": "frontend/public/samples/lamp-blurry.jpg",
        "text": "The arm is broken.",
    },
    {
        "preset": "LAMP-02 / reused",
        "sku": "LAMP-02",
        "photo": "frontend/public/samples/lamp-screenshot.jpg",
        "text": "Arrived broken out of the box, please refund.",
    },
    {
        "preset": "MUG-01 / broken",
        "sku": "MUG-01",
        "photo": "frontend/public/samples/mug-broken.jpg",
        "text": "The mug arrived with the handle snapped off in the box.",
    },
    {
        "preset": "MUG-01 / blurry",
        "sku": "MUG-01",
        "photo": "frontend/public/samples/mug-blurry.jpg",
        "text": "The handle is broken.",
    },
    {
        "preset": "MUG-01 / scratch",
        "sku": "MUG-01",
        "photo": "frontend/public/samples/mug-scratch.jpg",
        "text": "There is a visible scuff mark on the side of the mug.",
    },
    {
        "preset": "HEAD-03 / broken",
        "sku": "HEAD-03",
        "photo": "frontend/public/samples/head-broken.jpg",
        "text": "The headphones arrived with the headband snapped on one side.",
    },
    {
        "preset": "HEAD-03 / blurry",
        "sku": "HEAD-03",
        "photo": "frontend/public/samples/head-dark.jpg",
        "text": "The earcup is cracked.",
    },
    {
        "preset": "HEAD-03 / missing",
        "sku": "HEAD-03",
        "photo": "frontend/public/samples/head-missing.jpg",
        "text": "One of the ear cushions was missing when I opened the box.",
    },
]


def run_sample(s: dict, products: dict, policy: dict, assessor, runs: int = 3) -> dict:
    product = products[s["sku"]]
    photo_bytes = (REPO / s["photo"]).read_bytes()
    price = product["price_cents"]
    out_runs = []
    for _ in range(runs):
        inp = AssessmentInput(
            product_name=product["name"],
            product_image_url=product["image_url"],
            buyer_photos=[photo_bytes],
            buyer_text=s["text"],
            order_cents=price,
            days_since_delivery=3,
        )
        res = assessor.assess(inp)
        a = res.assessment.model_dump()
        d = decide(
            policy,
            a,
            order_cents=price,
            remaining_cents=price,
            days_since_delivery=3,
            prior_claims=0,
        )
        out_runs.append({
            "ai": {k: a[k] for k in FIELDS},
            "rule": d.rule_id,
            "outcome": d.outcome,
            "cents": d.suggested_cents,
            "model": res.model,
        })
    return {"preset": s["preset"], "sku": s["sku"], "runs": out_runs}


def main() -> int:
    products = load_products()
    preload_references(products)
    policies = {p["version"]: p for p in load_policy_files()}
    policy = policies["standard-1.2"]
    assessor = build_assessor(settings)
    if isinstance(assessor, FakeAssessor):
        print("No AI key configured (GEMINI_API_KEY).", file=sys.stderr)
        return 2

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda s: run_sample(s, products, policy, assessor, 3), SAMPLES))

    print("| Preset | Run | product_match | damage_type | evidence_quality | stock_photo_suspected | Rule | Outcome | Amount |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        for idx, run in enumerate(r["runs"], 1):
            ai_f = run["ai"]
            print(
                f"| {r['preset']} | {idx} | {ai_f['product_match']} | {ai_f['damage_type']} | "
                f"{ai_f['evidence_quality']} | {ai_f['stock_photo_suspected']} | {run['rule']} | "
                f"{run['outcome']} | {money(run['cents'])} |"
            )

    out_path = BACKEND / "eval" / "samples-verification.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

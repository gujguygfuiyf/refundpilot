"""Flatten the recorded evaluation runs into one table for the Insights dashboard.

    python -m eval.export_insights        # writes frontend/lib/insights/eval-runs.json

Each row is one real model run from eval/results.json (main set) or eval/results-heldout.json. Nothing is
re-run: the AI findings come from the recorded files. Two columns are computed here with the same deterministic
policy engine the app uses:

- the Standard policy is replayed on the recorded findings and must reproduce the recorded outcome, rule and
  amount exactly (the script stops if it doesn't), which proves the replay is faithful;
- `goodwill_suggested` is what the Goodwill policy would suggest for the same findings.

The Strict policy is left out on purpose: its human-review guard reads `needs_human_review`, which the result
files don't record.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.policy import decide, load_policy_files  # noqa: E402

OUT = REPO / "frontend" / "lib" / "insights" / "eval-runs.json"
SETS = (("Main set", "cases.json", "results.json"), ("Held-out set", "heldout.json", "results-heldout.json"))
OUTCOME = {"FULL_REFUND": "Full refund", "PARTIAL_REFUND": "Partial refund", "REQUEST_EVIDENCE": "Request evidence",
           "REVIEW": "Manual review", "REJECT": "Reject"}


def label(value) -> str:
    return str(value).replace("_", " ").capitalize()


def build() -> list[dict]:
    policies = {p["version"].rsplit("-", 1)[0]: p for p in load_policy_files()}
    products = {p["sku"]: p for p in json.loads((BACKEND / "seed" / "products.json").read_text(encoding="utf-8"))}
    rows = []
    for set_name, cases_file, results_file in SETS:
        spec = json.loads((BACKEND / "eval" / cases_file).read_text(encoding="utf-8"))
        defaults, by_id = spec["defaults"], {c["id"]: c for c in spec["cases"]}
        for case in json.loads((BACKEND / "eval" / results_file).read_text(encoding="utf-8"))["cases"]:
            src, product = by_id[case["id"]], products[case["sku"]]
            price = product["price_cents"]
            guards = dict(order_cents=price, remaining_cents=src.get("remaining_cents", price),
                          days_since_delivery=src.get("days_since_delivery", defaults["days_since_delivery"]),
                          prior_claims=src.get("prior_claims", defaults["prior_claims"]))
            for i, run in enumerate(case["runs"], start=1):
                standard = decide(policies["standard"], run["ai"], **guards)
                if (standard.outcome, standard.suggested_cents, standard.rule_id) != (run["outcome"], run["cents"], run["rule"]):
                    raise SystemExit(f"{case['id']} run {i}: replay gave {standard}, the recorded run says {run}")
                goodwill = decide(policies["goodwill"], run["ai"], **guards)
                ai = run["ai"]
                rows.append({
                    "run": f"{case['id']}-{i}", "case": case["id"], "set": set_name, "group": case["group"],
                    "scenario": case["scenario"], "sku": case["sku"], "product": product["name"],
                    "order_amount": price / 100, "outcome": OUTCOME[run["outcome"]], "rule": run["rule"],
                    "suggested": run["cents"] / 100, "cap": case["max_cents"] / 100,
                    "goodwill_outcome": OUTCOME[goodwill.outcome], "goodwill_suggested": goodwill.suggested_cents / 100,
                    "passed": 1 if run["ok"] else 0, "overpaid": 1 if run["overpay"] else 0,
                    "result": "Pass" if run["ok"] else "Fail", "latency_s": round(run["latency_ms"] / 1000, 2),
                    "product_match": label(ai["product_match"]), "damage_type": label(ai["damage_type"]),
                    "severity": label(ai["severity"]), "evidence_quality": label(ai["evidence_quality"]),
                    "stock_photo": "Suspected" if ai["stock_photo_suspected"] else "No", "model": run["model"],
                })
    return rows


def main() -> int:
    rows = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # One run per line keeps the diff readable when the evaluation is re-run.
    OUT.write_text("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n", encoding="utf-8")
    paid, goodwill = sum(r["suggested"] for r in rows), sum(r["goodwill_suggested"] for r in rows)
    print(f"{len(rows)} runs -> {OUT.relative_to(REPO)}; standard ${paid:,.2f}, goodwill ${goodwill:,.2f}, "
          f"overpaid {sum(r['overpaid'] for r in rows)}, passed {sum(r['passed'] for r in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

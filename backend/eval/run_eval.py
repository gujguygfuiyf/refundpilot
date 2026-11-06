"""Run the predefined claim scenarios through the real AI assessor and the policy engine.

No database, no PayPal and no live orders are touched: each case builds the same AssessmentInput the API builds,
calls the assessor, then runs the deterministic policy, exactly as ClaimService does.

    cd backend
    python -m eval.run_eval                       # 3 runs per case with the configured provider (GEMINI_API_KEY)
    python -m eval.run_eval --runs 1 --only D1,A1
    python -m eval.run_eval --fake                # offline smoke test of the harness itself
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from app import ai
from app.ai import AssessmentInput, FakeAssessor, build_assessor, prepare_jpeg
from app.config import settings
from app.policy import decide, load_policy_files

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FIELDS = ("product_match", "damage_type", "severity", "evidence_quality", "stock_photo_suspected")


def load_products() -> dict[str, dict]:
    return {p["sku"]: p for p in json.loads((BACKEND / "seed" / "products.json").read_text(encoding="utf-8"))}


def preload_references(products: dict[str, dict]) -> None:
    """Use the listing photos from the repo instead of downloading them, so results don't depend on the network."""
    for p in products.values():
        local = REPO / "assets" / "products" / p["image_url"].rsplit("/", 1)[-1]
        if local.exists():
            ai._REFERENCE_CACHE[p["image_url"]] = prepare_jpeg(local.read_bytes())[0]


def run_case(case: dict, defaults: dict, products: dict, policies: dict, assessor, runs: int) -> dict:
    product = products[case["sku"]]
    missing = [p for p in case["photos"] if not (REPO / p).exists()]
    out = {k: case[k] for k in ("id", "group", "scenario", "sku", "expect", "max_cents")}
    if missing:
        return {**out, "status": "skipped", "missing": missing, "runs": []}
    photos = [(REPO / p).read_bytes() for p in case["photos"]]
    policy = policies[case.get("policy", defaults["policy"])]
    price = product["price_cents"]
    results = []
    for _ in range(runs):
        inp = AssessmentInput(product_name=product["name"], product_image_url=product["image_url"], buyer_photos=photos,
                              buyer_text=case["text"], order_cents=price,
                              days_since_delivery=case.get("days_since_delivery", defaults["days_since_delivery"]))
        t0 = time.time()
        res = assessor.assess(inp)
        elapsed = int((time.time() - t0) * 1000)
        a = res.assessment.model_dump()
        d = decide(policy, a, order_cents=price, remaining_cents=case.get("remaining_cents", price),
                   days_since_delivery=inp.days_since_delivery,
                   prior_claims=case.get("prior_claims", defaults["prior_claims"]))
        results.append({
            "outcome": d.outcome, "cents": d.suggested_cents, "rule": d.rule_id,
            "ok": d.outcome in case["expect"] and d.suggested_cents <= case["max_cents"],
            "overpay": d.suggested_cents > case["max_cents"],
            "provider": res.provider, "model": res.model, "latency_ms": res.latency_ms or elapsed,
            "ai": {k: a[k] for k in FIELDS}, "observations": a["observations"], "limitations": a["limitations"],
        })
    keys = {(r["outcome"], r["cents"]) for r in results}
    return {**out, "status": "pass" if all(r["ok"] for r in results) else "fail",
            "consistent": len(keys) == 1, "runs": results}


def summarize(cases: list[dict]) -> dict:
    ran = [c for c in cases if c["status"] != "skipped"]
    runs = [r for c in ran for r in c["runs"]]
    lat = sorted(r["latency_ms"] for r in runs if r["provider"] not in ("fake", "none"))
    models = Counter(f'{r["provider"]}/{r["model"]}' for r in runs)
    return {
        "cases": len(ran), "skipped": [c["id"] for c in cases if c["status"] == "skipped"],
        "runs": len(runs),
        "cases_passed": sum(c["status"] == "pass" for c in ran),
        "runs_passed": sum(r["ok"] for r in runs),
        "overpayments": sum(r["overpay"] for r in runs),
        "inconsistent_cases": [c["id"] for c in ran if not c["consistent"]],
        "fallbacks": sum(r["provider"] == "none" for r in runs),
        "latency_p50_ms": int(statistics.median(lat)) if lat else None,
        "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
        "models": dict(models),
    }


def money(c: int) -> str:
    return f"${c / 100:.2f}"


def result_cell(c: dict) -> str:
    if c["status"] == "skipped":
        return "skipped (photo missing)"
    counts = Counter(f'{r["outcome"].replace("_", " ").lower()} {money(r["cents"])}' for r in c["runs"])
    return "; ".join(f"{k} ×{n}" if len(c["runs"]) > 1 else k for k, n in counts.items())


def ai_cell(c: dict) -> str:
    if not c["runs"]:
        return ""
    a = Counter(tuple(r["ai"][k] for k in FIELDS[:4]) + (r["ai"]["stock_photo_suspected"],) for r in c["runs"]).most_common(1)[0][0]
    return f"{a[0]}, {a[1]}, {a[2]}, evidence {a[3]}" + (", stock photo suspected" if a[4] else "")


def markdown(cases: list[dict], s: dict, args) -> str:
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# RefundPilot evaluation",
        "",
        f"Generated by `python -m eval.run_eval --runs {args.runs}` on {when}. "
        f"Model: {', '.join(s['models']) or 'n/a'}. Policy: {args.policy_label}.",
        "",
        "Each scenario runs through the same AI assessor and deterministic policy engine the live API uses "
        "(no database, no PayPal, no live orders). A run passes when the outcome is one of the expected ones and the "
        "suggested amount does not exceed the case's maximum. **Overpayment** (suggesting more than the maximum) is the "
        "failure that matters most; asking for more evidence on a genuine claim is the cheaper mistake.",
        "",
        "Photos: `demo-photos/` and `assets/products/` are real; `eval/photos/var-*` are derived from them by "
        "`eval/make_variants.py`; `eval/photos/syn-*` are AI-generated test images (stated so you can weigh them).",
        "",
        "## Summary",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Scenarios passed (all runs) | {s['cases_passed']} / {s['cases']} |",
        f"| Runs passed | {s['runs_passed']} / {s['runs']} |",
        f"| Overpayments | {s['overpayments']} of {s['runs']} runs |",
        f"| Scenarios with different results across runs | {len(s['inconsistent_cases'])}"
        + (f" ({', '.join(s['inconsistent_cases'])})" if s["inconsistent_cases"] else "") + " |",
        f"| AI unavailable, routed to a human | {s['fallbacks']} runs |",
        f"| Assessment latency p50 / p95 | "
        + (f"{s['latency_p50_ms'] / 1000:.1f} s / {s['latency_p95_ms'] / 1000:.1f} s" if s["latency_p50_ms"] else "n/a") + " |",
    ]
    if s["skipped"]:
        lines.append(f"| Skipped (photo missing) | {', '.join(s['skipped'])} |")
    group = None
    for c in cases:
        if c["group"] != group:
            group = c["group"]
            lines += ["", f"## {group}", "", "| ID | Scenario | Expected | Result | What the AI reported | |", "|---|---|---|---|---|---|"]
        exp = " or ".join(e.replace("_", " ").lower() for e in c["expect"]) + f", at most {money(c['max_cents'])}"
        mark = {"pass": "✅", "fail": "❌", "skipped": "–"}[c["status"]]
        if any(r["overpay"] for r in c["runs"]):
            mark = "❌ overpay"
        lines.append(f"| {c['id']} | {c['scenario']} | {exp} | {result_cell(c)} | {ai_cell(c)} | {mark} |")
    fails = [c for c in cases if c["status"] == "fail"]
    lines += ["", "## Failures in detail", ""]
    if not fails:
        lines.append("None in this run.")
    for c in fails:
        bad = next(r for r in c["runs"] if not r["ok"])
        lines += [f"**{c['id']} · {c['scenario']}**: got {bad['outcome'].replace('_', ' ').lower()} "
                  f"{money(bad['cents'])} via rule {bad['rule']}.",
                  f"AI observations: {'; '.join(bad['observations']) or 'none'}. "
                  f"Limitations: {'; '.join(bad['limitations']) or 'none'}.", ""]
    notes = BACKEND / "eval" / "notes.md"
    if notes.exists():
        lines += ["", notes.read_text(encoding="utf-8").strip()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--fake", action="store_true", help="use the offline FakeAssessor (tests the harness, not the AI)")
    ap.add_argument("--cases", default=str(BACKEND / "eval" / "cases.json"))
    ap.add_argument("--json", default=str(BACKEND / "eval" / "results.json"))
    ap.add_argument("--md", default=str(REPO / "docs" / "EVALUATION.md"))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)

    spec = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    args.policy_label = spec["defaults"]["policy"]
    cases = [c for c in spec["cases"] if not args.only or c["id"] in args.only.split(",")]
    products = load_products()
    preload_references(products)
    policies = {p["version"]: p for p in load_policy_files()}
    assessor = FakeAssessor() if args.fake else build_assessor(settings)
    if isinstance(assessor, FakeAssessor) and not args.fake:
        print("No AI key configured (GEMINI_API_KEY). Use --fake to test the harness offline.", file=sys.stderr)
        return 2

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(lambda c: run_case(c, spec["defaults"], products, policies, assessor, args.runs), cases))
    s = summarize(results)
    Path(args.json).write_text(json.dumps({"summary": s, "cases": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(args.md).write_text(markdown(results, s, args), encoding="utf-8")
    for c in results:
        print(f"{c['id']:3} {c['status']:7} {result_cell(c)}")
    print(json.dumps(s, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

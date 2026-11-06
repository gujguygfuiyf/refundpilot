"""The evaluation harness itself: offline, with the FakeAssessor (always 'broken, sufficient')."""
from __future__ import annotations

import json

from eval import run_eval


def test_harness_scores_and_flags_overpayment(tmp_path):
    spec = json.loads((run_eval.BACKEND / "eval" / "cases.json").read_text(encoding="utf-8"))
    ids = {"D1", "W1", "P3", "A1"}
    spec["cases"] = [c for c in spec["cases"] if c["id"] in ids]
    spec["cases"].append({"id": "X1", "group": "Missing", "scenario": "photo not there", "sku": "MUG-01",
                          "photos": ["backend/eval/photos/does-not-exist.jpg"], "text": "x",
                          "expect": ["REVIEW"], "max_cents": 0})
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps(spec), encoding="utf-8")
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    assert run_eval.main(["--fake", "--runs", "2", "--cases", str(cases_file),
                          "--json", str(out_json), "--md", str(out_md)]) == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in data["cases"]}
    # The fake AI always reports "likely, broken, sufficient": fine for D1/A1 (capped at the order value),
    # an overpayment for W1 (wrong product), and the fully-refunded guard still wins for P3.
    assert by_id["D1"]["status"] == "pass" and by_id["D1"]["runs"][0]["cents"] == 4900
    assert by_id["A1"]["status"] == "pass" and by_id["A1"]["runs"][0]["cents"] == 4900
    assert by_id["W1"]["status"] == "fail" and all(r["overpay"] for r in by_id["W1"]["runs"])
    assert by_id["P3"]["status"] == "pass" and by_id["P3"]["runs"][0]["outcome"] == "REJECT"
    assert by_id["X1"]["status"] == "skipped"
    s = data["summary"]
    assert s["cases"] == 4 and s["runs"] == 8 and s["overpayments"] == 2 and s["skipped"] == ["X1"]
    md = out_md.read_text(encoding="utf-8")
    assert "❌ overpay" in md and "W1" in md and "Failures in detail" in md

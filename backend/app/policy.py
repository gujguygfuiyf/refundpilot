"""Deterministic policy engine. Pure functions, no IO. The AI never sets amounts; this does."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

POLICIES_DIR = Path(__file__).resolve().parent.parent / "policies"

OUTCOMES = ("FULL_REFUND", "PARTIAL_REFUND", "REQUEST_EVIDENCE", "REVIEW", "REJECT")


@dataclass
class Decision:
    outcome: str
    suggested_cents: int
    rule_id: str
    requires_approval: bool
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _matches(cond: dict, a: dict) -> bool:
    for k, v in cond.items():
        if isinstance(v, list):
            if a.get(k) not in v:
                return False
        elif a.get(k) != v:
            return False
    return True


def decide(policy: dict, assessment: dict, *, order_cents: int, remaining_cents: int,
           days_since_delivery: int, prior_claims: int) -> Decision:
    if remaining_cents <= 0:
        return Decision("REJECT", 0, "PRE-nothing-left", True, "Order is already fully refunded")
    if days_since_delivery > int(policy["report_window_days"]):
        return Decision(policy.get("out_of_window_outcome", "REVIEW"), 0, "PRE-window", True,
                        f"Reported {days_since_delivery} days after delivery, outside the {policy['report_window_days']}-day window")
    if prior_claims > 0:
        return Decision(policy.get("repeat_claim_outcome", "REVIEW"), 0, "PRE-repeat", True,
                        "Order already has a prior claim")
    if assessment.get("needs_human_review") and policy.get("strict_human_review"):
        return Decision("REVIEW", 0, "PRE-ai-flag", True, "AI flagged the case for human review")

    auto_below = int(policy.get("auto_approve_below_cents", 0))
    for r in policy["rules"]:
        if not _matches(r.get("when", {}), assessment):
            continue
        outcome = r["outcome"]
        if outcome == "FULL_REFUND":
            cents = remaining_cents
        elif outcome == "PARTIAL_REFUND":
            pct = min(int(r.get("percent", 0)), int(policy.get("max_partial_percent", 100)))
            cents = min(order_cents * pct // 100, remaining_cents)
        else:
            cents = 0
        needs = outcome in ("REVIEW", "REJECT", "REQUEST_EVIDENCE") or cents > auto_below
        return Decision(outcome, cents, r["id"], needs, r.get("reason") or f"Rule {r['id']} matched")
    return Decision(policy.get("default_outcome", "REVIEW"), 0, "DEFAULT", True, "No rule matched")


def status_for(decision: Decision) -> str:
    """Claim status after a decision."""
    if decision.outcome == "REQUEST_EVIDENCE":
        return "EVIDENCE_REQUESTED"
    if decision.outcome in ("FULL_REFUND", "PARTIAL_REFUND") and not decision.requires_approval:
        return "AUTO_ELIGIBLE"
    return "REVIEW_REQUIRED"


def validate_policy(p: dict) -> None:
    for key in ("version", "name", "report_window_days", "rules"):
        if key not in p:
            raise ValueError(f"policy missing {key}")
    for r in p["rules"]:
        if r.get("outcome") not in OUTCOMES:
            raise ValueError(f"rule {r.get('id')} has invalid outcome {r.get('outcome')}")
        if r["outcome"] == "PARTIAL_REFUND" and "percent" not in r:
            raise ValueError(f"rule {r.get('id')} needs percent")


def policy_sha(p: dict) -> str:
    return hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_policy_files() -> list[dict]:
    out = []
    for f in sorted(POLICIES_DIR.glob("*.json")):
        p = json.loads(f.read_text(encoding="utf-8"))
        validate_policy(p)
        out.append(p)
    return out

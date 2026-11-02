import httpx
import pytest

from app.claims import Conflict
from app.models import AuditLog
from app.paypal import FakePayPal, PayPalError
from tests.conftest import jpeg_bytes


def submit(svc, order_id=1, text="handle broke on arrival"):
    c = svc.create_claim(order_id, text, [jpeg_bytes()])
    svc.run_assessment(c.id)
    return svc.get_claim(c.id)


def test_pipeline_broken_goes_to_review(svc, db):
    c = submit(svc)
    assert c.status == "REVIEW_REQUIRED"
    assert c.latest_assessment.output["damage_type"] == "broken"
    assert c.latest_decision.outcome == "FULL_REFUND" and c.latest_decision.suggested_cents == 1900
    assert c.latest_decision.policy_version == "standard-1.1"
    events = [a.event for a in db.query(AuditLog).filter_by(claim_id=c.id).order_by(AuditLog.id)]
    assert events[:4] == ["claim_submitted", "analysis_started", "assessed", "decided"]


def test_mismatch_requests_evidence_and_resubmit(svc):
    c = submit(svc, text="wrong")
    assert c.status == "EVIDENCE_REQUESTED"
    with pytest.raises(Conflict):
        svc.approve(c.id)                          # nothing to approve, no money moves
    svc.add_evidence(c.id, [jpeg_bytes((10, 200, 10))], "here is a better photo")
    svc.run_assessment(c.id)
    assert svc.get_claim(c.id).status == "EVIDENCE_REQUESTED"   # fake still says mismatch; two photos stored
    assert len(svc.get_claim(c.id).photos) == 2


def test_one_open_claim_per_order(svc):
    submit(svc)
    with pytest.raises(Conflict):
        svc.create_claim(1, "again", [jpeg_bytes()])


def test_approve_full_refund_completes(svc, paypal, db):
    c = submit(svc)
    r = svc.approve(c.id, by="merchant")
    assert r.status == "COMPLETED" and r.paypal_refund_id == "R00001"
    assert r.paypal_request_id == f"rp-{c.id}-1"
    c = svc.get_claim(c.id)
    assert c.status == "COMPLETED" and c.order.refunded_cents == 1900 and c.order.remaining_cents == 0
    assert [k[0] for k in paypal.calls] == ["get_capture", "refund"]


def test_double_approve_is_rejected_and_only_one_refund(svc, paypal):
    c = submit(svc)
    svc.approve(c.id)
    with pytest.raises(Conflict):
        svc.approve(c.id)
    assert len(paypal.refunds) == 1


def test_override_amount_within_remaining(svc, paypal):
    c = submit(svc, order_id=2)                    # $49 order
    r = svc.approve(c.id, override_cents=1200)
    assert r.amount_cents == 1200 and r.status == "COMPLETED"
    assert svc.get_claim(c.id).order.remaining_cents == 3700
    c2 = submit(svc, order_id=2)                   # second claim on same order: prior_claims=1 -> REVIEW, 0 cents
    assert c2.latest_decision.rule_id == "PRE-repeat"
    with pytest.raises(Conflict):
        svc.approve(c2.id, override_cents=5000)    # exceeds remaining


def test_paypal_error_marks_failed_and_returns_to_review(svc, paypal):
    c = submit(svc)
    paypal.fail_next = PayPalError(422, "REFUND_AMOUNT_EXCEEDED")
    r = svc.approve(c.id)
    assert r.status == "FAILED" and "422" in r.error
    c = svc.get_claim(c.id)
    assert c.status == "REVIEW_REQUIRED" and c.order.refunded_cents == 0
    r2 = svc.approve(c.id)                         # retry gets a new request id
    assert r2.status == "COMPLETED" and r2.paypal_request_id.endswith("-2")


def test_timeout_then_reconcile_replays_same_request_id(svc, paypal):
    c = submit(svc)
    paypal.fail_next = httpx.ReadTimeout("slow")
    r = svc.approve(c.id)
    assert r.status == "UNKNOWN" and svc.get_claim(c.id).status == "PROCESSING"
    assert svc.reconcile() == 1
    r = svc.get_claim(c.id).refunds[0]
    assert r.status == "COMPLETED"
    replay = [k for k in paypal.calls if k[0] == "refund"]
    assert len(replay) == 2 and replay[0][3] == replay[1][3]     # same PayPal-Request-Id
    assert len(paypal.refunds) == 1


def test_pending_refund_finalized_by_webhook_once(db, assessor):
    pp = FakePayPal({"CAP1": 1900, "CAP2": 4900}, refund_status="PENDING")
    from app.claims import ClaimService
    svc = ClaimService(db, pp, assessor, "standard-1.1")
    c = submit(svc)
    r = svc.approve(c.id)
    assert r.status == "PENDING" and svc.get_claim(c.id).status == "PROCESSING"
    assert svc.get_claim(c.id).order.refunded_cents == 0
    resource = pp.complete(r.paypal_refund_id)
    assert svc.apply_webhook_refund(resource) is True
    assert svc.apply_webhook_refund(resource) is True              # duplicate delivery
    c = svc.get_claim(c.id)
    assert c.status == "COMPLETED" and c.order.refunded_cents == 1900   # credited exactly once


def test_reject_and_cancel_paths(svc):
    c = submit(svc)
    svc.reject(c.id, "photo shows normal wear")
    assert svc.get_claim(c.id).status == "REJECTED"
    c2 = svc.create_claim(2, "x", [jpeg_bytes()])
    svc.cancel(c2.id)
    assert svc.get_claim(c2.id).status == "CANCELLED"


def test_redecide_under_other_policy(svc):
    c = submit(svc, text="scratch")                # cosmetic
    assert c.latest_decision.suggested_cents == 570   # 30% of 1900
    svc.redecide(c.id, "goodwill-1.1")
    c = svc.get_claim(c.id)
    assert c.policy_version == "goodwill-1.1" and c.latest_decision.suggested_cents == 950
    assert len(c.assessments) == 1                 # no new AI call

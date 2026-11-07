"""Tests for PayPal Disputes API v1 integration: matching, webhook dedupe, idempotent accept-claim,
ledger deduplication with PAYMENT.CAPTURE.REFUNDED, balance caps, deterministic evidence, and DISPUTES_ENABLED."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.ai import FakeAssessor
from app.claims import ClaimService, Conflict
from app.config import settings
from app.db import get_db
from app.main import BackgroundWork, app
from app.models import AuditLog, Dispute
from app.paypal import FakePayPal
from tests.conftest import jpeg_bytes
from tests.test_claims import submit

MERCHANT = {"Authorization": "Bearer test-merchant"}


def test_dispute_matches_order_and_claim_by_seller_transaction_id(svc, paypal, db):
    claim = submit(svc, order_id=1, text="scratch")     # cosmetic on $19.00 mug -> 30% = 570 cents
    assert claim.latest_decision.suggested_cents == 570

    raw = paypal.open_dispute("CAP1", dispute_id="PP-D-1001", reason="MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED")
    assert svc.sync_disputes() == 1

    dispute = svc.get_dispute("PP-D-1001")
    assert dispute.order_id == 1
    assert dispute.claim_id == claim.id
    assert dispute.dispute_amount_cents == 1900
    assert dispute.status == "WAITING_FOR_SELLER_RESPONSE"
    assert dispute.stage == "INQUIRY"
    assert svc.suggested_dispute_cents(dispute) == 570


def test_dispute_webhook_created_updated_resolved_and_deduped(db, paypal):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[deps.get_paypal] = lambda: paypal
    app.dependency_overrides[deps.get_assessor] = lambda: FakeAssessor()
    try:
        with TestClient(app) as client:
            raw = paypal.open_dispute("CAP1", dispute_id="PP-D-2001")
            ev = {
                "id": "WH-DISP-1",
                "event_type": "CUSTOMER.DISPUTE.CREATED",
                "resource": raw,
            }
            r1 = client.post("/webhooks/paypal", json=ev)
            assert r1.status_code == 200 and r1.json() == {"ok": True, "handled": True}

            # Duplicate delivery with same event id is ignored
            r2 = client.post("/webhooks/paypal", json=ev)
            assert r2.status_code == 200 and r2.json() == {"ok": True, "duplicate": True}

            created_logs = [
                a for a in db.query(AuditLog).all()
                if a.event == "dispute_created" and (a.detail or {}).get("paypal_dispute_id") == "PP-D-2001"
            ]
            assert len(created_logs) == 1

            # UPDATED event advances stage to CHARGEBACK
            raw_updated = {**raw, "dispute_life_cycle_stage": "CHARGEBACK", "status": "UNDER_REVIEW"}
            r3 = client.post("/webhooks/paypal", json={
                "id": "WH-DISP-2",
                "event_type": "CUSTOMER.DISPUTE.UPDATED",
                "resource": raw_updated,
            })
            assert r3.status_code == 200 and r3.json()["handled"] is True
            disp = db.query(Dispute).filter_by(paypal_dispute_id="PP-D-2001").one()
            assert disp.stage == "CHARGEBACK" and disp.status == "UNDER_REVIEW"

            # RESOLVED event marks resolved_at
            raw_resolved = {**raw_updated, "status": "RESOLVED"}
            r4 = client.post("/webhooks/paypal", json={
                "id": "WH-DISP-3",
                "event_type": "CUSTOMER.DISPUTE.RESOLVED",
                "resource": raw_resolved,
            })
            assert r4.status_code == 200 and r4.json()["handled"] is True
            db.refresh(disp)
            assert disp.status == "RESOLVED" and disp.resolved_at is not None
    finally:
        app.dependency_overrides.clear()


def test_accept_claim_idempotent_double_click_refunds_once(svc, paypal, db):
    claim = submit(svc, order_id=1)     # broken -> suggested 1900 cents
    paypal.open_dispute("CAP1", dispute_id="PP-D-3001")
    svc.sync_disputes()

    r = svc.accept_dispute("PP-D-3001", by="merchant")
    assert r.status == "COMPLETED"
    assert r.amount_cents == 1900
    assert r.paypal_request_id == "rp-dispute-accept-PP-D-3001-1"

    # Second click / concurrent attempt is rejected
    with pytest.raises(Conflict):
        svc.accept_dispute("PP-D-3001", by="merchant")

    accept_calls = [c for c in paypal.calls if c[0] == "accept_claim"]
    assert len(accept_calls) == 1
    assert svc.get_claim(claim.id).order.refunded_cents == 1900


def test_accept_claim_timeout_is_never_replayed_and_explicit_reset_frees_the_dispute(svc, paypal):
    submit(svc, order_id=1)
    paypal.open_dispute("CAP1", dispute_id="PP-D-3002")
    svc.sync_disputes()

    paypal.fail_next = httpx.ReadTimeout("network timeout")
    r = svc.accept_dispute("PP-D-3002")
    assert r.status == "UNKNOWN"
    assert svc.get_dispute("PP-D-3002").status == "PROCESSING"

    # reconcile asks PayPal about the dispute instead of re-sending accept-claim,
    # and does NOT prematurely fail the refund while PayPal may still be processing it.
    assert svc.reconcile() == 0
    disp = svc.get_dispute("PP-D-3002")
    assert r.status == "UNKNOWN"
    assert disp.status == "PROCESSING"
    assert disp.order.refunded_cents == 0
    assert len([c for c in paypal.calls if c[0] == "accept_claim"]) == 1

    # Once the merchant confirms in PayPal that the request never landed, explicit reset frees the dispute.
    svc.reset_dispute_accept("PP-D-3002", reason="confirmed not received in PayPal")
    assert r.status == "FAILED"
    assert disp.status == "WAITING_FOR_SELLER_RESPONSE"

    # the merchant can decide again; the retry carries a new attempt id
    r2 = svc.accept_dispute("PP-D-3002")
    assert r2.paypal_request_id == "rp-dispute-accept-PP-D-3002-2"
    assert r2.status == "COMPLETED"
    assert disp.order.refunded_cents == 1900


def test_accept_claim_timeout_that_landed_is_settled_from_the_paypal_dispute(svc, paypal):
    submit(svc, order_id=1)
    paypal.open_dispute("CAP1", dispute_id="PP-D-3003")
    svc.sync_disputes()

    real_accept = paypal.accept_claim

    def landed_then_timed_out(*args, **kwargs):
        real_accept(*args, **kwargs)
        raise httpx.ReadTimeout("response lost")

    paypal.accept_claim = landed_then_timed_out
    r = svc.accept_dispute("PP-D-3003")
    assert r.status == "UNKNOWN"
    assert svc.get_dispute("PP-D-3003").order.refunded_cents == 0     # nothing credited on a guess

    assert svc.reconcile() == 1
    disp = svc.get_dispute("PP-D-3003")
    assert r.status == "COMPLETED"
    assert disp.status == "RESOLVED"
    assert disp.order.refunded_cents == 1900


def test_accept_claim_is_not_credited_until_paypal_confirms(svc, paypal, db):
    claim = submit(svc, order_id=2)     # $49.00 order (CAP2)
    paypal.open_dispute("CAP2", dispute_id="PP-D-4002")
    svc.sync_disputes()
    paypal.dispute_resolves_immediately = False

    r = svc.accept_dispute("PP-D-4002")
    disp = svc.get_dispute("PP-D-4002")
    assert r.status == "PENDING" and r.paypal_refund_id is None
    assert disp.status == "PROCESSING"
    assert disp.order.refunded_cents == 0
    assert svc.get_claim(claim.id).status == "PROCESSING"

    assert svc.reconcile() == 0         # PayPal still reviewing: nothing changes
    assert r.status == "PENDING"

    pp_refund = paypal.resolve_deferred_accept("PP-D-4002")
    assert svc.apply_webhook_dispute(paypal.get_dispute("PP-D-4002"), "CUSTOMER.DISPUTE.RESOLVED") is True
    assert r.status == "COMPLETED"
    assert disp.status == "RESOLVED"
    assert disp.order.refunded_cents == 4900

    # PAYMENT.CAPTURE.REFUNDED for the same money only attaches the refund id
    assert svc.apply_webhook_refund(dict(pp_refund)) is True
    db.refresh(r)
    assert r.paypal_refund_id == pp_refund["id"]
    assert disp.order.refunded_cents == 4900


def test_dispute_refund_and_capture_refunded_webhook_never_double_credit(svc, paypal, db):
    """accept-claim returns HATEOAS links only; the refund id arrives later with PAYMENT.CAPTURE.REFUNDED.
    The order ledger must be credited once, however many times the webhook is delivered."""
    submit(svc, order_id=2)             # $49.00 order (CAP2)
    paypal.open_dispute("CAP2", dispute_id="PP-D-4001")
    svc.sync_disputes()

    r = svc.accept_dispute("PP-D-4001", override_cents=2000)
    assert r.status == "COMPLETED" and r.amount_cents == 2000
    assert r.paypal_refund_id is None
    assert svc.get_dispute("PP-D-4001").order.refunded_cents == 2000

    pp_refund = next(v for v in paypal.refunds.values() if v["invoice_id"] == "dispute-PP-D-4001")
    webhook_resource = {
        "id": pp_refund["id"],
        "status": "COMPLETED",
        "amount": {"value": "20.00", "currency_code": "USD"},
        "links": [{"rel": "up", "href": "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP2"}],
    }
    assert svc.apply_webhook_refund(webhook_resource) is True
    db.refresh(r)
    assert r.paypal_refund_id == pp_refund["id"]
    assert svc.get_dispute("PP-D-4001").order.refunded_cents == 2000

    # redelivery of the same webhook
    assert svc.apply_webhook_refund(webhook_resource) is True
    assert svc.get_dispute("PP-D-4001").order.refunded_cents == 2000


def test_accept_claim_rejects_amount_exceeding_remaining_balance(svc, paypal):
    # First refund $12.00 on order #2 ($49.00) via normal claim approval -> $37.00 remaining
    c1 = submit(svc, order_id=2)
    svc.approve(c1.id, override_cents=1200)
    assert svc.get_claim(c1.id).order.remaining_cents == 3700

    # Buyer then opens a PayPal dispute for the full $49.00
    paypal.open_dispute("CAP2", dispute_id="PP-D-5001", cents=4900)
    svc.sync_disputes()

    # Attempting to accept for $40.00 (> $37.00 remaining) is rejected before calling PayPal
    with pytest.raises(Conflict, match="out of range"):
        svc.accept_dispute("PP-D-5001", override_cents=4000)

    assert not [c for c in paypal.calls if c[0] == "accept_claim"]
    assert svc.get_claim(c1.id).order.refunded_cents == 1200


def test_provide_evidence_includes_deterministic_facts_and_prior_refund_id(svc, paypal):
    # Refund a cosmetic claim ($5.70) first, then buyer opens a dispute anyway
    c = submit(svc, order_id=1, text="scratch")
    ref = svc.approve(c.id)
    assert ref.status == "COMPLETED" and ref.paypal_refund_id

    paypal.open_dispute("CAP1", dispute_id="PP-D-6001")
    svc.sync_disputes()
    disp = svc.get_dispute("PP-D-6001")

    pkg = svc.build_dispute_evidence(disp)
    assert pkg["evidence_type"] == "PROOF_OF_REFUND"
    assert ref.paypal_refund_id in pkg["refund_ids"]
    assert ref.paypal_refund_id in pkg["notes"]
    assert "USD 5.70" in pkg["notes"]
    assert "R2-cosmetic" in pkg["notes"]
    assert "standard-1.2" in pkg["notes"]
    assert "small scratch on the rim" in pkg["notes"]

    # Merchant edits/confirms and sends evidence to PayPal
    updated = svc.submit_dispute_evidence("PP-D-6001", notes=pkg["notes"] + "\nMerchant verified.")
    assert updated.status == "UNDER_REVIEW"
    assert "Merchant verified." in (updated.submitted_evidence_notes or "")
    ev_calls = [c for c in paypal.calls if c[0] == "provide_evidence"]
    assert len(ev_calls) == 1 and ev_calls[0][2] == "PROOF_OF_REFUND"
    assert ev_calls[0][3] == "rp-dispute-evid-PP-D-6001-1"


def test_disputes_disabled_flag_leaves_everything_untouched(svc, paypal, db, monkeypatch):
    monkeypatch.setattr(settings, "disputes_enabled", False)
    paypal.open_dispute("CAP1", dispute_id="PP-D-7001")

    assert svc.sync_disputes() == 0
    assert svc.apply_webhook_dispute(paypal.get_dispute("PP-D-7001"), "CUSTOMER.DISPUTE.CREATED") is False
    assert db.query(Dispute).count() == 0

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[deps.get_paypal] = lambda: paypal
    app.dependency_overrides[deps.get_assessor] = lambda: FakeAssessor()
    try:
        with TestClient(app) as client:
            assert client.get("/merchant/disputes", headers=MERCHANT).json() == []
            assert client.post("/merchant/disputes/sync", headers=MERCHANT).status_code == 409
            r = client.post("/webhooks/paypal", json={
                "id": "WH-DISABLED-1",
                "event_type": "CUSTOMER.DISPUTE.CREATED",
                "resource": paypal.get_dispute("PP-D-7001"),
            })
            assert r.status_code == 200 and r.json()["handled"] is False
    finally:
        app.dependency_overrides.clear()


def test_background_housekeeping_syncs_disputes(db, paypal):
    paypal.open_dispute("CAP1", dispute_id="PP-D-8001")
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    work = BackgroundWork(Session, paypal=paypal)
    due = work.last_housekeeping + settings.housekeeping_interval_s
    assert work.tick(now=due) is True
    assert db.query(Dispute).filter_by(paypal_dispute_id="PP-D-8001").count() == 1


def test_zero_policy_suggestion_is_not_replaced_by_full_refund(svc, paypal, db):
    claim = submit(svc, order_id=1, text="wrong")
    assert claim.latest_decision.suggested_cents == 0
    paypal.open_dispute("CAP1", dispute_id="PP-D-ZERO")
    svc.sync_disputes()
    disp = svc.get_dispute("PP-D-ZERO")
    assert svc.suggested_dispute_cents(disp) == 0
    assert svc.has_dispute_policy_decision(disp) is True

    # Calling accept_dispute without an explicit override_cents on a $0 policy suggestion is rejected
    with pytest.raises(Conflict, match="policy suggests \\$0\\.00"):
        svc.accept_dispute("PP-D-ZERO")

    # Explicit override is allowed and recorded as override=True in audit_log
    r = svc.accept_dispute("PP-D-ZERO", override_cents=500)
    assert r.status == "COMPLETED" and r.amount_cents == 500 and r.override_cents == 500
    approved_logs = [
        a for a in db.query(AuditLog).all()
        if a.event == "dispute_accept_approved" and (a.detail or {}).get("paypal_dispute_id") == "PP-D-ZERO"
    ]
    assert len(approved_logs) == 1 and approved_logs[0].detail["override"] is True


def test_unassessed_dispute_has_zero_suggestion_and_requires_explicit_amount(svc, paypal):
    # Open a dispute on an order with NO prior RefundPilot claim
    paypal.open_dispute("CAP1", dispute_id="PP-D-NOCLAIM")
    svc.sync_disputes()
    disp = svc.get_dispute("PP-D-NOCLAIM")
    assert svc.has_dispute_policy_decision(disp) is False
    assert svc.suggested_dispute_cents(disp) == 0

    with pytest.raises(Conflict, match="no policy suggestion"):
        svc.accept_dispute("PP-D-NOCLAIM")

    r = svc.accept_dispute("PP-D-NOCLAIM", override_cents=1000)
    assert r.status == "COMPLETED" and r.amount_cents == 1000 and r.override_cents == 1000


def test_unrelated_refund_without_capture_identity_cannot_settle_dispute(svc, paypal):
    claim = submit(svc, order_id=1)
    paypal.open_dispute("CAP1", dispute_id="PP-D-IDENTITY")
    svc.sync_disputes()
    paypal.dispute_resolves_immediately = False
    refund = svc.accept_dispute("PP-D-IDENTITY")
    assert refund.status == "PENDING"
    handled = svc.apply_webhook_refund({
        "id": "UNRELATED-REFUND", "status": "COMPLETED",
        "amount": {"value": "19.00", "currency_code": "USD"},
    })
    assert handled is False
    assert claim.order.refunded_cents == 0
    assert refund.paypal_refund_id is None


def test_webhook_refund_for_different_capture_with_same_amount_is_not_bound(svc, paypal):
    claim = submit(svc, order_id=1)     # CAP1, $19.00
    paypal.open_dispute("CAP1", dispute_id="PP-D-DIFFCAP")
    svc.sync_disputes()
    paypal.dispute_resolves_immediately = False
    refund = svc.accept_dispute("PP-D-DIFFCAP")
    assert refund.status == "PENDING"

    # A $19.00 partial refund on CAP2 (order #2) must not attach to CAP1's dispute refund
    handled = svc.apply_webhook_refund({
        "id": "REF-ON-CAP2",
        "status": "COMPLETED",
        "capture_id": "CAP2",
        "amount": {"value": "19.00", "currency_code": "USD"},
    })
    assert handled is False
    assert claim.order.refunded_cents == 0
    assert refund.paypal_refund_id is None


def test_late_resolution_after_timeout_still_settles_ledger(svc, paypal):
    claim = submit(svc, order_id=1)
    paypal.open_dispute("CAP1", dispute_id="PP-D-LATE")
    svc.sync_disputes()
    original_accept = paypal.accept_claim
    queued = []

    def delayed_accept(*args, **kwargs):
        queued.append((args, kwargs))
        raise httpx.ReadTimeout("accepted request is still being processed upstream")

    paypal.accept_claim = delayed_accept
    refund = svc.accept_dispute("PP-D-LATE")
    assert refund.status == "UNKNOWN"
    svc.reconcile()  # GET still shows seller-response status before upstream commits.
    original_accept(*queued[0][0], **queued[0][1])
    resolved = paypal.get_dispute("PP-D-LATE")
    assert resolved["status"] == "RESOLVED"
    svc.apply_webhook_dispute(resolved, "CUSTOMER.DISPUTE.RESOLVED")
    assert claim.order.refunded_cents == 1900
    assert refund.status == "COMPLETED"


def test_evidence_requires_paypal_action_link(svc, paypal):
    submit(svc, order_id=1)
    payload = paypal.open_dispute("CAP1", dispute_id="PP-D-NO-EVIDENCE", status="OPEN")
    payload["links"] = [link for link in payload["links"] if link["rel"] != "provide-evidence"]
    svc.sync_disputes()
    with pytest.raises(Conflict):
        svc.submit_dispute_evidence("PP-D-NO-EVIDENCE")
    assert not [call for call in paypal.calls if call[0] == "provide_evidence"]


def test_accept_claim_checks_hateoas_link_and_allowed_response_options(svc, paypal):
    submit(svc, order_id=1)
    payload = paypal.open_dispute("CAP1", dispute_id="PP-D-ONLY-FULL", status="WAITING_FOR_SELLER_RESPONSE")
    payload["allowed_response_options"] = {"accept_claim": {"accept_claim_types": ["REFUND"]}}
    svc.sync_disputes()

    # Partial refund ($10.00 < $19.00) is blocked when PayPal only permits full REFUND
    with pytest.raises(Conflict, match="PARTIAL_REFUND"):
        svc.accept_dispute("PP-D-ONLY-FULL", override_cents=1000)
    assert not [call for call in paypal.calls if call[0] == "accept_claim"]

    # Removing accept-claim link blocks acceptance altogether
    payload["links"] = [link for link in payload["links"] if link["rel"] != "accept-claim"]
    svc.sync_disputes()
    with pytest.raises(Conflict, match="accept-claim"):
        svc.accept_dispute("PP-D-ONLY-FULL", override_cents=1900)
    assert not [call for call in paypal.calls if call[0] == "accept_claim"]

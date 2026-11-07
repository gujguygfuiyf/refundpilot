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


def test_accept_claim_timeout_then_reconcile_replays_same_request_id(svc, paypal):
    submit(svc, order_id=1)
    paypal.open_dispute("CAP1", dispute_id="PP-D-3002")
    svc.sync_disputes()

    paypal.fail_next = httpx.ReadTimeout("network timeout")
    r = svc.accept_dispute("PP-D-3002")
    assert r.status == "UNKNOWN"
    assert svc.get_dispute("PP-D-3002").status == "PROCESSING"

    assert svc.reconcile() == 1
    disp = svc.get_dispute("PP-D-3002")
    assert disp.status == "RESOLVED"
    assert disp.order.refunded_cents == 1900
    accept_calls = [c for c in paypal.calls if c[0] == "accept_claim"]
    assert len(accept_calls) == 2 and accept_calls[0][3] == accept_calls[1][3]
    assert len(paypal.refunds) == 1


def test_dispute_refund_and_capture_refunded_webhook_never_double_credit(svc, paypal, db):
    """Whether accept-claim returns a refund_id immediately or PayPal delivers PAYMENT.CAPTURE.REFUNDED afterwards,
    the order ledger must be credited once."""
    submit(svc, order_id=2)             # $49.00 order (CAP2)
    paypal.open_dispute("CAP2", dispute_id="PP-D-4001")
    svc.sync_disputes()

    r = svc.accept_dispute("PP-D-4001", override_cents=2000)
    assert r.status == "COMPLETED" and r.amount_cents == 2000
    assert svc.get_dispute("PP-D-4001").order.refunded_cents == 2000

    # 1) Webhook arrives with the same refund_id
    webhook_resource = {
        "id": r.paypal_refund_id,
        "status": "COMPLETED",
        "amount": {"value": "20.00", "currency_code": "USD"},
        "links": [{"rel": "up", "href": "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP2"}],
    }
    assert svc.apply_webhook_refund(webhook_resource) is True
    assert svc.get_dispute("PP-D-4001").order.refunded_cents == 2000

    # 2) Simulate PayPal accept-claim returning only HATEOAS links (no refund_id in sync response),
    # followed by PAYMENT.CAPTURE.REFUNDED webhook assigning the refund id.
    r.paypal_refund_id = None
    db.commit()
    webhook_resource_2 = {
        "id": "R-LATE-WEBHOOK-99",
        "status": "COMPLETED",
        "amount": {"value": "20.00", "currency_code": "USD"},
        "links": [{"rel": "up", "href": "https://api-m.sandbox.paypal.com/v2/payments/captures/CAP2"}],
    }
    assert svc.apply_webhook_refund(webhook_resource_2) is True
    db.refresh(r)
    assert r.paypal_refund_id == "R-LATE-WEBHOOK-99"
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

"""ClaimService: state machine, assessment pipeline, idempotent approval, reconciliation.

Only `approve()` moves money. It is the single entry point.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai import AssessmentInput, Assessor, prepare_jpeg
from .audit import audit
from .models import (APPROVABLE_STATES, OPEN_STATES, TERMINAL_STATES, AIAssessment, Claim, Decision, Order, Photo,
                     Policy, Refund, now_iso)
from .paypal import PayPalError, PayPalGateway
from .policy import decide, status_for


class Conflict(Exception):
    """State or amount conflict; maps to HTTP 409."""


class NotFound(Exception):
    """Maps to HTTP 404."""


class BadInput(Exception):
    """Unreadable upload; maps to HTTP 400."""


def _utc(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _days_between(iso_a: str, iso_b: str | None = None) -> int:
    a = _utc(iso_a)
    b = _utc(iso_b) if iso_b else datetime.now(timezone.utc)
    return max(0, (b - a).days)


def prepare_photos(photos: list[bytes]) -> list[tuple[bytes, int, int]]:
    """Decode and re-encode every upload before anything is stored, so a corrupt or non-image file is a clean 400."""
    out = []
    for raw in photos[:3]:
        try:
            out.append(prepare_jpeg(raw))
        except Exception:  # UnidentifiedImageError, truncated files, decompression bombs, HEIC without a plugin
            raise BadInput("a photo could not be read as an image (use JPEG, PNG or WebP)")
    return out


class ClaimService:
    def __init__(self, db: Session, paypal: PayPalGateway, assessor: Assessor, default_policy_version: str):
        self.db, self.paypal, self.assessor = db, paypal, assessor
        self.default_policy_version = default_policy_version

    # ------------------------------------------------------------------ helpers
    def get_claim(self, claim_id: str) -> Claim:
        c = self.db.get(Claim, claim_id)
        if not c:
            raise NotFound(claim_id)
        return c

    def policy(self, version: str) -> dict:
        p = self.db.get(Policy, version)
        if not p:
            raise NotFound(f"policy {version}")
        return p.body

    def open_claim_for_order(self, order_id: int) -> Claim | None:
        return self.db.execute(select(Claim).where(Claim.order_id == order_id, Claim.status.in_(OPEN_STATES))).scalars().first()

    def prior_claims(self, order_id: int, exclude: str) -> int:
        rows = self.db.execute(select(Claim).where(Claim.order_id == order_id, Claim.id != exclude)).scalars().all()
        return sum(1 for r in rows if r.status != "CANCELLED")

    # ------------------------------------------------------------------ buyer side
    def create_claim(self, order_id: int, buyer_text: str, photos: list[bytes],
                     policy_version: str | None = None) -> Claim:
        order = self.db.get(Order, order_id)
        if not order:
            raise NotFound(f"order {order_id}")
        if order.remaining_cents <= 0:
            raise Conflict("order is already fully refunded")
        if self.open_claim_for_order(order_id):
            raise Conflict("order already has an open claim")
        if not photos:
            raise Conflict("at least one photo is required")
        prepared = prepare_photos(photos)
        claim = Claim(order_id=order_id, buyer_text=buyer_text.strip()[:2000],
                      policy_version=policy_version or self.default_policy_version, status="SUBMITTED")
        self.db.add(claim)
        self.db.flush()
        self._add_photos(claim, prepared)
        audit(self.db, claim.id, "buyer", "claim_submitted", {"order_id": order_id, "photos": len(photos)})
        self.db.commit()
        return claim

    def add_evidence(self, claim_id: str, photos: list[bytes], buyer_text: str | None = None) -> Claim:
        claim = self.get_claim(claim_id)
        if claim.status != "EVIDENCE_REQUESTED":
            raise Conflict(f"claim is {claim.status}, not waiting for evidence")
        if not photos:
            raise Conflict("at least one photo is required")
        self._add_photos(claim, prepare_photos(photos))
        if buyer_text:
            claim.buyer_text = (claim.buyer_text + "\n---\n" + buyer_text.strip())[:4000]
        claim.status = "SUBMITTED"
        audit(self.db, claim.id, "buyer", "evidence_added", {"photos": len(photos)})
        self.db.commit()
        return claim

    def cancel(self, claim_id: str) -> Claim:
        claim = self.get_claim(claim_id)
        if claim.status in ("PROCESSING",) + TERMINAL_STATES:
            raise Conflict(f"cannot cancel a claim in {claim.status}")
        claim.status, claim.resolved_at = "CANCELLED", now_iso()
        audit(self.db, claim.id, "buyer", "cancelled")
        self.db.commit()
        return claim

    def _add_photos(self, claim: Claim, prepared: list[tuple[bytes, int, int]]) -> None:
        for jpg, w, h in prepared:
            claim.photos.append(Photo(claim_id=claim.id, bytes=jpg, mime="image/jpeg", width=w, height=h,
                                      sha256=hashlib.sha256(jpg).hexdigest()))

    # ------------------------------------------------------------------ pipeline
    def run_assessment(self, claim_id: str) -> Claim:
        """SUBMITTED -> ANALYZING -> (REVIEW_REQUIRED | AUTO_ELIGIBLE | EVIDENCE_REQUESTED | ANALYZE_FAILED)."""
        claim = self.get_claim(claim_id)
        if claim.status not in ("SUBMITTED", "ANALYZE_FAILED", "REVIEW_REQUIRED", "EVIDENCE_REQUESTED"):
            raise Conflict(f"claim is {claim.status}")
        claim.status = "ANALYZING"
        audit(self.db, claim.id, "system", "analysis_started")
        self.db.commit()

        order = claim.order
        inp = AssessmentInput(product_name=order.product.name, product_image_url=order.product.image_url,
                              buyer_photos=[p.bytes for p in claim.photos], buyer_text=claim.buyer_text,
                              order_cents=order.amount_cents, days_since_delivery=_days_between(order.delivered_at))
        try:
            result = self.assessor.assess(inp)
        except Exception as e:  # the chain already swallows provider errors; this is belt and braces
            claim.status = "ANALYZE_FAILED"
            audit(self.db, claim.id, "ai", "analysis_failed", {"error": str(e)[:300]})
            self.db.commit()
            return claim

        a = AIAssessment(claim_id=claim.id, provider=result.provider, model=result.model, input_sha256=inp.sha256(),
                         output=result.assessment.model_dump(), latency_ms=result.latency_ms,
                         input_tokens=result.input_tokens, output_tokens=result.output_tokens, refused=int(result.refused))
        claim.assessments.append(a)
        self.db.flush()
        audit(self.db, claim.id, "ai", "assessed", {"provider": result.provider, "model": result.model,
                                                    "damage_type": result.assessment.damage_type,
                                                    "evidence_quality": result.assessment.evidence_quality})
        self._decide(claim, a)
        self.db.commit()
        return claim

    def _decide(self, claim: Claim, a: AIAssessment) -> Decision:
        order = claim.order
        policy = self.policy(claim.policy_version)
        d = decide(policy, a.output, order_cents=order.amount_cents, remaining_cents=order.remaining_cents,
                   days_since_delivery=_days_between(order.delivered_at, claim.created_at),
                   prior_claims=self.prior_claims(order.id, exclude=claim.id))
        dec = Decision(claim_id=claim.id, assessment_id=a.id, policy_version=claim.policy_version, rule_id=d.rule_id,
                       outcome=d.outcome, suggested_cents=d.suggested_cents, requires_approval=int(d.requires_approval),
                       reason=d.reason)
        claim.decisions.append(dec)
        claim.status = status_for(d)
        audit(self.db, claim.id, "rules", "decided", {"policy_version": claim.policy_version, "rule_id": d.rule_id,
                                                      "outcome": d.outcome, "suggested_cents": d.suggested_cents})
        return dec

    def redecide(self, claim_id: str, policy_version: str | None = None) -> Claim:
        """Re-run the rules on the latest assessment (e.g. after switching policy). No new AI call."""
        claim = self.get_claim(claim_id)
        if claim.status in ("PROCESSING",) + TERMINAL_STATES:
            raise Conflict(f"claim is {claim.status}")
        a = claim.latest_assessment
        if not a:
            raise Conflict("no assessment yet")
        if policy_version:
            self.policy(policy_version)
            claim.policy_version = policy_version
        self._decide(claim, a)
        self.db.commit()
        return claim

    # ------------------------------------------------------------------ merchant actions
    def request_evidence(self, claim_id: str, message: str, by: str = "merchant") -> Claim:
        claim = self.get_claim(claim_id)
        if claim.status not in ("REVIEW_REQUIRED", "AUTO_ELIGIBLE", "ANALYZE_FAILED", "FAILED"):
            raise Conflict(f"claim is {claim.status}")
        claim.status, claim.merchant_message = "EVIDENCE_REQUESTED", message.strip()[:1000]
        audit(self.db, claim.id, by, "evidence_requested", {"message": claim.merchant_message})
        self.db.commit()
        return claim

    def reject(self, claim_id: str, reason: str, by: str = "merchant") -> Claim:
        claim = self.get_claim(claim_id)
        if claim.status in ("PROCESSING",) + TERMINAL_STATES:
            raise Conflict(f"claim is {claim.status}")
        claim.status, claim.merchant_message, claim.resolved_at = "REJECTED", reason.strip()[:1000], now_iso()
        audit(self.db, claim.id, by, "rejected", {"reason": claim.merchant_message})
        self.db.commit()
        return claim

    def approve(self, claim_id: str, by: str = "merchant", override_cents: int | None = None) -> Refund:
        """The only code path that moves money. Idempotent against double clicks and retries."""
        # ---- transaction 1: check-and-set ----
        claim = self.get_claim(claim_id)
        if claim.status not in APPROVABLE_STATES:
            raise Conflict(f"claim is {claim.status}, not approvable")
        order, decision = claim.order, claim.latest_decision
        if decision is None:
            raise Conflict("no decision to approve")
        remaining = order.remaining_cents
        cents = decision.suggested_cents if override_cents is None else int(override_cents)
        if cents <= 0 or cents > remaining:
            raise Conflict(f"amount {cents} out of range (1..{remaining})")
        attempt = len(claim.refunds) + 1
        refund = Refund(claim_id=claim.id, capture_id=order.capture_id, amount_cents=cents, currency=order.currency,
                        override_cents=override_cents, approved_by=by,
                        paypal_request_id=f"rp-{claim.id}-{attempt}", status="CREATED")
        claim.status = "PROCESSING"
        claim.refunds.append(refund)
        audit(self.db, claim.id, by, "approved", {"cents": cents, "override": override_cents is not None, "attempt": attempt})
        self.db.commit()           # from here on, a concurrent approve sees PROCESSING and gets 409

        # ---- network, outside any transaction ----
        try:
            live = self.paypal.get_capture(order.capture_id)
            if live.get("status") == "REFUNDED":
                raise Conflict("capture already fully refunded at PayPal")
            resp = self.paypal.refund_capture(order.capture_id, cents, order.currency,
                                              request_id=refund.paypal_request_id, invoice_id=f"claim-{claim.id}",
                                              note="Refund for the issue you reported")
        except httpx.TimeoutException as e:
            refund.status, refund.error = "UNKNOWN", f"timeout: {e}"   # reconcile will replay the same request id
            audit(self.db, claim.id, "paypal", "refund_timeout", {"request_id": refund.paypal_request_id})
            self.db.commit()
            return refund
        except (PayPalError, Conflict) as e:
            refund.status, refund.error = "FAILED", str(e)[:500]
            claim.status = "REVIEW_REQUIRED"
            audit(self.db, claim.id, "paypal", "refund_failed", {"error": refund.error})
            self.db.commit()
            return refund

        # ---- transaction 2: record the result ----
        self._apply_refund_response(refund, resp, source="api")
        self.db.commit()
        return refund

    # ------------------------------------------------------------------ finalisation & reconciliation
    def _apply_refund_response(self, refund: Refund, resp: dict, source: str) -> None:
        refund.paypal_refund_id = resp.get("id") or refund.paypal_refund_id
        refund.raw_response = resp
        status = resp.get("status") or "PENDING"
        refund.status = status
        audit(self.db, refund.claim_id, "paypal", f"refund_{status.lower()}",
              {"refund_id": refund.paypal_refund_id, "source": source})
        if status == "COMPLETED":
            self.finalize(refund)
        elif status in ("FAILED", "CANCELLED"):
            claim = self.get_claim(refund.claim_id)
            claim.status = "REVIEW_REQUIRED"
            refund.error = refund.error or f"PayPal reported {status}"

    def finalize(self, refund: Refund) -> None:
        """Idempotent: credits the order ledger and closes the claim exactly once."""
        if refund.finalized_at:
            return
        claim = self.get_claim(refund.claim_id)
        claim.order.refunded_cents += refund.amount_cents
        refund.finalized_at = now_iso()
        refund.status = "COMPLETED"
        claim.status, claim.resolved_at = "COMPLETED", now_iso()
        audit(self.db, claim.id, "system", "claim_completed", {"refund_id": refund.paypal_refund_id,
                                                              "cents": refund.amount_cents})

    def reconcile(self) -> int:
        """Resolve UNKNOWN (replay same request id) and PENDING (poll) refunds. Returns number touched."""
        rows = self.db.execute(select(Refund).where(Refund.status.in_(("UNKNOWN", "PENDING")))).scalars().all()
        touched = 0
        for r in rows:
            claim = self.get_claim(r.claim_id)
            try:
                if r.status == "UNKNOWN":
                    resp = self.paypal.refund_capture(r.capture_id, r.amount_cents, r.currency,
                                                      request_id=r.paypal_request_id, invoice_id=f"claim-{claim.id}")
                else:
                    resp = self.paypal.get_refund(r.paypal_refund_id)
            except httpx.TimeoutException:
                continue
            except PayPalError as e:
                r.status, r.error = "FAILED", str(e)[:500]
                claim.status = "REVIEW_REQUIRED"
                audit(self.db, claim.id, "paypal", "refund_failed", {"error": r.error, "source": "reconcile"})
                touched += 1
                continue
            self._apply_refund_response(r, resp, source="reconcile")
            touched += 1
        self.db.commit()
        return touched

    def apply_webhook_refund(self, resource: dict) -> bool:
        """PAYMENT.CAPTURE.REFUNDED: resource is the refund object. Returns True if a refund row was updated."""
        rid = resource.get("id")
        if not rid:
            return False
        r = self.db.execute(select(Refund).where(Refund.paypal_refund_id == rid)).scalars().first()
        if not r:
            return False
        self._apply_refund_response(r, {**(r.raw_response or {}), **resource}, source="webhook")
        self.db.commit()
        return True

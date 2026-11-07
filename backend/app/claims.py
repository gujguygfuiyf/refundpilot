"""ClaimService: state machine, assessment pipeline, idempotent approval, reconciliation, and PayPal Disputes."""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai import AssessmentInput, Assessor, prepare_jpeg
from .audit import audit
from .config import settings
from .models import (APPROVABLE_STATES, OPEN_STATES, TERMINAL_STATES, AIAssessment, AuditLog, Claim, Decision, Dispute,
                     Order, Photo, Policy, Refund, now_iso)
from .paypal import PayPalError, PayPalGateway, fmt_amount
from .policy import decide, status_for


# Set whenever a refund is left UNKNOWN or PENDING. The background loop only opens a database session when
# this is set (or on its slow housekeeping tick), so a serverless database (Neon) can scale to zero while the
# API idles. Without that, a once-a-minute poll would keep the database billed around the clock.
reconcile_needed = threading.Event()

DISPUTE_ACTIONABLE_STATUSES = ("WAITING_FOR_SELLER_RESPONSE", "OPEN")


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


def _parse_cents(val: str | float | int | None, fallback: int = 0) -> int:
    if val is None:
        return fallback
    try:
        return int(round(float(val) * 100))
    except (ValueError, TypeError):
        return fallback


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

    def get_dispute(self, dispute_id: str) -> Dispute:
        d = self.db.get(Dispute, dispute_id)
        if not d:
            d = self.db.execute(select(Dispute).where(Dispute.paypal_dispute_id == dispute_id)).scalars().first()
        if not d:
            raise NotFound(dispute_id)
        return d

    def policy(self, version: str) -> dict:
        p = self.db.get(Policy, version)
        if not p:
            raise NotFound(f"policy {version}")
        return p.body

    def open_claim_for_order(self, order_id: int) -> Claim | None:
        return self.db.execute(select(Claim).where(Claim.order_id == order_id, Claim.status.in_(OPEN_STATES))).scalars().first()

    def latest_claim_for_order(self, order_id: int) -> Claim | None:
        return self.db.execute(
            select(Claim)
            .where(Claim.order_id == order_id, Claim.status != "CANCELLED")
            .order_by(Claim.created_at.desc())
        ).scalars().first()

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
        # If an open PayPal dispute already exists for this order without a linked claim, link it automatically.
        for disp in self.db.execute(select(Dispute).where(Dispute.order_id == order_id, Dispute.claim_id.is_(None))).scalars().all():
            disp.claim_id = claim.id
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
        """Direct capture refund path. Idempotent against double clicks and retries."""
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
            reconcile_needed.set()
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
        refund.paypal_refund_id = resp.get("id") or resp.get("refund_id") or refund.paypal_refund_id
        refund.raw_response = resp
        status = resp.get("status") or "PENDING"
        if status == "RESOLVED":
            status = "COMPLETED"
        refund.status = status
        audit(self.db, refund.claim_id, "paypal", f"refund_{status.lower()}",
              {"refund_id": refund.paypal_refund_id, "source": source})
        if status == "COMPLETED":
            self.finalize(refund)
        elif status in ("FAILED", "CANCELLED"):
            claim = self.get_claim(refund.claim_id)
            claim.status = "REVIEW_REQUIRED"
            refund.error = refund.error or f"PayPal reported {status}"
        else:                                   # PENDING (eCheck): poll until PayPal settles it
            reconcile_needed.set()

    def finalize(self, refund: Refund) -> None:
        """Idempotent: credits the order ledger and closes the claim (and any linked dispute) exactly once."""
        if refund.finalized_at:
            return
        claim = self.get_claim(refund.claim_id)
        claim.order.refunded_cents += refund.amount_cents
        refund.finalized_at = now_iso()
        refund.status = "COMPLETED"
        claim.status, claim.resolved_at = "COMPLETED", now_iso()
        for disp in self.db.execute(select(Dispute).where(Dispute.order_id == claim.order_id)).scalars().all():
            if disp.status == "PROCESSING":
                disp.status = "RESOLVED"
                disp.resolved_at = disp.resolved_at or now_iso()
        audit(self.db, claim.id, "system", "claim_completed", {"refund_id": refund.paypal_refund_id,
                                                               "cents": refund.amount_cents})

    def pending_refunds(self) -> int:
        return self.db.execute(select(func.count(Refund.id)).where(Refund.status.in_(("UNKNOWN", "PENDING")))).scalar_one()

    def reconcile(self) -> int:
        """Resolve UNKNOWN (replay same request id) and PENDING (poll) refunds. Returns number touched."""
        rows = self.db.execute(select(Refund).where(Refund.status.in_(("UNKNOWN", "PENDING")))).scalars().all()
        touched = 0
        for r in rows:
            claim = self.get_claim(r.claim_id)
            try:
                if r.status == "UNKNOWN":
                    if r.paypal_request_id.startswith("rp-dispute-accept-"):
                        disp = self.db.execute(select(Dispute).where(Dispute.order_id == claim.order_id)).scalars().first()
                        if not disp:
                            continue
                        resp = self.paypal.accept_claim(
                            disp.paypal_dispute_id, cents=r.amount_cents, currency=r.currency,
                            dispute_amount_cents=disp.dispute_amount_cents or claim.order.amount_cents,
                            note="Accepting claim under merchant refund policy.", request_id=r.paypal_request_id,
                        )
                    else:
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
        """PAYMENT.CAPTURE.REFUNDED: resource is the refund object. Returns True if a refund row was updated.
        Deduplicates against dispute accept-claim refunds so the same money is never credited twice."""
        rid = resource.get("id")
        if not rid:
            return False
        r = self.db.execute(select(Refund).where(Refund.paypal_refund_id == rid)).scalars().first()
        if not r:
            # A dispute accept-claim refund may have been finalized before PayPal assigned or broadcast its refund id.
            cap_id = resource.get("capture_id")
            if not cap_id:
                for link in resource.get("links") or []:
                    href = link.get("href") or ""
                    if "/v2/payments/captures/" in href:
                        cap_id = href.rsplit("/v2/payments/captures/", 1)[-1].split("/")[0]
                        break
            amt_cents = _parse_cents((resource.get("amount") or {}).get("value"), fallback=-1)
            q = select(Refund).where(
                Refund.paypal_refund_id.is_(None),
                Refund.paypal_request_id.like("rp-dispute-accept-%"),
            )
            if cap_id:
                q = q.where(Refund.capture_id == cap_id)
            if amt_cents > 0:
                q = q.where(Refund.amount_cents == amt_cents)
            r = self.db.execute(q.order_by(Refund.id.desc())).scalars().first()
            if not r:
                return False
            r.paypal_refund_id = rid
        self._apply_refund_response(r, {**(r.raw_response or {}), **resource}, source="webhook")
        self.db.commit()
        return True

    # ------------------------------------------------------------------ PayPal Disputes v1
    def upsert_dispute_from_paypal(self, payload: dict, source: str = "sync") -> Dispute | None:
        """Create or update a local Dispute row from a PayPal dispute JSON resource."""
        if not settings.disputes_enabled:
            return None
        did = payload.get("dispute_id")
        if not did:
            return None

        # Summary items from GET /v1/customer/disputes may omit disputed_transactions; fetch full detail if needed.
        txs = payload.get("disputed_transactions") or []
        if not txs:
            try:
                full = self.paypal.get_dispute(did)
                if full and full.get("dispute_id"):
                    payload = full
                    txs = payload.get("disputed_transactions") or []
            except Exception:
                pass

        cap_id = None
        for tx in txs:
            if tx.get("seller_transaction_id"):
                cap_id = tx["seller_transaction_id"]
                break

        order = self.db.execute(select(Order).where(Order.capture_id == cap_id)).scalars().first() if cap_id else None
        claim = self.latest_claim_for_order(order.id) if order else None

        amt_obj = payload.get("dispute_amount") or {}
        if not amt_obj and txs:
            amt_obj = txs[0].get("gross_amount") or {}
        default_cents = order.amount_cents if order else 0
        dispute_cents = _parse_cents(amt_obj.get("value"), fallback=default_cents)
        currency = amt_obj.get("currency_code") or (order.currency if order else "USD")
        reason = payload.get("reason") or "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED"
        status = payload.get("status") or "WAITING_FOR_SELLER_RESPONSE"
        stage = payload.get("dispute_life_cycle_stage") or "INQUIRY"

        row = self.db.execute(select(Dispute).where(Dispute.paypal_dispute_id == did)).scalars().first()
        if row is None:
            row = Dispute(
                paypal_dispute_id=did,
                order_id=order.id if order else None,
                claim_id=claim.id if claim else None,
                reason=reason,
                status=status,
                stage=stage,
                dispute_amount_cents=dispute_cents,
                currency=currency,
                raw=payload,
                resolved_at=now_iso() if status == "RESOLVED" else None,
            )
            self.db.add(row)
            self.db.flush()
            audit(self.db, row.claim_id, "paypal", "dispute_created", {
                "paypal_dispute_id": did,
                "order_id": row.order_id,
                "capture_id": cap_id,
                "reason": reason,
                "stage": stage,
                "status": status,
                "dispute_amount_cents": dispute_cents,
                "source": source,
            })
        else:
            prev_status, prev_stage = row.status, row.stage
            if order and row.order_id is None:
                row.order_id = order.id
            if claim and row.claim_id is None:
                row.claim_id = claim.id
            row.reason = reason
            row.stage = stage
            if dispute_cents > 0:
                row.dispute_amount_cents = dispute_cents
            row.currency = currency
            row.raw = payload
            if row.status != "PROCESSING" or status == "RESOLVED":
                row.status = status
            if status == "RESOLVED" and not row.resolved_at:
                row.resolved_at = now_iso()
            if (prev_status, prev_stage) != (row.status, row.stage):
                ev_name = "dispute_resolved" if row.status == "RESOLVED" else "dispute_updated"
                audit(self.db, row.claim_id, "paypal", ev_name, {
                    "paypal_dispute_id": did,
                    "status": row.status,
                    "stage": row.stage,
                    "source": source,
                })
        self.db.commit()
        return row

    def apply_webhook_dispute(self, resource: dict, event_type: str = "") -> bool:
        """Handle CUSTOMER.DISPUTE.CREATED / UPDATED / RESOLVED webhook events."""
        if not settings.disputes_enabled:
            return False
        row = self.upsert_dispute_from_paypal(resource, source=f"webhook:{event_type or 'dispute'}")
        return row is not None

    def sync_disputes(self) -> int:
        """Poll GET /v1/customer/disputes and upsert into local database. Returns number of disputes synced."""
        if not settings.disputes_enabled:
            return 0
        items = self.paypal.list_disputes()
        count = 0
        for item in items:
            if self.upsert_dispute_from_paypal(item, source="poll"):
                count += 1
        return count

    def suggested_dispute_cents(self, dispute: Dispute) -> int:
        """Default refund suggestion for a dispute: policy suggestion if available, capped at remaining balance."""
        order = dispute.order
        if not order:
            return 0
        cap = order.remaining_cents
        if dispute.dispute_amount_cents > 0:
            cap = min(cap, dispute.dispute_amount_cents)
        claim = dispute.claim or self.latest_claim_for_order(order.id)
        if claim and claim.latest_decision and claim.latest_decision.suggested_cents > 0:
            return min(claim.latest_decision.suggested_cents, cap)
        return cap

    def build_dispute_evidence(self, dispute: Dispute) -> dict:
        """Deterministically assemble the evidence text and metadata from stored facts (no AI generation)."""
        order = dispute.order
        claim = dispute.claim or (self.latest_claim_for_order(order.id) if order else None)
        completed_refunds: list[Refund] = []
        if order:
            completed_refunds = list(
                self.db.execute(
                    select(Refund).where(Refund.capture_id == order.capture_id, Refund.status == "COMPLETED").order_by(Refund.id)
                ).scalars().all()
            )
        refund_ids = [r.paypal_refund_id for r in completed_refunds if r.paypal_refund_id]
        evidence_type = "PROOF_OF_REFUND" if refund_ids else "OTHER"

        lines: list[str] = [
            f"=== RefundPilot Evidence Package · Dispute {dispute.paypal_dispute_id} ===",
            f"Dispute reason: {dispute.reason} | Stage: {dispute.stage} | Disputed amount: {dispute.currency} {fmt_amount(dispute.dispute_amount_cents)}",
        ]
        if order:
            lines.append(
                f"Order: #{order.id} (PayPal Order {order.paypal_order_id}, Capture {order.capture_id}) | "
                f"Product: {order.product.name} ({order.product.sku}) | "
                f"Captured: {order.captured_at} | Delivered: {order.delivered_at} | "
                f"Order total: {order.currency} {fmt_amount(order.amount_cents)} | "
                f"Refunded so far: {order.currency} {fmt_amount(order.refunded_cents)} | "
                f"Remaining refundable balance: {order.currency} {fmt_amount(order.remaining_cents)}"
            )
        if completed_refunds:
            lines.append("")
            lines.append("--- Prior PayPal Refund(s) Recorded in RefundPilot ---")
            for r in completed_refunds:
                lines.append(
                    f"- Refund ID: {r.paypal_refund_id or 'N/A'} | Amount: {r.currency} {fmt_amount(r.amount_cents)} | "
                    f"Status: {r.status} | Request ID: {r.paypal_request_id} | Finalized: {r.finalized_at or r.created_at}"
                )
        if claim:
            lines.append("")
            lines.append(f"--- Claim {claim.id} (status: {claim.status}, policy: {claim.policy_version}) ---")
            lines.append(f"Buyer statement: {claim.buyer_text or '(none)'}")
            lines.append(f"Buyer photos on file: {len(claim.photos)}")
            a = claim.latest_assessment
            if a:
                o = a.output or {}
                lines.append(
                    f"AI structured observations ({a.provider}/{a.model}): "
                    f"product_match={o.get('product_match')}, damage_type={o.get('damage_type')}, "
                    f"severity={o.get('severity')}, evidence_quality={o.get('evidence_quality')}, "
                    f"stock_photo_suspected={o.get('stock_photo_suspected')}, "
                    f"description_consistent={o.get('description_consistent')}"
                )
                if o.get("observations"):
                    lines.append("Observations: " + "; ".join(str(x) for x in o["observations"]))
                if o.get("limitations"):
                    lines.append("Limitations: " + "; ".join(str(x) for x in o["limitations"]))
            d = claim.latest_decision
            if d:
                lines.append(
                    f"Policy decision (version {d.policy_version}): rule={d.rule_id}, outcome={d.outcome}, "
                    f"suggested={order.currency if order else 'USD'} {fmt_amount(d.suggested_cents)} ({d.reason})"
                )
            if claim.merchant_message:
                lines.append(f"Merchant decision note: {claim.merchant_message}")
            audits = self.db.execute(
                select(AuditLog).where(AuditLog.claim_id == claim.id).order_by(AuditLog.id)
            ).scalars().all()
            if audits:
                lines.append("")
                lines.append("--- Audit Timeline ---")
                for entry in audits:
                    lines.append(f"- {entry.ts} [{entry.actor}] {entry.event}")
        else:
            lines.append("")
            lines.append("No pre-dispute buyer claim was filed in RefundPilot for this order.")

        notes = "\n".join(lines)[:2000]
        photo_urls = [f"/claims/{claim.id}/photos/{i}" for i in range(len(claim.photos))] if claim else []
        return {
            "evidence_type": evidence_type,
            "refund_ids": refund_ids,
            "notes": notes,
            "photo_urls": photo_urls,
        }

    def accept_dispute(self, dispute_id: str, by: str = "merchant",
                       override_cents: int | None = None, note: str | None = None) -> Refund:
        """Accept a PayPal dispute and refund the buyer idempotently."""
        if not settings.disputes_enabled:
            raise Conflict("PayPal Disputes integration is disabled (DISPUTES_ENABLED=false)")
        dispute = self.get_dispute(dispute_id)
        if dispute.status not in DISPUTE_ACTIONABLE_STATUSES:
            raise Conflict(f"dispute is {dispute.status}, cannot accept claim")
        order = dispute.order
        if not order:
            raise Conflict("dispute is not matched to an order")
        remaining = order.remaining_cents
        if remaining <= 0:
            raise Conflict("order is already fully refunded")

        claim = dispute.claim or self.latest_claim_for_order(order.id)
        if claim is None:
            claim = Claim(
                order_id=order.id,
                buyer_text=f"[PayPal Dispute {dispute.paypal_dispute_id}] {dispute.reason}",
                policy_version=self.default_policy_version,
                status="APPROVED",
            )
            self.db.add(claim)
            self.db.flush()
            dispute.claim_id = claim.id
        elif dispute.claim_id is None:
            dispute.claim_id = claim.id

        default_cents = self.suggested_dispute_cents(dispute)
        cents = default_cents if override_cents is None else int(override_cents)
        max_cents = min(remaining, dispute.dispute_amount_cents) if dispute.dispute_amount_cents > 0 else remaining
        if cents <= 0 or cents > remaining:
            raise Conflict(f"amount {cents} out of range (1..{remaining})")
        if dispute.dispute_amount_cents > 0 and cents > dispute.dispute_amount_cents:
            raise Conflict(f"amount {cents} exceeds disputed amount ({max_cents})")

        # ---- transaction 1: check-and-set ----
        dispute.accept_attempts += 1
        attempt = dispute.accept_attempts
        req_id = f"rp-dispute-accept-{dispute.paypal_dispute_id}-{attempt}"
        refund = Refund(
            claim_id=claim.id,
            capture_id=order.capture_id,
            amount_cents=cents,
            currency=order.currency,
            override_cents=override_cents,
            approved_by=by,
            paypal_request_id=req_id,
            status="CREATED",
        )
        dispute.status = "PROCESSING"
        claim.status = "PROCESSING"
        claim.refunds.append(refund)
        audit(self.db, claim.id, by, "dispute_accept_approved", {
            "paypal_dispute_id": dispute.paypal_dispute_id,
            "cents": cents,
            "override": override_cents is not None,
            "attempt": attempt,
            "request_id": req_id,
        })
        self.db.commit()

        # ---- network, outside any transaction ----
        accept_note = (note or f"Accepted under policy ({claim.policy_version})").strip()[:2000]
        try:
            live = self.paypal.get_capture(order.capture_id)
            if live.get("status") == "REFUNDED":
                raise Conflict("capture already fully refunded at PayPal")
            resp = self.paypal.accept_claim(
                dispute.paypal_dispute_id,
                cents=cents,
                currency=order.currency,
                dispute_amount_cents=dispute.dispute_amount_cents or order.amount_cents,
                note=accept_note,
                request_id=req_id,
            )
        except httpx.TimeoutException as e:
            refund.status, refund.error = "UNKNOWN", f"timeout: {e}"
            reconcile_needed.set()
            audit(self.db, claim.id, "paypal", "dispute_accept_timeout", {
                "paypal_dispute_id": dispute.paypal_dispute_id,
                "request_id": req_id,
            })
            self.db.commit()
            return refund
        except (PayPalError, Conflict) as e:
            refund.status, refund.error = "FAILED", str(e)[:500]
            dispute.status = "WAITING_FOR_SELLER_RESPONSE"
            claim.status = "REVIEW_REQUIRED"
            audit(self.db, claim.id, "paypal", "dispute_accept_failed", {
                "paypal_dispute_id": dispute.paypal_dispute_id,
                "error": refund.error,
            })
            self.db.commit()
            return refund

        # ---- transaction 2: record & finalize ----
        refund.paypal_refund_id = resp.get("refund_id") or resp.get("id") or refund.paypal_refund_id
        refund.raw_response = resp
        self.finalize(refund)
        dispute.status = "RESOLVED"
        dispute.resolved_at = now_iso()
        audit(self.db, claim.id, "paypal", "dispute_accepted", {
            "paypal_dispute_id": dispute.paypal_dispute_id,
            "refund_id": refund.paypal_refund_id,
            "cents": cents,
        })
        self.db.commit()
        return refund

    def submit_dispute_evidence(self, dispute_id: str, notes: str | None = None, by: str = "merchant") -> Dispute:
        """Submit structured evidence package to PayPal Disputes API via multipart/form-data."""
        if not settings.disputes_enabled:
            raise Conflict("PayPal Disputes integration is disabled (DISPUTES_ENABLED=false)")
        dispute = self.get_dispute(dispute_id)
        if dispute.status not in DISPUTE_ACTIONABLE_STATUSES:
            raise Conflict(f"dispute is {dispute.status}, cannot submit evidence")

        pkg = self.build_dispute_evidence(dispute)
        final_notes = (notes.strip() if notes and notes.strip() else pkg["notes"])[:2000]
        evidence_type = pkg["evidence_type"]
        refund_ids = pkg["refund_ids"]

        claim = dispute.claim or (self.latest_claim_for_order(dispute.order_id) if dispute.order_id else None)
        if claim and dispute.claim_id is None:
            dispute.claim_id = claim.id

        docs: list[tuple[str, bytes, str]] = []
        if claim:
            for i, p in enumerate(claim.photos[:3]):
                docs.append((f"claim-{claim.id}-photo-{i + 1}.jpg", p.bytes, p.mime or "image/jpeg"))

        dispute.evidence_attempts += 1
        req_id = f"rp-dispute-evid-{dispute.paypal_dispute_id}-{dispute.evidence_attempts}"
        audit(self.db, dispute.claim_id, by, "dispute_evidence_sending", {
            "paypal_dispute_id": dispute.paypal_dispute_id,
            "evidence_type": evidence_type,
            "refund_ids": refund_ids,
            "documents": len(docs),
            "request_id": req_id,
        })
        self.db.commit()

        try:
            resp = self.paypal.provide_evidence(
                dispute.paypal_dispute_id,
                notes=final_notes,
                request_id=req_id,
                evidence_type=evidence_type,
                refund_ids=refund_ids,
                documents=docs,
            )
        except PayPalError as e:
            audit(self.db, dispute.claim_id, "paypal", "dispute_evidence_failed", {
                "paypal_dispute_id": dispute.paypal_dispute_id,
                "error": str(e)[:500],
            })
            self.db.commit()
            raise Conflict(f"PayPal rejected evidence: {e}")

        dispute.status = resp.get("status") or "UNDER_REVIEW"
        dispute.submitted_evidence_notes = final_notes
        audit(self.db, dispute.claim_id, "paypal", "dispute_evidence_submitted", {
            "paypal_dispute_id": dispute.paypal_dispute_id,
            "status": dispute.status,
            "evidence_type": evidence_type,
            "refund_ids": refund_ids,
            "documents": len(docs),
        })
        self.db.commit()
        return dispute

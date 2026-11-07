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
DISPUTE_ACCEPT_PREFIX = "rp-dispute-accept-"
# dispute_outcome.outcome_code values that mean the buyer was refunded from the merchant's capture.
# RESOLVED_WITH_PAYOUT is deliberately absent: PayPal pays the buyer from its own funds, the capture is untouched.
BUYER_FAVOUR_OUTCOMES = ("RESOLVED_BUYER_FAVOUR", "ACCEPTED")


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
        days_since_delivery = _days_between(order.delivered_at, claim.created_at)
        prior_claims = self.prior_claims(order.id, exclude=claim.id)
        remaining_cents = order.remaining_cents
        d = decide(policy, a.output, order_cents=order.amount_cents, remaining_cents=remaining_cents,
                   days_since_delivery=days_since_delivery, prior_claims=prior_claims)
        dec = Decision(claim_id=claim.id, assessment_id=a.id, policy_version=claim.policy_version, rule_id=d.rule_id,
                       outcome=d.outcome, suggested_cents=d.suggested_cents, requires_approval=int(d.requires_approval),
                       reason=d.reason)
        claim.decisions.append(dec)
        claim.status = status_for(d)
        audit(self.db, claim.id, "rules", "decided", {
            "policy_version": claim.policy_version,
            "rule_id": d.rule_id,
            "outcome": d.outcome,
            "suggested_cents": d.suggested_cents,
            "inputs": {
                "order_cents": order.amount_cents,
                "remaining_cents": remaining_cents,
                "days_since_delivery": days_since_delivery,
                "prior_claims": prior_claims,
            },
        })
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
        if refund.paypal_request_id.startswith(DISPUTE_ACCEPT_PREFIX):
            did = self._dispute_id_from_request(refund.paypal_request_id)
            disp = self.db.execute(select(Dispute).where(Dispute.paypal_dispute_id == did)).scalars().first()
            if disp and disp.status != "RESOLVED":
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
                if r.paypal_request_id.startswith(DISPUTE_ACCEPT_PREFIX) and not r.paypal_refund_id:
                    # Never replay accept-claim blindly: ask PayPal what happened to the dispute instead.
                    did = self._dispute_id_from_request(r.paypal_request_id)
                    disp = self.db.execute(select(Dispute).where(Dispute.paypal_dispute_id == did)).scalars().first()
                    if not disp:
                        continue
                    if self._settle_dispute_refund(r, disp, self.paypal.get_dispute(did), source="reconcile"):
                        touched += 1
                    continue
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

    @staticmethod
    def _extract_capture_id(resource: dict) -> str:
        cap_id = resource.get("capture_id")
        if cap_id:
            return str(cap_id).strip()
        related = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get("capture_id")
        if related:
            return str(related).strip()
        for link in resource.get("links") or []:
            href = link.get("href") or ""
            if "/v2/payments/captures/" in href:
                return href.rsplit("/v2/payments/captures/", 1)[-1].split("/")[0].split("?")[0].strip()
        return ""

    def apply_webhook_refund(self, resource: dict) -> bool:
        """PAYMENT.CAPTURE.REFUNDED: resource is the refund object. Returns True if a refund row was updated.
        Deduplicates against dispute accept-claim refunds so the same money is never credited twice."""
        rid = resource.get("id")
        if not rid:
            return False
        r = self.db.execute(select(Refund).where(Refund.paypal_refund_id == rid)).scalars().first()
        if not r:
            # A dispute accept-claim refund may have been finalized before PayPal assigned or broadcast its refund id.
            # Never guess by amount alone: require authoritative capture_id, currency, and exact amount_cents.
            cap_id = self._extract_capture_id(resource)
            amt_obj = resource.get("amount") or {}
            currency = (amt_obj.get("currency_code") or "").strip().upper()
            amt_cents = _parse_cents(amt_obj.get("value"), fallback=-1)
            invoice_id = (resource.get("invoice_id") or "").strip()
            if not cap_id or not currency or amt_cents <= 0:
                try:
                    fetched = self.paypal.get_refund(rid)
                    if isinstance(fetched, dict):
                        resource = {**fetched, **resource}
                        cap_id = cap_id or self._extract_capture_id(resource)
                        amt_obj = resource.get("amount") or fetched.get("amount") or {}
                        currency = currency or (amt_obj.get("currency_code") or "").strip().upper()
                        if amt_cents <= 0:
                            amt_cents = _parse_cents(amt_obj.get("value"), fallback=-1)
                        invoice_id = invoice_id or (resource.get("invoice_id") or "").strip()
                except Exception:
                    pass
            if not cap_id or not currency or amt_cents <= 0:
                return False

            q = select(Refund).where(
                Refund.paypal_refund_id.is_(None),
                Refund.paypal_request_id.like(f"{DISPUTE_ACCEPT_PREFIX}%"),
                Refund.capture_id == cap_id,
                Refund.currency == currency,
                Refund.amount_cents == amt_cents,
                Refund.status.notin_(("FAILED", "CANCELLED")),
            )
            if invoice_id.startswith("dispute-"):
                did = invoice_id[len("dispute-"):]
                if did:
                    q = q.where(Refund.paypal_request_id.like(f"{DISPUTE_ACCEPT_PREFIX}{did}-%"))
            candidates = list(self.db.execute(q.order_by(Refund.id.desc())).scalars().all())
            if len(candidates) != 1:
                return False
            r = candidates[0]
            r.paypal_refund_id = rid
        self._apply_refund_response(r, {**(r.raw_response or {}), **resource}, source="webhook")
        self.db.commit()
        return True

    # ------------------------------------------------------------------ PayPal Disputes v1
    @staticmethod
    def dispute_capabilities(payload: dict | None, status_override: str | None = None) -> tuple[bool, bool, list[str]]:
        """Inspect PayPal HATEOAS links and allowed_response_options for a dispute resource.
        Returns (can_accept_claim, can_provide_evidence, allowed_accept_claim_types)."""
        raw = payload or {}
        status = status_override if status_override is not None else (raw.get("status") or "")
        actionable = status in DISPUTE_ACTIONABLE_STATUSES
        links = raw.get("links")
        if isinstance(links, list):
            rels = {(lk.get("rel") or "").strip() for lk in links if isinstance(lk, dict)}
            has_accept_link = "accept-claim" in rels
            has_evidence_link = "provide-evidence" in rels
        else:
            # If no links array is present at all on a summary object, fall back to actionable status.
            has_accept_link = actionable
            has_evidence_link = actionable

        allowed_opts = ((raw.get("allowed_response_options") or {}).get("accept_claim") or {}).get("accept_claim_types")
        if isinstance(allowed_opts, list):
            allowed_types = [str(x).strip() for x in allowed_opts if str(x).strip()]
        else:
            allowed_types = ["REFUND", "PARTIAL_REFUND"] if (actionable and has_accept_link) else []

        can_accept = actionable and has_accept_link and len(allowed_types) > 0
        can_evidence = actionable and has_evidence_link
        return can_accept, can_evidence, allowed_types

    def upsert_dispute_from_paypal(self, payload: dict, source: str = "sync") -> Dispute | None:
        """Create or update a local Dispute row from a PayPal dispute JSON resource."""
        if not settings.disputes_enabled:
            return None
        did = payload.get("dispute_id")
        if not did:
            return None

        # Summary items from GET /v1/customer/disputes may omit disputed_transactions or links; fetch full detail if needed.
        txs = payload.get("disputed_transactions") or []
        if not txs or "links" not in payload:
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
        if row.status == "RESOLVED" or status == "RESOLVED":
            # CUSTOMER.DISPUTE.RESOLVED (or a poll) settles an accept-claim refund waiting on PayPal
            # (or a previously timed-out attempt if no other attempt has been finalized yet).
            all_attempts = list(self.db.execute(select(Refund).where(
                Refund.paypal_request_id.like(f"{DISPUTE_ACCEPT_PREFIX}{did}-%")
            ).order_by(Refund.id.desc())).scalars().all())
            if not any(a.finalized_at is not None for a in all_attempts):
                target = next((a for a in all_attempts if a.status in ("PENDING", "UNKNOWN")), None)
                if target is None:
                    target = next((a for a in all_attempts if a.status == "FAILED"), None)
                if target is not None:
                    self._settle_dispute_refund(target, row, payload, source=source)
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

    def has_dispute_policy_decision(self, dispute: Dispute) -> bool:
        order = dispute.order
        claim = dispute.claim or (self.latest_claim_for_order(order.id) if order else None)
        return bool(claim and claim.latest_decision is not None)

    def suggested_dispute_cents(self, dispute: Dispute) -> int:
        """Default refund suggestion for a dispute: policy suggestion if available (including $0 when policy rejects
        or requests evidence), capped at remaining balance. Never replaces a $0 or missing policy suggestion with full refund."""
        order = dispute.order
        if not order:
            return 0
        cap = order.remaining_cents
        if dispute.dispute_amount_cents > 0:
            cap = min(cap, dispute.dispute_amount_cents)
        claim = dispute.claim or self.latest_claim_for_order(order.id)
        if claim and claim.latest_decision is not None:
            return max(0, min(claim.latest_decision.suggested_cents, cap))
        return 0

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

        can_accept, _, allowed_types = self.dispute_capabilities(dispute.raw, status_override=dispute.status)
        if not can_accept:
            raise Conflict("PayPal does not allow accept-claim on this dispute (missing accept-claim action link)")

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

        has_policy = self.has_dispute_policy_decision(dispute)
        default_cents = self.suggested_dispute_cents(dispute)
        if override_cents is None and default_cents <= 0:
            if has_policy:
                raise Conflict("policy suggests $0.00 for this claim; enter an explicit override_cents > 0 to refund")
            raise Conflict("no policy suggestion available for this dispute; enter an explicit override_cents > 0")
        cents = default_cents if override_cents is None else int(override_cents)
        max_cents = min(remaining, dispute.dispute_amount_cents) if dispute.dispute_amount_cents > 0 else remaining
        if cents <= 0 or cents > remaining:
            raise Conflict(f"amount {cents} out of range (1..{remaining})")
        if dispute.dispute_amount_cents > 0 and cents > dispute.dispute_amount_cents:
            raise Conflict(f"amount {cents} exceeds disputed amount ({max_cents})")

        dispute_total = dispute.dispute_amount_cents or order.amount_cents
        claim_type = "PARTIAL_REFUND" if cents < dispute_total else "REFUND"
        if allowed_types and claim_type not in allowed_types:
            raise Conflict(f"PayPal does not permit {claim_type} for this dispute (allowed: {', '.join(allowed_types)})")

        is_override = (override_cents is not None) or (not has_policy) or (cents != default_cents)

        # ---- transaction 1: check-and-set ----
        dispute.accept_attempts += 1
        attempt = dispute.accept_attempts
        req_id = f"rp-dispute-accept-{dispute.paypal_dispute_id}-{attempt}"
        refund = Refund(
            claim_id=claim.id,
            capture_id=order.capture_id,
            amount_cents=cents,
            currency=order.currency,
            override_cents=cents if is_override else None,
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
            "override": is_override,
            "has_policy_decision": has_policy,
            "policy_suggested_cents": default_cents if has_policy else None,
            "accept_claim_type": claim_type,
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
                dispute_amount_cents=dispute_total,
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

        # ---- transaction 2: record the acceptance, NOT the refund ----
        # accept-claim answers with HATEOAS links only: no refund id and no refund status. The money is only
        # credited once PayPal itself reports it (dispute resolved in the buyer's favour, or the
        # PAYMENT.CAPTURE.REFUNDED webhook), the same rule as for normal refunds.
        refund.paypal_refund_id = resp.get("refund_id") or refund.paypal_refund_id
        refund.raw_response = resp
        refund.status = "PENDING"
        reconcile_needed.set()
        audit(self.db, claim.id, "paypal", "dispute_accept_sent", {
            "paypal_dispute_id": dispute.paypal_dispute_id,
            "cents": cents,
            "request_id": req_id,
        })
        self.db.commit()
        # PayPal usually resolves a full-refund acceptance at once; check now so the merchant sees it immediately.
        try:
            self._settle_dispute_refund(refund, dispute, self.paypal.get_dispute(dispute.paypal_dispute_id),
                                        source="api")
        except (httpx.TimeoutException, PayPalError):
            pass                                  # webhook or reconcile will settle it
        self.db.commit()
        return refund

    # ------------------------------------------------------------------ dispute refund settlement
    @staticmethod
    def _dispute_id_from_request(request_id: str) -> str:
        """rp-dispute-accept-<paypal dispute id>-<attempt> -> paypal dispute id (which may itself contain dashes)."""
        return request_id[len(DISPUTE_ACCEPT_PREFIX):].rsplit("-", 1)[0]

    def _settle_dispute_refund(self, refund: Refund, dispute: Dispute, payload: dict, source: str) -> bool:
        """Settle a dispute accept-claim refund from PayPal's dispute resource. Returns True if it changed state.

        Credits the ledger only when PayPal reports the dispute resolved in the buyer's favour (or reports an
        amount refunded). `finalize()` is idempotent, so a later PAYMENT.CAPTURE.REFUNDED webhook for the same
        money only attaches the refund id.
        """
        if refund.finalized_at or refund.status == "CANCELLED":
            return False
        status = payload.get("status") or ""
        outcome = payload.get("dispute_outcome") or {}
        refunded = _parse_cents((outcome.get("amount_refunded") or {}).get("value"), fallback=0)
        claim = self.get_claim(refund.claim_id)
        code = outcome.get("outcome_code")
        if status == "RESOLVED" and (code in BUYER_FAVOUR_OUTCOMES or (not code and refunded > 0)):
            if 0 < refunded <= claim.order.remaining_cents and refunded != refund.amount_cents:
                audit(self.db, claim.id, "paypal", "dispute_refund_amount_adjusted",
                      {"requested_cents": refund.amount_cents, "refunded_cents": refunded})
                refund.amount_cents = refunded
            refund.error = None
            refund.raw_response = {**(refund.raw_response or {}), "dispute_outcome": outcome}
            audit(self.db, claim.id, "paypal", "dispute_refund_confirmed", {
                "paypal_dispute_id": dispute.paypal_dispute_id, "outcome_code": outcome.get("outcome_code"),
                "cents": refund.amount_cents, "source": source})
            self.finalize(refund)
            dispute.status = "RESOLVED"
            dispute.resolved_at = dispute.resolved_at or now_iso()
            return True
        if refund.status == "FAILED":
            return False
        if status == "RESOLVED":                  # resolved without paying the buyer (seller favour, cancelled)
            refund.status, refund.error = "CANCELLED", f"dispute resolved: {outcome.get('outcome_code') or 'no refund'}"
            claim.status = "REVIEW_REQUIRED"
            dispute.status = "RESOLVED"
            dispute.resolved_at = dispute.resolved_at or now_iso()
            audit(self.db, claim.id, "paypal", "dispute_resolved_without_refund",
                  {"paypal_dispute_id": dispute.paypal_dispute_id, "outcome_code": outcome.get("outcome_code"),
                   "source": source})
            return True
        if refund.status == "UNKNOWN" and status in DISPUTE_ACTIONABLE_STATUSES:
            # A timed-out accept-claim may still be queued/processing upstream at PayPal.
            # Never auto-fail on a single status poll; keep UNKNOWN/PROCESSING so a late CUSTOMER.DISPUTE.RESOLVED
            # settles the ledger accurately. If the merchant confirms in PayPal that the request never landed,
            # they can explicitly reset the timed-out attempt via reset_dispute_accept().
            reconcile_needed.set()
            return False
        if refund.status == "UNKNOWN":            # PayPal moved on (e.g. buyer must answer a partial offer)
            refund.status = "PENDING"
            reconcile_needed.set()
            return True
        return False

    def reset_dispute_accept(self, dispute_id: str, reason: str = "verified not received in PayPal",
                             by: str = "merchant") -> Dispute:
        """Explicit, audited manual recovery when a timed-out accept-claim is confirmed not to have landed at PayPal."""
        if not settings.disputes_enabled:
            raise Conflict("PayPal Disputes integration is disabled (DISPUTES_ENABLED=false)")
        dispute = self.get_dispute(dispute_id)
        unknowns = list(self.db.execute(select(Refund).where(
            Refund.paypal_request_id.like(f"{DISPUTE_ACCEPT_PREFIX}{dispute.paypal_dispute_id}-%"),
            Refund.status == "UNKNOWN",
        ).order_by(Refund.id.desc())).scalars().all())
        if not unknowns:
            raise Conflict("no timed-out (UNKNOWN) accept-claim attempt to reset")

        live = self.paypal.get_dispute(dispute.paypal_dispute_id)
        dispute.raw = live
        live_status = live.get("status") or ""
        if live_status == "RESOLVED":
            self._settle_dispute_refund(unknowns[0], dispute, live, source="reset_check")
            self.db.commit()
            return dispute
        if live_status not in DISPUTE_ACTIONABLE_STATUSES:
            for r in unknowns:
                r.status = "PENDING"
            reconcile_needed.set()
            self.db.commit()
            raise Conflict(f"PayPal dispute is now {live_status}; cannot reset to open")

        for r in unknowns:
            r.status = "FAILED"
            r.error = f"reset by {by}: {reason.strip()[:300]}"
            claim = self.get_claim(r.claim_id)
            claim.status = "REVIEW_REQUIRED"
            audit(self.db, claim.id, by, "dispute_accept_reset", {
                "paypal_dispute_id": dispute.paypal_dispute_id,
                "request_id": r.paypal_request_id,
                "reason": reason.strip()[:300],
            })
        dispute.status = live_status
        self.db.commit()
        return dispute

    def submit_dispute_evidence(self, dispute_id: str, notes: str | None = None, by: str = "merchant") -> Dispute:
        """Submit structured evidence package to PayPal Disputes API via multipart/form-data."""
        if not settings.disputes_enabled:
            raise Conflict("PayPal Disputes integration is disabled (DISPUTES_ENABLED=false)")
        dispute = self.get_dispute(dispute_id)
        if dispute.status not in DISPUTE_ACTIONABLE_STATUSES:
            raise Conflict(f"dispute is {dispute.status}, cannot submit evidence")
        _, can_evidence, _ = self.dispute_capabilities(dispute.raw, status_override=dispute.status)
        if not can_evidence:
            raise Conflict("PayPal does not allow provide-evidence on this dispute (missing provide-evidence action link)")

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

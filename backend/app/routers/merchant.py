"""Merchant endpoints (bearer MERCHANT_TOKEN)."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..claims import ClaimService, Conflict, NotFound
from ..config import settings
from ..db import get_db
from ..deps import get_service, require_merchant
from ..models import AuditLog, Claim, Decision, Dispute, Refund
from ..schemas import (ApproveIn, AuditOut, ClaimOut, ClaimRow, DisputeAcceptIn, DisputeEvidenceIn,
                       DisputeEvidencePreviewOut, DisputeOut, DisputeRow, MessageIn, OrderOut, ReassessIn, RefundOut,
                       StatsOut)
from .claims import _run_assessment_job, claim_out

router = APIRouter(prefix="/merchant", tags=["merchant"], dependencies=[Depends(require_merchant)])


def _dispute_timeline(db: Session, d: Dispute) -> list[AuditOut]:
    rows = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
    out: list[AuditOut] = []
    for a in rows:
        if d.claim_id and a.claim_id == d.claim_id:
            out.append(AuditOut.from_model(a))
        elif a.detail and a.detail.get("paypal_dispute_id") == d.paypal_dispute_id:
            out.append(AuditOut.from_model(a))
    return out


def dispute_out(db: Session, svc: ClaimService, d: Dispute) -> DisputeOut:
    claim = d.claim or (svc.latest_claim_for_order(d.order_id) if d.order_id else None)
    pkg = svc.build_dispute_evidence(d)
    return DisputeOut(
        id=d.id,
        paypal_dispute_id=d.paypal_dispute_id,
        status=d.status,
        stage=d.stage,
        reason=d.reason,
        dispute_amount_cents=d.dispute_amount_cents,
        currency=d.currency,
        suggested_cents=svc.suggested_dispute_cents(d),
        order=OrderOut.from_model(d.order) if d.order else None,
        claim=claim_out(db, claim) if claim else None,
        evidence_preview=DisputeEvidencePreviewOut(**pkg),
        submitted_evidence_notes=d.submitted_evidence_notes,
        timeline=_dispute_timeline(db, d),
        raw=d.raw or {},
        created_at=d.created_at,
        updated_at=d.updated_at,
        resolved_at=d.resolved_at,
    )


@router.get("/claims", response_model=list[ClaimRow])
def list_claims(status: str | None = Query(default=None), outcome: str | None = None, damage_type: str | None = None,
                db: Session = Depends(get_db)) -> list[ClaimRow]:
    q = select(Claim).order_by(Claim.created_at.desc())
    if status:
        q = q.where(Claim.status.in_(status.split(",")))
    rows: list[ClaimRow] = []
    for c in db.execute(q).scalars().all():
        a = c.latest_assessment.output if c.latest_assessment else {}
        d = c.latest_decision
        if outcome and (d is None or d.outcome != outcome):
            continue
        if damage_type and a.get("damage_type") != damage_type:
            continue
        rows.append(ClaimRow(id=c.id, status=c.status, order_id=c.order_id, product_name=c.order.product.name,
                             product_image_url=c.order.product.image_url, amount_cents=c.order.amount_cents,
                             remaining_cents=c.order.remaining_cents, damage_type=a.get("damage_type"),
                             evidence_quality=a.get("evidence_quality"), product_match=a.get("product_match"),
                             outcome=d.outcome if d else None, rule_id=d.rule_id if d else None,
                             suggested_cents=d.suggested_cents if d else None, policy_version=c.policy_version,
                             created_at=c.created_at, updated_at=c.updated_at))
    return rows


@router.get("/claims/{claim_id}", response_model=ClaimOut)
def get_claim(claim_id: str, svc: ClaimService = Depends(get_service), db: Session = Depends(get_db)) -> ClaimOut:
    try:
        return claim_out(db, svc.get_claim(claim_id))
    except NotFound:
        raise HTTPException(404, "claim not found")


@router.post("/claims/{claim_id}/approve", response_model=RefundOut)
def approve(claim_id: str, body: ApproveIn | None = None, svc: ClaimService = Depends(get_service),
            who: str = Depends(require_merchant)) -> RefundOut:
    try:
        r = svc.approve(claim_id, by=who, override_cents=body.override_cents if body else None)
    except NotFound:
        raise HTTPException(404, "claim not found")
    except Conflict as e:
        raise HTTPException(409, str(e))
    return RefundOut.from_model(r)


@router.post("/claims/{claim_id}/request-evidence", response_model=ClaimOut)
def request_evidence(claim_id: str, body: MessageIn, svc: ClaimService = Depends(get_service),
                     db: Session = Depends(get_db)) -> ClaimOut:
    try:
        return claim_out(db, svc.request_evidence(claim_id, body.message))
    except NotFound:
        raise HTTPException(404, "claim not found")
    except Conflict as e:
        raise HTTPException(409, str(e))


@router.post("/claims/{claim_id}/reject", response_model=ClaimOut)
def reject(claim_id: str, body: MessageIn, svc: ClaimService = Depends(get_service),
           db: Session = Depends(get_db)) -> ClaimOut:
    try:
        return claim_out(db, svc.reject(claim_id, body.message))
    except NotFound:
        raise HTTPException(404, "claim not found")
    except Conflict as e:
        raise HTTPException(409, str(e))


@router.post("/claims/{claim_id}/reassess", response_model=ClaimOut)
def reassess(claim_id: str, body: ReassessIn, background: BackgroundTasks,
             svc: ClaimService = Depends(get_service), db: Session = Depends(get_db)) -> ClaimOut:
    """Re-run the rules on the existing assessment (optionally under another policy); rerun_ai=True re-runs the model."""
    try:
        if body.rerun_ai:
            claim = svc.get_claim(claim_id)
            if body.policy_version:
                svc.policy(body.policy_version)
                claim.policy_version = body.policy_version
                db.commit()
            background.add_task(_run_assessment_job, claim.id)
            return claim_out(db, claim)
        return claim_out(db, svc.redecide(claim_id, body.policy_version))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Conflict as e:
        raise HTTPException(409, str(e))


@router.get("/disputes", response_model=list[DisputeRow])
def list_disputes(status: str | None = Query(default=None), svc: ClaimService = Depends(get_service),
                  db: Session = Depends(get_db)) -> list[DisputeRow]:
    if not settings.disputes_enabled:
        return []
    q = select(Dispute).order_by(Dispute.created_at.desc())
    if status:
        q = q.where(Dispute.status.in_(status.split(",")))
    rows: list[DisputeRow] = []
    for d in db.execute(q).scalars().all():
        o = d.order
        c = d.claim or (svc.latest_claim_for_order(o.id) if o else None)
        dec = c.latest_decision if c else None
        rows.append(DisputeRow(
            id=d.id,
            paypal_dispute_id=d.paypal_dispute_id,
            status=d.status,
            stage=d.stage,
            reason=d.reason,
            dispute_amount_cents=d.dispute_amount_cents,
            currency=d.currency,
            order_id=o.id if o else None,
            capture_id=o.capture_id if o else None,
            product_name=o.product.name if o and o.product else None,
            product_image_url=o.product.image_url if o and o.product else None,
            order_amount_cents=o.amount_cents if o else None,
            remaining_cents=o.remaining_cents if o else None,
            claim_id=c.id if c else None,
            outcome=dec.outcome if dec else None,
            rule_id=dec.rule_id if dec else None,
            suggested_cents=svc.suggested_dispute_cents(d),
            created_at=d.created_at,
            updated_at=d.updated_at,
        ))
    return rows


@router.post("/disputes/sync")
def sync_disputes(svc: ClaimService = Depends(get_service)) -> dict:
    if not settings.disputes_enabled:
        raise HTTPException(409, "PayPal Disputes integration is disabled (DISPUTES_ENABLED=false)")
    return {"synced": svc.sync_disputes()}


@router.get("/disputes/{dispute_id}", response_model=DisputeOut)
def get_dispute(dispute_id: str, svc: ClaimService = Depends(get_service), db: Session = Depends(get_db)) -> DisputeOut:
    if not settings.disputes_enabled:
        raise HTTPException(404, "disputes disabled")
    try:
        d = svc.get_dispute(dispute_id)
    except NotFound:
        raise HTTPException(404, "dispute not found")
    return dispute_out(db, svc, d)


@router.post("/disputes/{dispute_id}/accept", response_model=RefundOut)
def accept_dispute(dispute_id: str, body: DisputeAcceptIn | None = None, svc: ClaimService = Depends(get_service),
                   who: str = Depends(require_merchant)) -> RefundOut:
    if not settings.disputes_enabled:
        raise HTTPException(409, "PayPal Disputes integration is disabled (DISPUTES_ENABLED=false)")
    try:
        r = svc.accept_dispute(
            dispute_id,
            by=who,
            override_cents=body.override_cents if body else None,
            note=body.note if body else None,
        )
    except NotFound:
        raise HTTPException(404, "dispute not found")
    except Conflict as e:
        raise HTTPException(409, str(e))
    return RefundOut.from_model(r)


@router.post("/disputes/{dispute_id}/evidence", response_model=DisputeOut)
def provide_dispute_evidence(dispute_id: str, body: DisputeEvidenceIn | None = None,
                             svc: ClaimService = Depends(get_service), db: Session = Depends(get_db),
                             who: str = Depends(require_merchant)) -> DisputeOut:
    if not settings.disputes_enabled:
        raise HTTPException(409, "PayPal Disputes integration is disabled (DISPUTES_ENABLED=false)")
    try:
        d = svc.submit_dispute_evidence(dispute_id, notes=body.notes if body else None, by=who)
    except NotFound:
        raise HTTPException(404, "dispute not found")
    except Conflict as e:
        raise HTTPException(409, str(e))
    return dispute_out(db, svc, d)


@router.post("/reconcile")
def reconcile(svc: ClaimService = Depends(get_service)) -> dict:
    touched = svc.reconcile()
    synced = svc.sync_disputes() if settings.disputes_enabled else 0
    return {"touched": touched, "disputes_synced": synced}


@router.get("/stats", response_model=StatsOut)
def stats(db: Session = Depends(get_db)) -> StatsOut:
    def count(*statuses: str) -> int:
        return db.execute(select(func.count()).select_from(Claim).where(Claim.status.in_(statuses))).scalar_one()
    pending_ids = [c.id for c in db.execute(select(Claim).where(Claim.status.in_(("REVIEW_REQUIRED", "AUTO_ELIGIBLE")))).scalars().all()]
    suggested = 0
    for cid in pending_ids:
        d = db.execute(select(Decision).where(Decision.claim_id == cid).order_by(Decision.id.desc())).scalars().first()
        suggested += d.suggested_cents if d else 0
    refunded = db.execute(select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(Refund.status == "COMPLETED")).scalar_one()
    return StatsOut(pending_review=count("REVIEW_REQUIRED", "AUTO_ELIGIBLE"), evidence_requested=count("EVIDENCE_REQUESTED"),
                    processing=count("PROCESSING", "ANALYZING"), completed=count("COMPLETED"),
                    suggested_cents_pending=suggested, refunded_cents_total=int(refunded))

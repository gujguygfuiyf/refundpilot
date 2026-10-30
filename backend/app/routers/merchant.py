"""Merchant endpoints (bearer MERCHANT_TOKEN)."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..claims import ClaimService, Conflict, NotFound
from ..db import get_db
from ..deps import get_service, require_merchant
from ..models import Claim, Decision, Refund
from ..schemas import ApproveIn, ClaimOut, ClaimRow, MessageIn, ReassessIn, RefundOut, StatsOut
from .claims import _run_assessment_job, claim_out

router = APIRouter(prefix="/merchant", tags=["merchant"], dependencies=[Depends(require_merchant)])


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


@router.post("/reconcile")
def reconcile(svc: ClaimService = Depends(get_service)) -> dict:
    return {"touched": svc.reconcile()}


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

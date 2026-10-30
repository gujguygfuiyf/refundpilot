from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import EvidenceAssessment
from ..db import get_db
from ..deps import require_merchant
from ..models import Policy
from ..policy import decide
from ..schemas import SimulateIn

router = APIRouter(prefix="/policies", tags=["policy"], dependencies=[Depends(require_merchant)])


@router.get("")
def list_policies(db: Session = Depends(get_db)) -> list[dict]:
    return [{"version": p.version, "name": p.name, "body": p.body, "sha256": p.body_sha256}
            for p in db.execute(select(Policy).order_by(Policy.version)).scalars().all()]


@router.get("/{version}")
def get_policy(version: str, db: Session = Depends(get_db)) -> dict:
    p = db.get(Policy, version)
    if not p:
        raise HTTPException(404, "policy not found")
    return {"version": p.version, "name": p.name, "body": p.body, "sha256": p.body_sha256}


@router.post("/simulate")
def simulate(body: SimulateIn, db: Session = Depends(get_db)) -> dict:
    """Pure computation: no claim is touched, no PayPal call."""
    p = db.get(Policy, body.policy_version)
    if not p:
        raise HTTPException(404, "policy not found")
    try:
        assessment = EvidenceAssessment.model_validate(body.assessment).model_dump()
    except Exception as e:
        raise HTTPException(422, f"invalid assessment: {e}")
    d = decide(p.body, assessment, order_cents=body.order_cents,
               remaining_cents=body.remaining_cents if body.remaining_cents is not None else body.order_cents,
               days_since_delivery=body.days_since_delivery, prior_claims=body.prior_claims)
    return {"policy_version": p.version, "simulation": True, **d.as_dict()}

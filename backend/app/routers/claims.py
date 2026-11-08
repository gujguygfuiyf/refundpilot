"""Buyer-facing claim endpoints (anonymous; ids are unguessable)."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..claims import BadInput, ClaimService, Conflict, NotFound
from ..config import settings
from ..db import SessionLocal, get_db
from ..deps import get_assessor, get_paypal, get_service
from ..models import AuditLog, Claim, Order
from ..ratelimit import limit_claim_submissions
from ..schemas import ClaimOut

router = APIRouter(prefix="/claims", tags=["claims"])

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


async def _read_photos(files: list[UploadFile]) -> list[bytes]:
    out = []
    limit = settings.max_upload_mb * 1024 * 1024
    for f in files[:3]:
        if (f.content_type or "") not in ALLOWED_MIME:
            raise HTTPException(415, f"unsupported image type {f.content_type}")
        data = await f.read()
        if len(data) > limit:
            raise HTTPException(413, f"photo larger than {settings.max_upload_mb} MB")
        out.append(data)
    return out


def _run_assessment_job(claim_id: str) -> None:
    """Background task: fresh session, same gateways."""
    db = SessionLocal()
    try:
        ClaimService(db, get_paypal(), get_assessor(), settings.default_policy_version).run_assessment(claim_id)
    except Exception:
        db.rollback()
        c = db.get(Claim, claim_id)
        if c and c.status == "ANALYZING":
            c.status = "ANALYZE_FAILED"
            db.commit()
    finally:
        db.close()


def claim_out(db: Session, claim: Claim) -> ClaimOut:
    timeline = db.execute(select(AuditLog).where(AuditLog.claim_id == claim.id).order_by(AuditLog.id)).scalars().all()
    return ClaimOut.from_model(claim, timeline)


@router.post("", response_model=ClaimOut, status_code=201, dependencies=[Depends(limit_claim_submissions)])
async def create_claim(background: BackgroundTasks, order_id: int = Form(...), buyer_text: str = Form(""),
                       photos: list[UploadFile] = File(...), svc: ClaimService = Depends(get_service),
                       db: Session = Depends(get_db)) -> ClaimOut:
    data = await _read_photos(photos)
    try:
        claim = svc.create_claim(order_id, buyer_text, data)
    except NotFound as e:
        raise HTTPException(404, str(e))
    except BadInput as e:
        raise HTTPException(400, str(e))
    except Conflict as e:
        raise HTTPException(409, str(e))
    background.add_task(_run_assessment_job, claim.id)
    return claim_out(db, claim)


@router.get("/{claim_id}", response_model=ClaimOut)
def get_claim(claim_id: str, svc: ClaimService = Depends(get_service), db: Session = Depends(get_db)) -> ClaimOut:
    try:
        return claim_out(db, svc.get_claim(claim_id))
    except NotFound:
        raise HTTPException(404, "claim not found")


@router.get("/{claim_id}/photos/{n}")
def get_photo(claim_id: str, n: int, svc: ClaimService = Depends(get_service)) -> Response:
    try:
        claim = svc.get_claim(claim_id)
    except NotFound:
        raise HTTPException(404, "claim not found")
    if n < 0 or n >= len(claim.photos):
        raise HTTPException(404, "photo not found")
    p = claim.photos[n]
    return Response(content=p.bytes, media_type=p.mime, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/{claim_id}/evidence", response_model=ClaimOut, dependencies=[Depends(limit_claim_submissions)])
async def add_evidence(claim_id: str, background: BackgroundTasks, buyer_text: str = Form(""),
                       photos: list[UploadFile] = File(...), svc: ClaimService = Depends(get_service),
                       db: Session = Depends(get_db)) -> ClaimOut:
    data = await _read_photos(photos)
    try:
        claim = svc.add_evidence(claim_id, data, buyer_text or None)
    except NotFound:
        raise HTTPException(404, "claim not found")
    except BadInput as e:
        raise HTTPException(400, str(e))
    except Conflict as e:
        raise HTTPException(409, str(e))
    background.add_task(_run_assessment_job, claim.id)
    return claim_out(db, claim)


@router.post("/{claim_id}/cancel", response_model=ClaimOut)
def cancel(claim_id: str, svc: ClaimService = Depends(get_service), db: Session = Depends(get_db)) -> ClaimOut:
    try:
        return claim_out(db, svc.cancel(claim_id))
    except NotFound:
        raise HTTPException(404, "claim not found")
    except Conflict as e:
        raise HTTPException(409, str(e))

"""Dev-only helpers (bearer DEV_TOKEN). Keep them off the public surface in README."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_paypal, require_dev
from ..seed import seed_all

router = APIRouter(prefix="/dev", tags=["dev"], dependencies=[Depends(require_dev)])


@router.post("/seed")
def seed(db: Session = Depends(get_db)) -> dict:
    return seed_all(db, paypal=get_paypal())

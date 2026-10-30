from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import OPEN_STATES, Claim, Order
from ..schemas import OrderOut

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderOut])
def list_orders(db: Session = Depends(get_db)) -> list[OrderOut]:
    orders = db.execute(select(Order).order_by(Order.id)).scalars().all()
    open_by_order = {c.order_id: c.id for c in
                     db.execute(select(Claim).where(Claim.status.in_(OPEN_STATES))).scalars().all()}
    return [OrderOut.from_model(o, open_by_order.get(o.id)) for o in orders]

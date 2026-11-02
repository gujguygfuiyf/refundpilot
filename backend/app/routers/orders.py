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
    open_by_order: dict[int, str] = {}
    last_by_order: dict[int, str] = {}
    for c in db.execute(select(Claim).where(Claim.status != "CANCELLED").order_by(Claim.created_at)).scalars().all():
        last_by_order[c.order_id] = c.id              # ascending, so the newest wins
        if c.status in OPEN_STATES:
            open_by_order[c.order_id] = c.id
    return [OrderOut.from_model(o, open_by_order.get(o.id), last_by_order.get(o.id)) for o in orders]

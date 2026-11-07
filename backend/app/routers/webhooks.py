"""PayPal webhooks: verify signature, dedupe by event id, finalize refunds and sync disputes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..claims import ClaimService
from ..config import settings
from ..db import get_db
from ..deps import get_paypal, get_service
from ..models import WebhookEvent, now_iso
from ..paypal import FakePayPal, PayPalGateway

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

DISPUTE_EVENTS = (
    "CUSTOMER.DISPUTE.CREATED",
    "CUSTOMER.DISPUTE.UPDATED",
    "CUSTOMER.DISPUTE.RESOLVED",
)


@router.post("/paypal")
async def paypal_webhook(request: Request, db: Session = Depends(get_db), svc: ClaimService = Depends(get_service),
                         paypal: PayPalGateway = Depends(get_paypal)) -> dict:
    event = await request.json()
    if not settings.paypal_webhook_id and not isinstance(paypal, FakePayPal):
        raise HTTPException(400, "PAYPAL_WEBHOOK_ID not configured")
    if not paypal.verify_webhook(dict(request.headers), event, settings.paypal_webhook_id):
        raise HTTPException(400, "webhook signature verification failed")
    ev_id = event.get("id")
    if not ev_id:
        raise HTTPException(400, "missing event id")
    if db.get(WebhookEvent, ev_id):
        return {"ok": True, "duplicate": True}
    row = WebhookEvent(id=ev_id, event_type=event.get("event_type", ""), raw=event)
    db.add(row)
    db.commit()

    handled = False
    if row.event_type == "PAYMENT.CAPTURE.REFUNDED":
        handled = svc.apply_webhook_refund(event.get("resource") or {})
    elif row.event_type in DISPUTE_EVENTS and settings.disputes_enabled:
        handled = svc.apply_webhook_dispute(event.get("resource") or {}, event_type=row.event_type)
    row.processed_at = now_iso()
    db.commit()
    return {"ok": True, "handled": handled}

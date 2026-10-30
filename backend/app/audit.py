from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditLog


def audit(db: Session, claim_id: str | None, actor: str, event: str, detail: dict | None = None) -> None:
    db.add(AuditLog(claim_id=claim_id, actor=actor, event=event, detail=detail))

"""Abuse limits for the anonymous claim endpoints. Every submission triggers one paid AI call, so a public demo
needs a ceiling. Two layers:

- per client IP, a sliding one-minute window (in memory; best effort, since the IP comes from the proxy header);
- a global cap on AI-triggering submissions per UTC day, counted in the database, so it survives restarts and
  can't be dodged by rotating IPs.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import AuditLog

AI_TRIGGER_EVENTS = ("claim_submitted", "evidence_added")


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float = 60.0, now: float | None = None) -> int | None:
        """Record a hit. Returns None if allowed, else the seconds to wait."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                return max(1, int(q[0] + window_s - now) + 1)
            q.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


per_ip = SlidingWindow()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def ai_submissions_today(db: Session) -> int:
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    return db.execute(select(func.count(AuditLog.id)).where(AuditLog.event.in_(AI_TRIGGER_EVENTS),
                                                            AuditLog.ts >= midnight)).scalar_one()


def limit_claim_submissions(request: Request, db: Session = Depends(get_db)) -> None:
    """FastAPI dependency for POST /claims and POST /claims/{id}/evidence."""
    if settings.claims_per_ip_per_minute > 0:
        wait = per_ip.hit(client_ip(request), settings.claims_per_ip_per_minute)
        if wait is not None:
            raise HTTPException(429, f"Too many submissions from your address. Try again in {wait} s.",
                                headers={"Retry-After": str(wait)})
    if settings.ai_calls_per_day > 0 and ai_submissions_today(db) >= settings.ai_calls_per_day:
        raise HTTPException(429, "The demo has reached today's limit of AI assessments. Please try again tomorrow (UTC).")

"""RefundPilot API. Run: uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .claims import ClaimService
from .config import settings
from .db import Base, SessionLocal, engine
from .deps import get_assessor, get_paypal
from .routers import claims, dev, merchant, orders, policy, webhooks
from .seed import seed_all

log = logging.getLogger("refundpilot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _reconcile_loop(stop: threading.Event) -> None:
    while not stop.wait(settings.reconcile_interval_s):
        db = SessionLocal()
        try:
            n = ClaimService(db, get_paypal(), get_assessor(), settings.default_policy_version).reconcile()
            if n:
                log.info("reconciled %d refund(s)", n)
        except Exception as e:  # never let the loop die
            log.warning("reconcile error: %s", e)
            db.rollback()
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        log.info("seed: %s", seed_all(db, paypal=get_paypal()))
    finally:
        db.close()
    stop = threading.Event()
    t = threading.Thread(target=_reconcile_loop, args=(stop,), name="reconcile", daemon=True)
    t.start()
    yield
    stop.set()


app = FastAPI(title="RefundPilot API", version="0.1.0",
              description="AI reads the evidence, policy decides the amount, a human approves, PayPal executes.",
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_methods=["*"], allow_headers=["*"])

for r in (orders.router, claims.router, merchant.router, policy.router, webhooks.router, dev.router):
    app.include_router(r)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "paypal": type(get_paypal()).__name__, "assessor": getattr(get_assessor(), "name", "?"),
            "time": time.time()}

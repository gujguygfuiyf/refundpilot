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
from .deps import WEAK_TOKENS, get_assessor, get_paypal
from .paypal import FakePayPal
from .routers import claims, dev, merchant, orders, policy, webhooks
from .seed import refresh_captures, refresh_demo_delivery, seed_all

log = logging.getLogger("refundpilot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _reconcile_loop(stop: threading.Event) -> None:
    while not stop.wait(settings.reconcile_interval_s):
        db = SessionLocal()
        try:
            n = ClaimService(db, get_paypal(), get_assessor(), settings.default_policy_version).reconcile()
            if n:
                log.info("reconciled %d refund(s)", n)
            refresh_demo_delivery(db)
        except Exception as e:  # never let the loop die
            log.warning("reconcile error: %s", e)
            db.rollback()
        finally:
            db.close()


def _refresh_captures_once() -> None:
    db = SessionLocal()
    try:
        n = refresh_captures(db, get_paypal())
        log.info("capture status refreshed in the background (%d newly refunded elsewhere)", n)
    except Exception as e:
        log.warning("capture refresh failed: %s", e)
        db.rollback()
    finally:
        db.close()


def check_secrets() -> None:
    """Refuse to start a deployed instance whose merchant token is empty or the documented default."""
    if settings.is_deployed() and settings.merchant_token in WEAK_TOKENS:
        raise RuntimeError("MERCHANT_TOKEN is empty or the default 'change-me'. Set a random value before deploying "
                           "(render.yaml generates one).")
    if settings.is_deployed() and settings.dev_token in WEAK_TOKENS:
        log.warning("DEV_TOKEN is empty or default: /dev routes are disabled on this deployment")


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_secrets()
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        log.info("seed: %s", seed_all(db, paypal=get_paypal(), refresh=False))
    finally:
        db.close()
    if not isinstance(get_paypal(), FakePayPal):   # one PayPal GET per order: keep it off the cold-start path
        threading.Thread(target=_refresh_captures_once, name="capture-refresh", daemon=True).start()
    stop = threading.Event()
    t = threading.Thread(target=_reconcile_loop, args=(stop,), name="reconcile", daemon=True)
    t.start()
    yield
    stop.set()


app = FastAPI(title="RefundPilot API", version="0.1.0",
              description="AI reads the evidence, policy decides the amount, a human approves, PayPal executes.",
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_origin_regex=settings.cors_origin_regex or None, allow_methods=["*"], allow_headers=["*"])

for r in (orders.router, claims.router, merchant.router, policy.router, webhooks.router, dev.router):
    app.include_router(r)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "paypal": type(get_paypal()).__name__, "assessor": getattr(get_assessor(), "name", "?"),
            "time": time.time()}

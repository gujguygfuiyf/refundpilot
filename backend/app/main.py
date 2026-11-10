"""RefundPilot API. Run: uvicorn app.main:app --reload"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .claims import ClaimService, reconcile_needed
from .config import settings
from .db import Base, SessionLocal, engine
from .deps import WEAK_TOKENS, get_assessor, get_paypal
from .paypal import FakePayPal
from .routers import assistant, claims, dev, merchant, orders, policy, webhooks
from .seed import refresh_captures, refresh_demo_delivery, seed_all

log = logging.getLogger("refundpilot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class BackgroundWork:
    """The API's background loop. It wakes every `reconcile_interval_s` but opens a database session only when a
    refund is waiting on PayPal (`reconcile_needed`) or the slow housekeeping tick is due, so the database can
    scale to zero while the API idles (and an uptime ping to /health never touches it)."""

    def __init__(self, session_factory=None, paypal=None) -> None:
        self.session_factory = session_factory or SessionLocal
        self.paypal = paypal
        self.last_housekeeping = time.monotonic()

    def tick(self, now: float | None = None) -> bool:
        """One pass. Returns True if it touched the database."""
        now = time.monotonic() if now is None else now
        housekeeping = (now - self.last_housekeeping) >= (settings.housekeeping_interval_s - 1e-6)
        if not (reconcile_needed.is_set() or housekeeping):
            return False
        try:
            db = self.session_factory()
        except Exception as e:   # e.g. the database is unreachable: retry next tick
            log.warning("background work: no database session: %s", e)
            return False
        try:
            svc = ClaimService(db, self.paypal or get_paypal(), get_assessor(), settings.default_policy_version)
            reconcile_needed.clear()          # cleared first: a refund that goes pending meanwhile sets it again
            n = svc.reconcile()
            if n:
                log.info("reconciled %d refund(s)", n)
            if svc.pending_refunds():
                reconcile_needed.set()
            if housekeeping:
                refresh_demo_delivery(db)
                self.last_housekeeping = now
                if settings.disputes_enabled:
                    try:
                        d_synced = svc.sync_disputes()
                        if d_synced:
                            log.info("synced %d dispute(s)", d_synced)
                    except Exception as e:
                        log.warning("dispute sync error: %s", e)
                        db.rollback()
        except Exception as e:  # never let the loop die; try again next tick
            log.warning("background work error: %s", e)
            db.rollback()
            reconcile_needed.set()
        finally:
            db.close()
        return True


def _background_loop(stop: threading.Event) -> None:
    work = BackgroundWork()
    while not stop.wait(settings.reconcile_interval_s):
        work.tick()


def _refresh_captures_once() -> None:
    db = SessionLocal()
    try:
        from .seed import reconcile_orders_and_ledger
        calib = reconcile_orders_and_ledger(db, get_paypal())
        log.info("orders and ledger calibrated in the background: %s", calib)
        if settings.disputes_enabled:
            svc = ClaimService(db, get_paypal(), get_assessor(), settings.default_policy_version)
            d_synced = svc.sync_disputes()
            if d_synced:
                log.info("synced %d dispute(s) on startup", d_synced)
    except Exception as e:
        log.warning("capture/dispute refresh failed: %s", e)
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
    reconcile_needed.set()                         # pick up refunds left pending before a restart
    t = threading.Thread(target=_background_loop, args=(stop,), name="background", daemon=True)
    t.start()
    yield
    stop.set()


app = FastAPI(title="RefundPilot API", version="0.1.0",
              description="AI reads the evidence, policy decides the amount, a human approves, PayPal executes.",
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_origin_regex=settings.cors_origin_regex or None, allow_methods=["*"], allow_headers=["*"])

for r in (orders.router, claims.router, merchant.router, assistant.router, policy.router, webhooks.router, dev.router):
    app.include_router(r)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "paypal": type(get_paypal()).__name__, "assessor": getattr(get_assessor(), "name", "?"),
            "disputes_enabled": settings.disputes_enabled, "time": time.time()}

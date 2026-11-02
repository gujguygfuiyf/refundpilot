from datetime import datetime, timedelta, timezone

from app.claims import ClaimService
from app.models import Order, Product
from app.paypal import FakePayPal
from app.seed import seed_demo_orders
from tests.conftest import jpeg_bytes


def test_demo_orders_seeded_and_approvable_offline(db, assessor):
    pp = FakePayPal()
    n = seed_demo_orders(db, pp)
    assert n == 1 and "DEMO-CAP-MUG-01" in pp.captures          # conftest has one product
    assert seed_demo_orders(db, pp) == 0                          # idempotent
    demo = db.query(Order).filter_by(capture_id="DEMO-CAP-MUG-01").one()
    svc = ClaimService(db, pp, assessor, "standard-1.1")
    c = svc.create_claim(demo.id, "handle broke", [jpeg_bytes()])
    svc.run_assessment(c.id)
    r = svc.approve(c.id)
    assert r.status == "COMPLETED" and svc.get_claim(c.id).order.remaining_cents == 0


def test_unknown_capture_fails_cleanly(db, assessor):
    pp = FakePayPal()                                             # CAP1 not registered
    svc = ClaimService(db, pp, assessor, "standard-1.1")
    c = svc.create_claim(1, "handle broke", [jpeg_bytes()])
    svc.run_assessment(c.id)
    r = svc.approve(c.id)
    assert r.status == "FAILED" and "404" in r.error
    assert svc.get_claim(c.id).status == "REVIEW_REQUIRED"


def test_first_boot_single_seed_then_approve(db, assessor):
    """Regression: on a brand-new database the demo order wasn't flushed yet when its capture was registered in
    the fake gateway, so `refunded` was None and the first offline approve raised a TypeError (HTTP 500)."""
    pp = FakePayPal()
    assert seed_demo_orders(db, pp) == 1                          # exactly one seed, like a fresh `uvicorn` start
    assert pp.captures["DEMO-CAP-MUG-01"]["refunded"] == 0
    demo = db.query(Order).filter_by(capture_id="DEMO-CAP-MUG-01").one()
    svc = ClaimService(db, pp, assessor, "standard-1.1")
    c = svc.create_claim(demo.id, "handle broke", [jpeg_bytes()])
    svc.run_assessment(c.id)
    assert svc.approve(c.id).status == "COMPLETED"
